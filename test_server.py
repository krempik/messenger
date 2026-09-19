import os
import time
import uuid
import tempfile
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

# Isolated DB: must be set before server.main is imported (SessionLocal binds at import).
os.environ["MESSENGER_DB"] = os.path.join(tempfile.gettempdir(), f"messenger_test_{os.getpid()}.db")
if os.path.exists(os.environ["MESSENGER_DB"]):
    os.remove(os.environ["MESSENGER_DB"])

from server import main
from server.main import app

TEST_PEM = "-----BEGIN PUBLIC KEY-----\n" + "A" * 300 + "\n-----END PUBLIC KEY-----\n"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, tag: str) -> str:
    user = f"u_{tag}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    r = client.post("/api/register", json={
        "username": user,
        "display_name": user,
        "password": "pass1234",
        "public_key": TEST_PEM,
    })
    assert r.status_code == 200, r.text
    return user


def _auth(client: TestClient, user: str) -> dict:
    r = client.post("/api/login", json={"username": user, "password": "pass1234"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _user_id(client: TestClient, headers: dict) -> int:
    return client.get("/api/me", headers=headers).json()["id"]


def _dm_chat(client: TestClient, headers: dict, partner_id: int) -> int:
    r = client.post("/api/chats", json={"name": "", "member_ids": [partner_id]}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _ws_send(client: TestClient, token: str, chat_id: int, content: str):
    with client.websocket_connect(f"ws://localhost/ws?token={token}") as ws:
        ws.send_json({"type": "message", "chat_id": chat_id, "content": content})
        for _ in range(8):
            evt = ws.receive_json()
            if evt.get("type") == "message":
                return evt["message"]
            if evt.get("type") == "error":
                return evt
    return None


def _set_admin(client: TestClient, user: str) -> dict:
    headers = _auth(client, user)
    uid = _user_id(client, headers)
    db = main.SessionLocal()
    try:
        u = db.query(main.User).filter(main.User.id == uid).first()
        u.is_admin = True
        db.commit()
    finally:
        db.close()
    return headers


# ---------- smoke ----------

def test_root(client):
    assert client.get("/").status_code in (200, 404)


def test_docs(client):
    assert client.get("/docs").status_code == 200


def test_register_and_login(client):
    user = _register(client, "reg")
    assert _auth(client, user)["Authorization"]
    assert client.post("/api/login", json={"username": user, "password": "wrong"}).status_code == 401


def test_refresh_token_rejected_as_access(client):
    user = _register(client, "refr")
    r = client.post("/api/login", json={"username": user, "password": "pass1234"})
    refresh = r.json()["refresh_token"]
    r2 = client.get("/api/me", headers={"Authorization": f"Bearer {refresh}"})
    assert r2.status_code == 401


def test_bad_public_key_rejected(client):
    r = client.post("/api/register", json={
        "username": f"badkey_{uuid.uuid4().hex[:8]}",
        "display_name": "Bad Key",
        "password": "pass1234",
        "public_key": "not-a-pem-just-text",
    })
    assert r.status_code in (400, 422)


def test_chats_need_auth(client):
    assert client.get("/api/chats").status_code in (401, 403)
    assert client.post("/api/chats", json={"name": "test"}).status_code in (401, 403)


def test_user_list_need_auth(client):
    assert client.get("/api/users").status_code == 401
    assert client.get("/api/users/1").status_code == 401
    assert client.get("/api/users/1/public-key").status_code == 401


def test_create_chat_and_list(client):
    user = _register(client, "chat")
    h = _auth(client, user)
    r = client.post("/api/chats", json={"name": "room", "member_ids": []}, headers=h)
    assert r.status_code == 200, r.text
    chat_id = r.json()["id"]
    r = client.get("/api/chats", headers=h)
    assert r.status_code == 200
    assert any(c["id"] == chat_id for c in r.json())


# ---------- upload + link preview security ----------

def test_upload_html_blocked(client):
    user = _register(client, "up")
    h = _auth(client, user)
    r = client.post("/api/upload", headers=h,
                    files={"file": ("poc.html", b"<script>alert(1)</script>", "text/html")})
    assert r.status_code == 400


def test_upload_masked_svg_blocked(client):
    user = _register(client, "up2")
    h = _auth(client, user)
    r = client.post("/api/upload", headers=h,
                    files={"file": ("evil.png", b"<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>", "image/png")})
    assert r.status_code == 400


def test_link_preview_blocks_loopback(client):
    user = _register(client, "ssrf")
    h = _auth(client, user)
    r = client.get("/api/link-preview", params={"url": "http://127.0.0.1:1/"}, headers=h)
    assert r.status_code == 400


def test_link_preview_blocks_private_ip(client):
    user = _register(client, "ssrf2")
    h = _auth(client, user)
    r = client.get("/api/link-preview", params={"url": "http://192.168.0.1/"}, headers=h)
    assert r.status_code == 400


def test_register_rejects_empty_display_name(client):
    r = client.post("/api/register", json={
        "username": f"nodisp_{uuid.uuid4().hex[:8]}",
        "display_name": "",
        "password": "pass1234",
        "public_key": TEST_PEM,
    })
    assert r.status_code in (400, 422)


def test_reaction_requires_membership(client):
    user_a = _register(client, "rbA")
    user_b = _register(client, "rbB")
    h_a = _auth(client, user_a)
    h_b = _auth(client, user_b)
    r = client.post("/api/chats", json={"name": "rchat", "member_ids": []}, headers=h_a)
    chat_id = r.json()["id"]
    msg = None
    ws_url = f"ws://localhost/ws?token={h_a['Authorization'].split()[1]}"
    with client.websocket_connect(ws_url) as ws:
        ws.send_json({"type": "message", "chat_id": chat_id, "content": "hi"})
        for _ in range(8):
            evt = ws.receive_json()
            if evt.get("type") == "message":
                msg = evt["message"]["id"]
                break
    # B is not a member — must get 403
    assert msg is not None
    r = client.post(f"/api/messages/{msg}/reactions", json={"emoji": "❤️"}, headers=h_b)
    assert r.status_code == 403


# ---------- versions ----------

def test_version_consistency(client):
    with open(os.path.join(os.path.dirname(__file__), "VERSION"), encoding="utf-8") as f:
        expected = f.read().strip()
    assert client.get("/api/version").json()["version"] == expected
    html = client.get("/").text
    assert f"v{expected}" in html
    assert "__VERSION__" not in html


# ---------- blocking (server-side enforcement) ----------

def test_block_sender_rejected(client):
    user_a = _register(client, "blkA")
    user_b = _register(client, "blkB")
    h_a = _auth(client, user_a)
    h_b = _auth(client, user_b)
    b_id = _user_id(client, h_b)
    chat_id = _dm_chat(client, h_a, b_id)
    token = h_b["Authorization"].split()[1]

    # Normal send works.
    sent = _ws_send(client, token, chat_id, "hello before block")
    assert sent is not None and sent.get("content") == "hello before block"

    # A blocks B → B's next send is rejected server-side.
    assert client.post(f"/api/users/{b_id}/block", headers=h_a).status_code == 200
    evt = _ws_send(client, token, chat_id, "blocked attempt")
    assert evt is not None and evt.get("type") == "error" and evt.get("detail") == "Blocked by recipient"


def test_block_hides_messages(client):
    user_a = _register(client, "hidA")
    user_b = _register(client, "hidB")
    h_a = _auth(client, user_a)
    h_b = _auth(client, user_b)
    b_id = _user_id(client, h_b)
    chat_id = _dm_chat(client, h_a, b_id)

    _ws_send(client, h_b["Authorization"].split()[1], chat_id, "from B")
    _ws_send(client, h_a["Authorization"].split()[1], chat_id, "from A")
    assert len(client.get(f"/api/chats/{chat_id}/messages", headers=h_a).json()) == 2

    client.post(f"/api/users/{b_id}/block", headers=h_a)
    # Blocker never sees the blocked party; the blocked party never sees the blocker.
    a_list = client.get(f"/api/chats/{chat_id}/messages", headers=h_a).json()
    assert [m["content"] for m in a_list] == ["from A"]
    b_list = client.get(f"/api/chats/{chat_id}/messages", headers=h_b).json()
    assert [m["content"] for m in b_list] == ["from B"]

    client.delete(f"/api/users/{b_id}/block", headers=h_a)
    assert len(client.get(f"/api/chats/{chat_id}/messages", headers=h_a).json()) == 2


# ---------- shadow ban ----------

def test_shadow_ban_hides_content(client):
    admin = _register(client, "admA")
    h_admin = _set_admin(client, admin)
    user_b = _register(client, "shdB")
    h_b = _auth(client, user_b)
    b_id = _user_id(client, h_b)
    chat_id = _dm_chat(client, h_admin, b_id)

    msg = _ws_send(client, h_b["Authorization"].split()[1], chat_id, "secret text")
    assert msg is not None

    r = client.post(f"/api/admin/users/{b_id}/shadow-ban", headers=h_admin)
    assert r.status_code in (200, 403), r.text
    if r.status_code == 200:
        msgs = client.get(f"/api/chats/{chat_id}/messages", headers=h_admin).json()
        assert msgs and msgs[0]["content"] == "[скрыто]"
        assert msgs[0]["encrypted_key"] is None


# ---------- disappearing messages ----------

def test_disappearing_message_purged(client):
    user_a = _register(client, "disA")
    user_b = _register(client, "disB")
    h_a = _auth(client, user_a)
    h_b = _auth(client, user_b)
    b_id = _user_id(client, h_b)
    chat_id = _dm_chat(client, h_a, b_id)

    r = client.put(f"/api/chats/{chat_id}/settings", json={"disappearing_timer": 5}, headers=h_a)
    assert r.status_code == 200, r.text

    msg = _ws_send(client, h_b["Authorization"].split()[1], chat_id, "burn after reading")
    assert msg is not None
    msg_id = int(msg["id"])
    db = main.SessionLocal()
    try:
        row = db.query(main.Message).filter(main.Message.id == msg_id).first()
        assert row is not None and row.expires_at is not None  # created with a deadline
    finally:
        db.close()

    # Pin it, then age it past expiry and purge.
    client.put(f"/api/chats/{chat_id}/settings", json={"pinned_message_id": int(msg_id)}, headers=h_a)
    db = main.SessionLocal()
    try:
        row = db.query(main.Message).filter(main.Message.id == int(msg_id)).first()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        main._purge_expired_messages(db)
    finally:
        db.close()

    assert client.get(f"/api/chats/{chat_id}/messages", headers=h_a).json() == []
    db = main.SessionLocal()
    try:
        chat = db.query(main.Chat).filter(main.Chat.id == chat_id).first()
        assert chat.pinned_message_id is None
    finally:
        db.close()