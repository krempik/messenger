import os
import re
import json
import uuid
import subprocess
import threading
import time
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel

from .database import get_db, User, Chat, ChatMember, Message, SessionLocal
from .auth import hash_password, verify_password, create_access_token, authenticate_ws_token, get_current_user
from .websocket_manager import manager

TUNNEL_URL: Optional[str] = None
_tunnel_process: Optional[subprocess.Popen] = None
_tunnel_config: Optional[dict] = None
HOST_START_TIME = time.time()

MAX_AVATAR_SIZE = 5 * 1024 * 1024
MAX_FILE_SIZE = 100 * 1024 * 1024
ALLOWED_AVATAR_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
SAFE_UPLOAD_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
    ".pdf", ".doc", ".docx", ".txt", ".rtf",
    ".mp3", ".wav", ".ogg", ".flac", ".m4a",
    ".mp4", ".avi", ".mkv", ".mov", ".webm",
    ".zip", ".rar", ".7z", ".tar", ".gz",
    ".py", ".js", ".ts", ".html", ".css", ".json", ".xml",
    ".exe", ".msi", ".apk", ".ipa",
}
UNSAFE_CONTENT_TYPES = {".html", ".htm", ".svg", ".xhtml", ".php", ".jsp", ".js", ".css"}


def _find_cloudflared() -> Optional[str]:
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cloudflared.exe")
    if os.path.isfile(path):
        return path
    for p in ["cloudflared.exe", "cloudflared"]:
        for d in os.environ.get("PATH", "").split(os.pathsep):
            full = os.path.join(d, p)
            if os.path.isfile(full):
                return full
    for pf in [
        os.path.expandvars(r"%ProgramFiles%\cloudflared\cloudflared.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\cloudflared\cloudflared.exe"),
    ]:
        if os.path.isfile(pf):
            return pf
    return None


def _load_tunnel_config() -> Optional[dict]:
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tunnel.json")
    if os.path.isfile(config_path):
        try:
            with open(config_path) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _start_tunnel():
    global _tunnel_process, TUNNEL_URL, _tunnel_config
    cf = _find_cloudflared()
    if not cf:
        print("[!] cloudflared not found — tunnel not started")
        return

    _tunnel_config = _load_tunnel_config()
    named_tunnel = _tunnel_config and _tunnel_config.get("tunnel_id")

    try:
        if named_tunnel:
            tunnel_name = _tunnel_config.get("tunnel_name", "h4ck")
            domain = _tunnel_config.get("domain", "")
            cmd = [cf, "tunnel", "--no-autoupdate", "run", tunnel_name]
            print(f"[*] Starting NAMED tunnel '{tunnel_name}'...")
        else:
            cmd = [cf, "tunnel", "--url", "http://localhost:8000"]
            print("[*] Starting quick tunnel...")

        _tunnel_process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        def _reader():
            global TUNNEL_URL
            url_pattern = re.compile(r"https://[a-z0-9\-]+\.trycloudflare\.com")
            for line in iter(_tunnel_process.stdout.readline, b""):
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    print(f"[tunnel] {text}")
                if named_tunnel and _tunnel_config.get("domain"):
                    TUNNEL_URL = f"https://{_tunnel_config['domain']}"
                    if "Connection registered" in text or "INF" in text:
                        print(f"\n{'='*50}")
                        print(f"  PERMANENT URL: {TUNNEL_URL}")
                        print(f"{'='*50}\n")
                else:
                    m = url_pattern.search(text)
                    if m:
                        TUNNEL_URL = m.group(0)
                        print(f"\n{'='*50}")
                        print(f"  PUBLIC URL: {TUNNEL_URL}")
                        print(f"{'='*50}\n")

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
    except Exception as e:
        print(f"[!] Failed to start cloudflared: {e}")


def _stop_tunnel():
    global _tunnel_process
    if _tunnel_process:
        try:
            _tunnel_process.terminate()
            _tunnel_process.wait(timeout=3)
        except Exception:
            try:
                _tunnel_process.kill()
            except Exception:
                pass
        _tunnel_process = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    _start_tunnel()
    print("[*] H4ck Messenger started on http://localhost:8000")
    yield
    _stop_tunnel()


app = FastAPI(title="H4ck Messenger", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

CLIENT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "client")


class RegisterRequest(BaseModel):
    username: str
    display_name: str
    password: str
    public_key: str


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateChatRequest(BaseModel):
    name: Optional[str] = None
    member_ids: list[int] = []


class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = None
    password: Optional[str] = None


def user_dict(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name,
        "public_key": u.public_key,
        "avatar_url": u.avatar_url,
        "online": manager.is_online(u.id),
    }


USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")


@app.post("/api/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if not USERNAME_RE.match(req.username):
        raise HTTPException(400, "Username: 3-32 chars, only a-z, 0-9, _")
    if len(req.password) < 6:
        raise HTTPException(400, "Password min 6 characters")
    if len(req.display_name) > 128:
        raise HTTPException(400, "Display name too long")
    if len(req.public_key) < 100:
        raise HTTPException(400, "Invalid public key")

    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(409, "Username already taken")

    user = User(
        username=req.username,
        display_name=req.display_name,
        password_hash=hash_password(req.password),
        public_key=req.public_key,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id)
    return {"token": token, "user": user_dict(user)}


@app.post("/api/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")
    token = create_access_token(user.id)
    return {"token": token, "user": user_dict(user)}


@app.get("/api/me")
def get_me(user: User = Depends(get_current_user)):
    return user_dict(user)


@app.put("/api/me")
def update_me(req: UpdateProfileRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if req.display_name:
        if len(req.display_name) > 128:
            raise HTTPException(400, "Display name too long")
        user.display_name = req.display_name
    if req.password:
        if len(req.password) < 6:
            raise HTTPException(400, "Password min 6 characters")
        user.password_hash = hash_password(req.password)
    db.commit()
    db.refresh(user)

    asyncio_thread_safe_broadcast({
        "type": "profile_update",
        "user_id": user.id,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
    })

    return user_dict(user)


def asyncio_thread_safe_broadcast(data: dict):
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(manager.broadcast(data))
        else:
            loop.run_until_complete(manager.broadcast(data))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(manager.broadcast(data))


@app.post("/api/me/avatar")
async def upload_avatar(file: UploadFile = File(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_AVATAR_EXTS:
        raise HTTPException(400, "Only jpg, png, gif, webp allowed")
    content = await file.read()
    if len(content) > MAX_AVATAR_SIZE:
        raise HTTPException(400, f"Max avatar size: {MAX_AVATAR_SIZE // 1024 // 1024}MB")
    unique_name = f"avatar_{user.id}_{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_name)
    with open(file_path, "wb") as f:
        f.write(content)
    if user.avatar_url and "avatar_" in (user.avatar_url or ""):
        old_path = os.path.join(UPLOAD_DIR, user.avatar_url.split("/")[-1])
        if os.path.isfile(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass
    user.avatar_url = f"/uploads/{unique_name}"
    db.commit()
    db.refresh(user)
    return user_dict(user)


@app.get("/api/users")
def list_users(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    users = db.query(User).filter(User.id != user.id).all()
    return [
        {
            **user_dict(u),
            "online": manager.is_online(u.id),
        }
        for u in users
    ]


@app.get("/api/users/{user_id}/public-key")
def get_public_key(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    return {"public_key": user.public_key, "user_id": user.id}


@app.post("/api/chats")
def create_chat(req: CreateChatRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member_ids = [mid for mid in req.member_ids if mid != user.id]

    if len(member_ids) == 1:
        existing = (
            db.query(Chat)
            .join(ChatMember, ChatMember.chat_id == Chat.id)
            .filter(Chat.is_group == False, ChatMember.user_id.in_([user.id, member_ids[0]]))
            .group_by(Chat.id)
            .having(func.count(ChatMember.id) == 2)
            .first()
        )
        if existing:
            return {
                "id": existing.id,
                "name": existing.name or "Chat",
                "is_group": False,
            }

    is_group = len(member_ids) > 1
    name = req.name if is_group else None
    chat = Chat(name=name, is_group=is_group)
    db.add(chat)
    db.flush()
    db.add(ChatMember(chat_id=chat.id, user_id=user.id, role="owner"))
    for mid in member_ids:
        db.add(ChatMember(chat_id=chat.id, user_id=mid))
    db.commit()
    db.refresh(chat)

    return {"id": chat.id, "name": name or "Chat", "is_group": is_group}


@app.get("/api/chats")
def list_chats(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    memberships = db.query(ChatMember).filter(ChatMember.user_id == user.id).all()
    chat_ids = [m.chat_id for m in memberships]

    if not chat_ids:
        return []

    chats = db.query(Chat).filter(Chat.id.in_(chat_ids)).all()
    chat_map = {c.id: c for c in chats}

    online_ids = set(manager.get_online_user_ids())

    unread_sub = (
        db.query(Message.chat_id, func.count(Message.id).label("cnt"))
        .filter(Message.sender_id != user.id)
        .group_by(Message.chat_id)
        .subquery()
    )
    unread_rows = db.query(unread_sub).all()
    unread_map = {r.chat_id: r.cnt for r in unread_rows}

    result = []
    for cid in chat_ids:
        chat = chat_map.get(cid)
        if not chat:
            continue

        members = (
            db.query(ChatMember, User)
            .join(User, User.id == ChatMember.user_id)
            .filter(ChatMember.chat_id == cid)
            .all()
        )

        display_name = chat.name or ""
        other_user = None

        if not chat.is_group:
            for _, u in members:
                if u.id != user.id:
                    display_name = u.display_name
                    other_user = user_dict(u)
                    other_user["online"] = u.id in online_ids
                    break
        else:
            display_name = chat.name or ", ".join(u.display_name for _, u in members[:4])

        last_msg = (
            db.query(Message)
            .filter(Message.chat_id == cid)
            .order_by(Message.created_at.desc())
            .first()
        )

        result.append({
            "id": chat.id,
            "name": display_name,
            "is_group": chat.is_group,
            "other_user": other_user,
            "members": [
                {**user_dict(u), "online": u.id in online_ids, "role": m.role}
                for m, u in members
            ],
            "unread": unread_map.get(cid, 0),
            "last_message": {
                "id": last_msg.id,
                "content": last_msg.content,
                "sender_id": last_msg.sender_id,
                "sender_name": last_msg.sender.display_name,
                "created_at": last_msg.created_at.isoformat(),
                "message_type": last_msg.message_type,
                "encrypted_key": last_msg.encrypted_key,
                "sender_encrypted_key": last_msg.sender_encrypted_key,
                "file_name": last_msg.file_name,
            } if last_msg else None,
        })

    result.sort(
        key=lambda c: c["last_message"]["created_at"] if c["last_message"] else "",
        reverse=True,
    )
    return result


@app.get("/api/chats/{chat_id}/messages")
def get_messages(chat_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = db.query(ChatMember).filter(
        ChatMember.chat_id == chat_id, ChatMember.user_id == user.id
    ).first()
    if not member:
        raise HTTPException(403, "Not a member")

    messages = (
        db.query(Message)
        .filter(Message.chat_id == chat_id)
        .order_by(Message.created_at.desc())
        .limit(200)
        .all()
    )
    messages.reverse()

    return [
        {
            "id": m.id,
            "chat_id": m.chat_id,
            "sender_id": m.sender_id,
            "sender_name": m.sender.display_name,
            "content": m.content,
            "encrypted_key": m.encrypted_key,
            "sender_encrypted_key": m.sender_encrypted_key,
            "message_type": m.message_type,
            "file_url": m.file_url,
            "file_name": m.file_name,
            "created_at": m.created_at.isoformat(),
        }
        for m in messages
    ]


@app.get("/api/chats/{chat_id}/search")
def search_messages(chat_id: int, q: str = Query(""), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = db.query(ChatMember).filter(
        ChatMember.chat_id == chat_id, ChatMember.user_id == user.id
    ).first()
    if not member:
        raise HTTPException(403, "Not a member")
    if not q:
        return []

    messages = (
        db.query(Message)
        .filter(Message.chat_id == chat_id, Message.content.contains(q))
        .order_by(Message.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": m.id,
            "sender_name": m.sender.display_name,
            "content": m.content,
            "created_at": m.created_at.isoformat(),
            "message_type": m.message_type,
        }
        for m in messages
    ]


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = db.query(ChatMember).filter(
        ChatMember.chat_id == chat_id, ChatMember.user_id == user.id
    ).first()
    if not member:
        raise HTTPException(403, "Not a member")

    members = db.query(ChatMember).filter(ChatMember.chat_id == chat_id).all()
    member_ids = [m.user_id for m in members]

    db.query(Message).filter(Message.chat_id == chat_id).delete()
    db.query(ChatMember).filter(ChatMember.chat_id == chat_id).delete()
    db.query(Chat).filter(Chat.id == chat_id).delete()
    db.commit()

    return {"ok": True, "deleted_for": member_ids}


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in SAFE_UPLOAD_EXTS:
        ext = ".bin"
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, f"Max file size: {MAX_FILE_SIZE // 1024 // 1024}MB")
    unique_name = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_name)
    with open(file_path, "wb") as f:
        f.write(content)
    return {"url": f"/uploads/{unique_name}", "name": file.filename}


app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)):
    db = SessionLocal()
    try:
        user = authenticate_ws_token(token, db)
        if not user:
            await websocket.close(code=4001)
            return

        await manager.connect(websocket, user.id)
        user.last_seen = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        db.commit()

        await manager.broadcast({
            "type": "presence",
            "user_id": user.id,
            "online": True,
        })

        try:
            while True:
                raw = await websocket.receive_text()
                data = json.loads(raw)
                msg_type = data.get("type")

                if msg_type == "message":
                    chat_id = data["chat_id"]
                    member = db.query(ChatMember).filter(
                        ChatMember.chat_id == chat_id, ChatMember.user_id == user.id
                    ).first()
                    if not member:
                        await websocket.send_text(json.dumps({"type": "error", "detail": "Not a member"}))
                        continue

                    msg = Message(
                        chat_id=chat_id,
                        sender_id=user.id,
                        content=data["content"],
                        encrypted_key=data.get("encrypted_key"),
                        sender_encrypted_key=data.get("sender_encrypted_key"),
                        message_type=data.get("message_type", "text"),
                        file_url=data.get("file_url"),
                        file_name=data.get("file_name"),
                    )
                    db.add(msg)
                    db.commit()
                    db.refresh(msg)

                    members = db.query(ChatMember).filter(ChatMember.chat_id == chat_id).all()
                    member_ids = [m.user_id for m in members]

                    payload = {
                        "type": "message",
                        "message": {
                            "id": msg.id,
                            "chat_id": msg.chat_id,
                            "sender_id": msg.sender_id,
                            "sender_name": user.display_name,
                            "content": msg.content,
                            "encrypted_key": msg.encrypted_key,
                            "sender_encrypted_key": msg.sender_encrypted_key,
                            "message_type": msg.message_type,
                            "file_url": msg.file_url,
                            "file_name": msg.file_name,
                            "created_at": msg.created_at.isoformat(),
                        }
                    }
                    await manager.send_to_chat(member_ids, payload)

                elif msg_type == "typing":
                    chat_id = data["chat_id"]
                    members = db.query(ChatMember).filter(ChatMember.chat_id == chat_id).all()
                    member_ids = [m.user_id for m in members]
                    await manager.send_to_chat(member_ids, {
                        "type": "typing",
                        "chat_id": chat_id,
                        "user_id": user.id,
                        "user_name": user.display_name,
                    }, exclude_user=user.id)

                elif msg_type == "read":
                    chat_id = data["chat_id"]
                    members = db.query(ChatMember).filter(ChatMember.chat_id == chat_id).all()
                    member_ids = [m.user_id for m in members]
                    await manager.send_to_chat(member_ids, {
                        "type": "read",
                        "chat_id": chat_id,
                        "user_id": user.id,
                    }, exclude_user=user.id)

                elif msg_type == "chat_deleted":
                    chat_id = data["chat_id"]
                    members = db.query(ChatMember).filter(ChatMember.chat_id == chat_id).all()
                    member_ids = [m.user_id for m in members]
                    await manager.send_to_chat(member_ids, {
                        "type": "chat_deleted",
                        "chat_id": chat_id,
                    }, exclude_user=user.id)

        except WebSocketDisconnect:
            pass
        except json.JSONDecodeError:
            pass
        except Exception:
            pass
        finally:
            manager.disconnect(websocket, user.id)
            user.last_seen = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            db.commit()
            await manager.broadcast({
                "type": "presence",
                "user_id": user.id,
                "online": False,
            })
    finally:
        db.close()


@app.get("/api/tunnel-url")
def get_tunnel_url():
    named = _tunnel_config is not None and _tunnel_config.get("tunnel_id") is not None
    return {
        "url": TUNNEL_URL,
        "permanent": named,
        "domain": _tunnel_config.get("domain") if _tunnel_config else None,
    }


@app.get("/api/host-info")
def get_host_info():
    return {
        "tunnel_url": TUNNEL_URL,
        "permanent": _tunnel_config is not None and bool(_tunnel_config.get("tunnel_id")),
        "uptime_seconds": int(time.time() - HOST_START_TIME),
        "online_users": len(manager.get_online_user_ids()),
    }


app.mount("/static", StaticFiles(directory=CLIENT_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    return FileResponse(os.path.join(CLIENT_DIR, "index.html"))
