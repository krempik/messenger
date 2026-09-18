import time
import uuid

import pytest
from fastapi.testclient import TestClient

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
        for _ in range(5):
            evt = ws.receive_json()
            if evt.get("type") == "message":
                msg = evt["message"]["id"]
                break
    # B is not a member — must get 403
    assert msg is not None
    r = client.post(f"/api/messages/{msg}/reactions", json={"emoji": "❤️"}, headers=h_b)
    assert r.status_code == 403