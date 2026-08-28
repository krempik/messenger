import os
import re
import json
import uuid
import subprocess
import threading
import time
import asyncio
from typing import Optional
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Query, Request, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel, field_validator

from .database import get_db, User, Chat, ChatMember, Message, Reaction, MessageRead, GroupKey, SessionLocal, PushSubscription, Block, ModLog, StickerPack, Sticker, LinkPreview
from .auth import hash_password, verify_password, create_access_token, authenticate_ws_token, get_current_user, SECRET_KEY, get_vapid_keys, create_refresh_token, decode_refresh_token
from .websocket_manager import manager

BASE_DIR = Path(__file__).parent.parent
VERSION_FILE = BASE_DIR / "VERSION"
def get_version():
    try:
        return VERSION_FILE.read_text().strip()
    except Exception:
        return "5.0.0"

_loop: Optional[asyncio.AbstractEventLoop] = None

def _fire(coro):
    if _loop and not _loop.is_closed():
        asyncio.run_coroutine_threadsafe(coro, _loop)

TUNNEL_URL: Optional[str] = None
_tunnel_process: Optional[subprocess.Popen] = None
_tunnel_config: Optional[dict] = None
HOST_START_TIME = time.time()

ALLOWED_AVATAR_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_AVATAR_SIZE = 5 * 1024 * 1024
MAX_FILE_SIZE = 100 * 1024 * 1024
ALLOWED_AVATAR_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
UNSAFE_UPLOAD_EXTS = {".html", ".htm", ".svg", ".xhtml", ".php", ".jsp", ".py", ".pyc", ".sh", ".bat", ".cmd"}
BLOCKED_UPLOAD_MIMES = {"text/html", "text/javascript", "application/javascript", "image/svg+xml"}
ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml", "image/apng", "image/avif", "image/bmp", "image/x-icon", "text/plain", "audio/mpeg", "audio/webm", "audio/wav", "audio/ogg", "video/mp4", "video/webm", "video/ogg", "video/quicktime", "video/x-matroska", "application/zip", "application/x-zip-compressed", "application/pdf", "application/x-rar-compressed", "application/x-7z-compressed", "application/json", "application/octet-stream", "application/x-msdownload"}

_rate_limit_store: dict[str, list[float]] = {}
_rate_limit_store_admin: dict[str, list[float]] = {}
_RATE_LIMIT_WINDOW = 60
_RATE_LIMIT_MAX_REQUESTS = 120
_RATE_LIMIT_ADMIN_MAX = 30


def _check_rate_limit(client_ip: str) -> bool:
    now = time.time()
    window_start = now - _RATE_LIMIT_WINDOW
    if client_ip not in _rate_limit_store:
        _rate_limit_store[client_ip] = []
    requests = _rate_limit_store[client_ip]
    while requests and requests[0] < window_start:
        requests.pop(0)
    if len(requests) >= _RATE_LIMIT_MAX_REQUESTS:
        return False
    requests.append(now)
    return True


def _check_rate_limit_admin(client_ip: str) -> bool:
    now = time.time()
    window_start = now - _RATE_LIMIT_WINDOW
    if client_ip not in _rate_limit_store_admin:
        _rate_limit_store_admin[client_ip] = []
    requests = _rate_limit_store_admin[client_ip]
    while requests and requests[0] < window_start:
        requests.pop(0)
    if len(requests) >= _RATE_LIMIT_ADMIN_MAX:
        return False
    requests.append(now)
    return True


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _find_cloudflared():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cloudflared.exe")
    if os.path.isfile(path):
        return path
    for p in ["cloudflared.exe", "cloudflared"]:
        for d in os.environ.get("PATH", "").split(os.pathsep):
            full = os.path.join(d, p)
            if os.path.isfile(full):
                return full
    return None


def _load_tunnel_config():
    p = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tunnel.json")
    if os.path.isfile(p):
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _start_tunnel():
    global _tunnel_process, TUNNEL_URL, _tunnel_config
    cf = _find_cloudflared()
    if not cf:
        print("[!] cloudflared not found")
        return
    _tunnel_config = _load_tunnel_config()
    named = _tunnel_config and _tunnel_config.get("tunnel_id")
    try:
        if named:
            cmd = [cf, "tunnel", "--no-autoupdate", "run", _tunnel_config.get("tunnel_name", "h4ck")]
            print(f"[*] Named tunnel '{_tunnel_config.get('tunnel_name')}'...")
        else:
            cmd = [cf, "tunnel", "--url", "http://localhost:8000"]
            print("[*] Quick tunnel...")
        _tunnel_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        def _reader():
            global TUNNEL_URL
            pat = re.compile(r"https://(?!api\.)[a-z0-9\-]+\.trycloudflare\.com")
            for line in iter(_tunnel_process.stdout.readline, b""):
                t = line.decode("utf-8", errors="replace").rstrip()
                if t:
                    print(f"[tunnel] {t}")
                if named and _tunnel_config.get("domain"):
                    TUNNEL_URL = f"https://{_tunnel_config['domain']}"
                    if "Connection registered" in t or "INF" in t:
                        print(f"\n{'='*50}\n  PERMANENT URL: {TUNNEL_URL}\n{'='*50}\n")
                else:
                    m = pat.search(t)
                    if m:
                        TUNNEL_URL = m.group(0)
                        print(f"\n{'='*50}\n  PUBLIC URL: {TUNNEL_URL}\n{'='*50}\n")
        threading.Thread(target=_reader, daemon=True).start()
    except Exception as e:
        print(f"[!] cloudflared failed: {e}")


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
    global _loop
    _loop = asyncio.get_running_loop()
    _start_tunnel()
    # Start WebSocket cleanup task
    manager._cleanup_task = asyncio.create_task(manager._cleanup_stale_connections())
    print("[*] H4ck Messenger started on http://localhost:8000")
    yield
    if manager._cleanup_task:
        manager._cleanup_task.cancel()
        try:
            await manager._cleanup_task
        except asyncio.CancelledError:
            pass
    _stop_tunnel()

app = FastAPI(title="H4ck Messenger", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
CLIENT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "client")


@app.get("/api/version")
def get_version_endpoint():
    return {"version": get_version()}


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        client_ip = _get_client_ip(request)
        limit = 30 if request.url.path.startswith("/api/admin/") else _RATE_LIMIT_MAX_REQUESTS
        if not _check_rate_limit_admin(client_ip) if request.url.path.startswith("/api/admin/") else not _check_rate_limit(client_ip):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
    response = await call_next(request)
    return response


class RegisterRequest(BaseModel):
    username: str; display_name: str; password: str; public_key: str
class LoginRequest(BaseModel):
    username: str; password: str
class CreateChatRequest(BaseModel):
    name: Optional[str] = None; member_ids: list[int] = []; theme_color: Optional[str] = None
class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = None; password: Optional[str] = None
    public_key: Optional[str] = None; bio: Optional[str] = None
class UpdateChatRequest(BaseModel):
    name: Optional[str] = None
    theme_color: Optional[str] = None
class ReactionRequest(BaseModel):
    emoji: str
class EditMessageRequest(BaseModel):
    content: str; encrypted_key: Optional[str] = None; sender_encrypted_key: Optional[str] = None
class ForwardMessageRequest(BaseModel):
    chat_id: int


def user_dict(u):
    return {"id": u.id, "username": u.username, "display_name": u.display_name,
            "bio": getattr(u, "bio", "") or "", "public_key": u.public_key,
            "avatar_url": u.avatar_url, "online": manager.is_online(u.id),
            "is_admin": bool(getattr(u, "is_admin", False))}

def message_dict(m, db=None):
    d = {"id": m.id, "chat_id": m.chat_id, "sender_id": m.sender_id,
         "sender_name": m.sender.display_name, "sender_avatar": m.sender.avatar_url,
         "content": m.content, "encrypted_key": m.encrypted_key,
         "sender_encrypted_key": m.sender_encrypted_key, "message_type": m.message_type,
         "file_url": m.file_url, "file_name": m.file_name,
         "reply_to_id": m.reply_to_id, "is_edited": m.is_edited, "is_deleted": m.is_deleted,
         "created_at": m.created_at.isoformat()}
    if m.reply_to:
        d["reply_to_content"] = m.reply_to.content if not m.reply_to.is_deleted else "[удалено]"
        d["reply_to_sender"] = m.reply_to.sender.display_name
    if db:
        reacts = db.query(Reaction).filter(Reaction.message_id == m.id).all()
        d["reactions"] = [{"emoji": r.emoji, "user_id": r.user_id, "user_name": r.user.display_name} for r in reacts]
        reads = db.query(MessageRead).filter(MessageRead.message_id == m.id).all()
        d["read_by"] = [{"user_id": r.user_id, "user_name": r.user.display_name, "read_at": r.read_at.isoformat()} for r in reads]
    return d

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")


@app.post("/api/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if not USERNAME_RE.match(req.username): raise HTTPException(400, "Username: 3-32 chars, a-z, 0-9, _")
    if len(req.password) < 6: raise HTTPException(400, "Password min 6 characters")
    if len(req.display_name) > 128: raise HTTPException(400, "Display name too long")
    if db.query(User).filter(User.username == req.username).first(): raise HTTPException(409, "Username taken")
    user = User(username=req.username, display_name=req.display_name,
                password_hash=hash_password(req.password), public_key=req.public_key)
    db.add(user); db.commit(); db.refresh(user)
    return {"token": create_access_token(user.id), "user": user_dict(user)}

@app.post("/api/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash): raise HTTPException(401, "Invalid credentials")
    return {
        "token": create_access_token(user.id),
        "refresh_token": create_refresh_token(user.id),
        "user": user_dict(user)
    }


@app.post("/api/refresh")
def refresh_token(req: dict, db: Session = Depends(get_db)):
    refresh = req.get("refresh_token")
    if not refresh:
        raise HTTPException(400, "Refresh token required")
    payload = decode_refresh_token(refresh)
    if not payload:
        raise HTTPException(401, "Invalid or expired refresh token")
    user_id = int(payload["sub"])
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(401, "User not found")
    return {
        "token": create_access_token(user.id),
        "refresh_token": create_refresh_token(user.id),
        "user": user_dict(user)
    }


@app.get("/api/me")
def get_me(user: User = Depends(get_current_user)): return user_dict(user)

@app.put("/api/me")
def update_me(req: UpdateProfileRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if req.display_name:
        if len(req.display_name) > 128: raise HTTPException(400, "Name too long")
        user.display_name = req.display_name
    if req.password:
        if len(req.password) < 6: raise HTTPException(400, "Password min 6 chars")
        user.password_hash = hash_password(req.password)
    if req.public_key and len(req.public_key) <= 4096: user.public_key = req.public_key
    if req.bio is not None: user.bio = req.bio[:512]
    db.commit(); db.refresh(user)
    _fire(manager.broadcast({"type": "profile_update", "user_id": user.id,
        "display_name": user.display_name, "avatar_url": user.avatar_url, "bio": user.bio}))
    return user_dict(user)

@app.post("/api/me/avatar")
async def upload_avatar(file: UploadFile = File(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_AVATAR_EXTS: raise HTTPException(400, "Only jpg, png, gif, webp")
    mime = (file.content_type or "").lower()
    if mime and mime not in ALLOWED_AVATAR_MIMES: raise HTTPException(400, "Only jpg, png, gif, webp")
    content = await file.read()
    if not content: raise HTTPException(400, "Empty file")
    if len(content) > MAX_AVATAR_SIZE: raise HTTPException(400, "Max 5MB")
    name = f"avatar_{user.id}_{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, name), "wb") as f: f.write(content)
    if user.avatar_url and "avatar_" in (user.avatar_url or ""):
        old = os.path.join(UPLOAD_DIR, user.avatar_url.split("/")[-1])
        if os.path.isfile(old):
            try: os.remove(old)
            except Exception: pass
    user.avatar_url = f"/uploads/{name}"
    db.commit(); db.refresh(user)
    return user_dict(user)

@app.get("/api/users")
def list_users(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [{**user_dict(u), "online": manager.is_online(u.id)}
            for u in db.query(User).filter(User.id != user.id).all()]

@app.get("/api/users/{user_id}")
def get_user(user_id: int, db: Session = Depends(get_db)):
    u = db.query(User).filter(User.id == user_id).first()
    if not u: raise HTTPException(404, "User not found")
    return {**user_dict(u), "online": manager.is_online(u.id)}

@app.get("/api/users/{user_id}/public-key")
def get_public_key(user_id: int, db: Session = Depends(get_db)):
    u = db.query(User).filter(User.id == user_id).first()
    if not u: raise HTTPException(404, "User not found")
    return {"public_key": u.public_key, "user_id": u.id}

@app.post("/api/chats")
def create_chat(req: CreateChatRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member_ids = [mid for mid in req.member_ids if mid != user.id]
    if len(member_ids) == 1:
        existing = (db.query(Chat).join(ChatMember, ChatMember.chat_id == Chat.id)
            .filter(Chat.is_group == False, ChatMember.user_id.in_([user.id, member_ids[0]]))
            .group_by(Chat.id).having(func.count(ChatMember.id) == 2).first())
        if existing: return {"id": existing.id, "name": existing.name or "Chat", "is_group": False}
    is_group = len(member_ids) > 1
    chat = Chat(name=req.name if is_group else None, is_group=is_group, theme_color=req.theme_color)
    db.add(chat); db.flush()
    db.add(ChatMember(chat_id=chat.id, user_id=user.id, role="owner"))
    for mid in member_ids: db.add(ChatMember(chat_id=chat.id, user_id=mid))
    db.commit(); db.refresh(chat)
    return {"id": chat.id, "name": chat.name or "Chat", "is_group": is_group, "theme_color": chat.theme_color}

@app.post("/api/chats/{chat_id}/avatar")
async def upload_chat_avatar(chat_id: int, file: UploadFile = File(...),
    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == user.id).first()
    if not member: raise HTTPException(403, "Not a member")
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat or not chat.is_group: raise HTTPException(400, "Only group chats")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_AVATAR_EXTS: raise HTTPException(400, "Only jpg, png, gif, webp")
    mime = (file.content_type or "").lower()
    if mime and mime not in ALLOWED_AVATAR_MIMES: raise HTTPException(400, "Only jpg, png, gif, webp")
    content = await file.read()
    if not content: raise HTTPException(400, "Empty file")
    if len(content) > MAX_AVATAR_SIZE: raise HTTPException(400, "Max 5MB")
    name = f"chat_{chat_id}_{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, name), "wb") as f: f.write(content)
    chat.avatar_url = f"/uploads/{name}"
    db.commit()
    members = db.query(ChatMember).filter(ChatMember.chat_id == chat_id).all()
    _fire(manager.send_to_chat([m.user_id for m in members],
        {"type": "chat_update", "chat_id": chat_id, "avatar_url": chat.avatar_url, "name": chat.name}))
    return {"avatar_url": chat.avatar_url}

class AddMembersRequest(BaseModel):
    member_ids: list[int] = []


class UploadGroupKeyRequest(BaseModel):
    encrypted_keys: list
    key_version: int = 1


@app.post("/api/chats/{chat_id}/members")
def add_chat_members(chat_id: int, req: AddMembersRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == user.id).first()
    if not member: raise HTTPException(403, "Not a member")
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat or not chat.is_group: raise HTTPException(400, "Only group chats")
    added = 0
    for mid in req.member_ids:
        exists = db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == mid).first()
        if not exists:
            db.add(ChatMember(chat_id=chat_id, user_id=mid)); added += 1
    db.commit()
    return {"added": added}

@app.post("/api/chats/{chat_id}/leave")
def leave_chat(chat_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == user.id).first()
    if not member: raise HTTPException(403, "Not a member")
    db.delete(member); db.commit()
    remaining = db.query(ChatMember).filter(ChatMember.chat_id == chat_id).count()
    if remaining == 0:
        db.query(Message).filter(Message.chat_id == chat_id).delete()
        db.query(Chat).filter(Chat.id == chat_id).delete()
        db.commit()
    return {"ok": True}

@app.get("/api/chats")
def list_chats(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    memberships = db.query(ChatMember).filter(ChatMember.user_id == user.id).all()
    chat_ids = [m.chat_id for m in memberships]
    last_read_map = {m.chat_id: m.last_read_id for m in memberships}
    if not chat_ids: return []
    chats = db.query(Chat).filter(Chat.id.in_(chat_ids)).all()
    chat_map = {c.id: c for c in chats}
    online_ids = set(manager.get_online_user_ids())
    unread_map = {}
    for cid in chat_ids:
        lr = last_read_map.get(cid, 0)
        cnt = db.query(func.count(Message.id)).filter(
            Message.chat_id == cid, Message.sender_id != user.id, Message.id > lr).scalar()
        if cnt > 0: unread_map[cid] = cnt
    result = []
    for cid in chat_ids:
        chat = chat_map.get(cid)
        if not chat: continue
        members = db.query(ChatMember, User).join(User, User.id == ChatMember.user_id).filter(ChatMember.chat_id == cid).all()
        display_name = chat.name or ""
        other_user = None
        if not chat.is_group:
            for _, u in members:
                if u.id != user.id:
                    display_name = u.display_name; other_user = user_dict(u)
                    other_user["online"] = u.id in online_ids; break
        else:
            display_name = chat.name or ", ".join(u.display_name for _, u in members[:4])
        last_msg = db.query(Message).filter(Message.chat_id == cid).order_by(Message.created_at.desc()).first()
        result.append({"id": chat.id, "name": display_name, "is_group": chat.is_group,
            "avatar_url": chat.avatar_url, "theme_color": chat.theme_color, "other_user": other_user,
            "members": [{**user_dict(u), "online": u.id in online_ids, "role": m.role} for m, u in members],
            "unread": unread_map.get(cid, 0),
            "last_message": message_dict(last_msg) if last_msg else None})
    result.sort(key=lambda c: c["last_message"]["created_at"] if c["last_message"] else "", reverse=True)
    return result

@app.get("/api/chats/{chat_id}")
def get_chat(chat_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == user.id).first()
    if not member: raise HTTPException(403, "Not a member")
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat: raise HTTPException(404, "Chat not found")
    members = db.query(ChatMember, User).join(User, User.id == ChatMember.user_id).filter(ChatMember.chat_id == chat_id).all()
    online_ids = set(manager.get_online_user_ids())
    return {"id": chat.id, "name": chat.name, "is_group": chat.is_group,
            "avatar_url": chat.avatar_url, "theme_color": chat.theme_color,
            "members": [{**user_dict(u), "online": u.id in online_ids, "role": m.role} for m, u in members]}

@app.get("/api/chats/{chat_id}/messages")
def get_messages(chat_id: int, before_id: Optional[int] = None, limit: int = 50,
    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == user.id).first():
        raise HTTPException(403, "Not a member")
    q = db.query(Message).filter(Message.chat_id == chat_id)
    if before_id: q = q.filter(Message.id < before_id)
    msgs = q.order_by(Message.created_at.desc()).limit(limit).all()
    msgs.reverse()
    return [message_dict(m, db) for m in msgs]


@app.post("/api/chats/{chat_id}/read")
def mark_read(chat_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == user.id).first()
    if not member: raise HTTPException(403, "Not a member")
    prev_read = member.last_read_id or 0
    max_id = db.query(func.max(Message.id)).filter(Message.chat_id == chat_id).scalar() or 0
    unread_msgs = db.query(Message).filter(
        Message.chat_id == chat_id, Message.sender_id != user.id, Message.id > prev_read
    ).all()
    for msg in unread_msgs:
        existing = db.query(MessageRead).filter(MessageRead.message_id == msg.id, MessageRead.user_id == user.id).first()
        if not existing:
            db.add(MessageRead(message_id=msg.id, user_id=user.id))
    member.last_read_id = max_id
    db.commit()
    members = db.query(ChatMember).filter(ChatMember.chat_id == chat_id).all()
    _fire(manager.send_to_chat([m.user_id for m in members],
        {"type": "read_receipt", "chat_id": chat_id, "user_id": user.id, "last_read_id": max_id}))
    return {"ok": True}

@app.get("/api/chats/{chat_id}/search")
def search_messages(chat_id: int, q: str = Query(""), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == user.id).first():
        raise HTTPException(403, "Not a member")
    if not q: return []
    msgs = db.query(Message).filter(Message.chat_id == chat_id, Message.content.contains(q),
        Message.is_deleted == False).order_by(Message.created_at.desc()).limit(50).all()
    return [{"id": m.id, "sender_name": m.sender.display_name, "content": m.content,
             "created_at": m.created_at.isoformat(), "message_type": m.message_type} for m in msgs]

@app.put("/api/chats/{chat_id}")
def update_chat(chat_id: int, req: UpdateChatRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat: raise HTTPException(404, "Chat not found")
    member = db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == user.id).first()
    if not member: raise HTTPException(403, "Not a member")
    if req.name is not None: chat.name = req.name[:128]
    if req.theme_color is not None: chat.theme_color = req.theme_color[:7] if req.theme_color else None
    db.commit(); db.refresh(chat)
    members = db.query(ChatMember).filter(ChatMember.chat_id == chat_id).all()
    _fire(manager.send_to_chat([m.user_id for m in members],
        {"type": "chat_update", "chat_id": chat_id, "name": chat.name, "avatar_url": chat.avatar_url}))
    return {"ok": True, "name": chat.name, "theme_color": chat.theme_color}

@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == user.id).first():
        raise HTTPException(403, "Not a member")
    members = db.query(ChatMember).filter(ChatMember.chat_id == chat_id).all()
    member_ids = [m.user_id for m in members]
    _fire(manager.send_to_chat(member_ids,
        {"type": "chat_deleted", "chat_id": chat_id}, exclude_user=user.id))
    db.query(Reaction).filter(Reaction.message_id.in_(
        db.query(Message.id).filter(Message.chat_id == chat_id))).delete(synchronize_session=False)
    db.query(Message).filter(Message.chat_id == chat_id).delete()
    db.query(ChatMember).filter(ChatMember.chat_id == chat_id).delete()
    db.query(Chat).filter(Chat.id == chat_id).delete()
    db.commit()
    return {"ok": True}

@app.delete("/api/chats/{chat_id}/members/{user_id}")
def remove_chat_member(chat_id: int, user_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    caller = db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == user.id).first()
    if not caller or caller.role not in ("owner", "admin"): raise HTTPException(403, "Admin only")
    target = db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == user_id).first()
    if not target: raise HTTPException(404, "Member not found")
    if target.role == "owner": raise HTTPException(403, "Cannot kick owner")
    db.delete(target); db.commit()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    members = db.query(ChatMember).filter(ChatMember.chat_id == chat_id).all()
    _fire(manager.send_to_chat([m.user_id for m in members],
        {"type": "chat_update", "chat_id": chat_id, "name": chat.name, "avatar_url": chat.avatar_url}))
    _fire(manager.send_to_chat([user_id], {"type": "chat_deleted", "chat_id": chat_id}))
    return {"ok": True}

@app.put("/api/chats/{chat_id}/members/{user_id}")
def update_member_role(chat_id: int, user_id: int, body: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    caller = db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == user.id).first()
    if not caller or caller.role != "owner": raise HTTPException(403, "Owner only")
    target = db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == user_id).first()
    if not target: raise HTTPException(404, "Member not found")
    new_role = body.get("role", "member")
    if new_role not in ("member", "admin"): raise HTTPException(400, "Invalid role")
    target.role = new_role; db.commit()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    members = db.query(ChatMember).filter(ChatMember.chat_id == chat_id).all()
    _fire(manager.send_to_chat([m.user_id for m in members],
        {"type": "chat_update", "chat_id": chat_id, "name": chat.name, "avatar_url": chat.avatar_url}))
    return {"ok": True, "role": new_role}


@app.post("/api/chats/{chat_id}/group-key")
def upload_group_key(chat_id: int, req: UploadGroupKeyRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == user.id).first()
    if not member: raise HTTPException(403, "Not a member")
    if not isinstance(req.encrypted_keys, list):
        raise HTTPException(400, "encrypted_keys must be a list")
    for ek in req.encrypted_keys:
        if not isinstance(ek, dict) or "user_id" not in ek or "encrypted_key" not in ek:
            raise HTTPException(400, "Invalid encrypted_key format")
        existing = db.query(GroupKey).filter(GroupKey.chat_id == chat_id, GroupKey.user_id == ek["user_id"]).first()
        if existing: existing.encrypted_key = ek["encrypted_key"]; existing.key_version = req.key_version
        else: db.add(GroupKey(chat_id=chat_id, user_id=ek["user_id"], encrypted_key=ek["encrypted_key"], key_version=req.key_version))
    db.commit()
    return {"ok": True}

@app.get("/api/chats/{chat_id}/group-key")
def get_group_key(chat_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == user.id).first()
    if not member: raise HTTPException(403, "Not a member")
    gk = db.query(GroupKey).filter(GroupKey.chat_id == chat_id, GroupKey.user_id == user.id).order_by(GroupKey.key_version.desc()).first()
    if not gk: return {"key": None, "key_version": 0}
    return {"key": gk.encrypted_key, "key_version": gk.key_version}

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext in UNSAFE_UPLOAD_EXTS: raise HTTPException(400, "Extension not allowed")
    mime = (file.content_type or "").lower()
    if mime in BLOCKED_UPLOAD_MIMES: raise HTTPException(400, "File type not allowed")
    content = await file.read()
    if not content: raise HTTPException(400, "Empty file")
    if len(content) > MAX_FILE_SIZE: raise HTTPException(400, "Max 100MB")
    if ext in {".py", ".js", ".htm", ".html", ".xml"} and not mime:
        raise HTTPException(400, "File type not allowed")
    name = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, name), "wb") as f: f.write(content)
    return {"url": f"/uploads/{name}", "name": file.filename}

@app.post("/api/messages/{message_id}/reactions")
def add_reaction(message_id: int, req: ReactionRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg: raise HTTPException(404, "Message not found")
    existing = db.query(Reaction).filter(Reaction.message_id == message_id,
        Reaction.user_id == user.id, Reaction.emoji == req.emoji).first()
    if existing:
        db.delete(existing); db.commit()
        return {"action": "removed", "emoji": req.emoji}
    r = Reaction(message_id=message_id, user_id=user.id, emoji=req.emoji)
    db.add(r); db.commit()
    members = db.query(ChatMember).filter(ChatMember.chat_id == msg.chat_id).all()
    _fire(manager.send_to_chat([m.user_id for m in members],
        {"type": "reaction", "message_id": message_id, "chat_id": msg.chat_id,
         "user_id": user.id, "user_name": user.display_name, "emoji": req.emoji, "action": "added"}))
    return {"action": "added", "emoji": req.emoji}

@app.put("/api/messages/{message_id}")
def edit_message(message_id: int, req: EditMessageRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg: raise HTTPException(404, "Not found")
    if msg.sender_id != user.id: raise HTTPException(403, "Not your message")
    msg.content = req.content
    if req.encrypted_key: msg.encrypted_key = req.encrypted_key
    if req.sender_encrypted_key: msg.sender_encrypted_key = req.sender_encrypted_key
    msg.is_edited = True
    db.commit()
    members = db.query(ChatMember).filter(ChatMember.chat_id == msg.chat_id).all()
    _fire(manager.send_to_chat([m.user_id for m in members],
        {"type": "message_edited", "message_id": message_id, "chat_id": msg.chat_id,
         "content": msg.content, "encrypted_key": msg.encrypted_key,
         "sender_encrypted_key": msg.sender_encrypted_key}))
    return {"ok": True}

@app.delete("/api/messages/{message_id}")
def delete_message(message_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg: raise HTTPException(404, "Not found")
    member = db.query(ChatMember).filter(ChatMember.chat_id == msg.chat_id, ChatMember.user_id == user.id).first()
    if not member: raise HTTPException(403, "Not a member")
    msg.is_deleted = True; msg.content = "[удалено]"; msg.encrypted_key = None; msg.sender_encrypted_key = None
    db.commit()
    members = db.query(ChatMember).filter(ChatMember.chat_id == msg.chat_id).all()
    _fire(manager.send_to_chat([m.user_id for m in members],
        {"type": "message_deleted", "message_id": message_id, "chat_id": msg.chat_id}))
    return {"ok": True}

@app.post("/api/messages/{message_id}/forward")
def forward_message(message_id: int, req: ForwardMessageRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg: raise HTTPException(404, "Message not found")
    if not db.query(ChatMember).filter(ChatMember.chat_id == msg.chat_id, ChatMember.user_id == user.id).first():
        raise HTTPException(403, "Not a member of source chat")
    if not db.query(ChatMember).filter(ChatMember.chat_id == req.chat_id, ChatMember.user_id == user.id).first():
        raise HTTPException(403, "Not a member of target chat")
    fwd = Message(chat_id=req.chat_id, sender_id=user.id, content=msg.content,
        encrypted_key=msg.encrypted_key, sender_encrypted_key=msg.sender_encrypted_key,
        message_type=msg.message_type, file_url=msg.file_url, file_name=msg.file_name)
    db.add(fwd); db.commit(); db.refresh(fwd)
    members = db.query(ChatMember).filter(ChatMember.chat_id == req.chat_id).all()
    _fire(manager.send_to_chat([m.user_id for m in members],
        {"type": "message", "message": message_dict(fwd, db)}))
    return {"ok": True, "message_id": fwd.id}

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)):
    db = SessionLocal()
    try:
        user = authenticate_ws_token(token, db)
        if not user: await websocket.close(code=4001); return
        await manager.connect(websocket, user.id)
        user.last_seen = datetime.now(timezone.utc); db.commit()
        await manager.broadcast({"type": "presence", "user_id": user.id, "online": True})
        try:
            while True:
                raw = await websocket.receive_text()
                data = json.loads(raw)
                t = data.get("type")
                if t == "message":
                    chat_id = data["chat_id"]
                    if not db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == user.id).first():
                        await websocket.send_text(json.dumps({"type": "error", "detail": "Not a member"})); continue
                    msg = Message(chat_id=chat_id, sender_id=user.id, content=data["content"],
                        encrypted_key=data.get("encrypted_key"), sender_encrypted_key=data.get("sender_encrypted_key"),
                        message_type=data.get("message_type", "text"), file_url=data.get("file_url"),
                        file_name=data.get("file_name"), reply_to_id=data.get("reply_to_id"))
                    db.add(msg); db.commit(); db.refresh(msg)
                    members = db.query(ChatMember).filter(ChatMember.chat_id == chat_id).all()
                    await manager.send_to_chat([m.user_id for m in members],
                        {"type": "message", "message": message_dict(msg, db)})
                elif t == "typing":
                    chat_id = data["chat_id"]
                    members = db.query(ChatMember).filter(ChatMember.chat_id == chat_id).all()
                    await manager.send_to_chat([m.user_id for m in members],
                        {"type": "typing", "chat_id": chat_id, "user_id": user.id,
                         "user_name": user.display_name}, exclude_user=user.id)
                elif t == "read":
                    chat_id = data["chat_id"]
                    members = db.query(ChatMember).filter(ChatMember.chat_id == chat_id).all()
                    await manager.send_to_chat([m.user_id for m in members],
                        {"type": "read", "chat_id": chat_id, "user_id": user.id}, exclude_user=user.id)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            print(f"[WS Error] {e}")
        finally:
            manager.disconnect(websocket, user.id)
            user.last_seen = datetime.now(timezone.utc); db.commit()
            await manager.broadcast({"type": "presence", "user_id": user.id, "online": False})
    finally: db.close()


@app.get("/api/tunnel-url")
def get_tunnel_url():
    named = _tunnel_config and _tunnel_config.get("tunnel_id")
    return {"url": TUNNEL_URL, "permanent": bool(named), "domain": _tunnel_config.get("domain") if _tunnel_config else None}

@app.get("/api/host-info")
def get_host_info():
    return {"tunnel_url": TUNNEL_URL, "permanent": bool(_tunnel_config and _tunnel_config.get("tunnel_id")),
            "uptime_seconds": int(time.time() - HOST_START_TIME), "online_users": len(manager.get_online_user_ids())}


# ---- ADMIN ----

from .auth import decode_token


def verify_admin(request: Request, db: Session = Depends(get_db)) -> User:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401)
    token = auth[7:]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401)
    admin_user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not admin_user or not admin_user.is_admin:
        raise HTTPException(status_code=403)
    return admin_user


class AdminLoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/admin/login")
def admin_login(req: AdminLoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not user.is_admin or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(user.id)
    return {"token": token}


@app.get("/api/admin/stats")
def admin_stats(admin_user: User = Depends(verify_admin), db: Session = Depends(get_db)):
    total_users = db.query(func.count(User.id)).scalar() or 0
    total_chats = db.query(func.count(Chat.id)).scalar() or 0
    total_messages = db.query(func.count(Message.id)).scalar() or 0
    return {"users": total_users, "chats": total_chats, "messages": total_messages, "online": len(manager.get_online_user_ids())}


@app.get("/api/admin/users")
def admin_users(admin_user: User = Depends(verify_admin), db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.id).all()
    return [{"id": u.id, "username": u.username, "display_name": u.display_name, "bio": u.bio,
             "is_admin": bool(u.is_admin), "is_banned": bool(u.is_banned), "is_shadow_banned": bool(u.is_shadow_banned),
             "created_at": u.created_at.isoformat() if u.created_at else None} for u in users]


@app.get("/api/admin/chats")
def admin_chats(admin_user: User = Depends(verify_admin), db: Session = Depends(get_db)):
    chats = db.query(Chat).all()
    result = []
    for c in chats:
        member_count = db.query(func.count(ChatMember.id)).filter(ChatMember.chat_id == c.id).scalar() or 0
        result.append({"id": c.id, "name": c.name, "is_group": c.is_group,
                       "member_count": member_count, "is_hidden": bool(c.is_hidden),
                       "created_at": c.created_at.isoformat() if c.created_at else None})
    return result


@app.get("/api/admin/logs")
def admin_logs(admin_user: User = Depends(verify_admin), db: Session = Depends(get_db), limit: int = 50):
    logs = db.query(ModLog).order_by(ModLog.created_at.desc()).limit(limit).all()
    if not logs:
        return [{"time": datetime.now(timezone.utc).isoformat(), "action": "heartbeat", "detail": "admin polling"}]
    return [{
        "time": log.created_at.isoformat() if log.created_at else None,
        "action": log.action,
        "detail": f"#{log.target_type}:{log.target_id}" + (f" — {log.reason}" if log.reason else "")
    } for log in logs]


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, admin_user: User = Depends(verify_admin), db: Session = Depends(get_db)):
    if user_id == admin_user.id:
        raise HTTPException(403, "Cannot delete yourself")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    db.query(PushSubscription).filter(PushSubscription.user_id == user_id).delete()
    db.query(Block).filter((Block.blocker_id == user_id) | (Block.blocked_id == user_id)).delete()
    db.query(Reaction).filter(Reaction.user_id == user_id).delete()
    db.query(MessageRead).filter(MessageRead.user_id == user_id).delete()
    db.query(GroupKey).filter(GroupKey.user_id == user_id).delete()
    chat_ids = [m.chat_id for m in db.query(ChatMember).filter(ChatMember.user_id == user_id).all()]
    for cid in chat_ids:
        db.query(Message).filter(Message.chat_id == cid, Message.sender_id == user_id).update({Message.is_deleted: True, Message.content: "[аккаунт удален]"})
    db.query(ChatMember).filter(ChatMember.user_id == user_id).delete()
    db.add(ModLog(admin_id=admin_user.id, action="delete_user", target_type="user", target_id=user_id))
    db.delete(user)
    db.commit()
    return {"ok": True}


@app.delete("/api/admin/chats/{chat_id}")
def admin_delete_chat(chat_id: int, admin_user: User = Depends(verify_admin), db: Session = Depends(get_db)):
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(404, "Chat not found")
    members = db.query(ChatMember).filter(ChatMember.chat_id == chat_id).all()
    member_ids = [m.user_id for m in members]
    _fire(manager.send_to_chat(member_ids, {"type": "chat_deleted", "chat_id": chat_id}))
    db.query(Reaction).filter(Reaction.message_id.in_(
        db.query(Message.id).filter(Message.chat_id == chat_id))).delete(synchronize_session=False)
    db.query(MessageRead).filter(MessageRead.message_id.in_(
        db.query(Message.id).filter(Message.chat_id == chat_id))).delete(synchronize_session=False)
    db.query(Message).filter(Message.chat_id == chat_id).delete()
    db.query(ChatMember).filter(ChatMember.chat_id == chat_id).delete()
    db.query(GroupKey).filter(GroupKey.chat_id == chat_id).delete()
    db.add(ModLog(admin_id=admin_user.id, action="delete_chat", target_type="chat", target_id=chat_id))
    db.delete(chat)
    db.commit()
    return {"ok": True}


# ===== PUSH NOTIFICATIONS =====

class PushSubscriptionRequest(BaseModel):
    endpoint: str
    p256dh: str
    auth: str

@app.post("/api/push/subscribe")
def push_subscribe(req: PushSubscriptionRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    existing = db.query(PushSubscription).filter(PushSubscription.user_id == user.id, PushSubscription.endpoint == req.endpoint).first()
    if existing:
        existing.p256dh = req.p256dh
        existing.auth = req.auth
    else:
        db.add(PushSubscription(user_id=user.id, endpoint=req.endpoint, p256dh=req.p256dh, auth=req.auth))
    db.commit()
    return {"ok": True}

@app.delete("/api/push/subscribe")
def push_unsubscribe(req: PushSubscriptionRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(PushSubscription).filter(PushSubscription.user_id == user.id, PushSubscription.endpoint == req.endpoint).delete()
    db.commit()
    return {"ok": True}

@app.get("/api/push/vapid-public-key")
def get_vapid_public_key():
    return {"public_key": get_vapid_keys()["public_key"]}

# ===== BLOCKING =====

@app.post("/api/users/{user_id}/block")
def block_user(user_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user_id == user.id:
        raise HTTPException(400, "Cannot block yourself")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "User not found")
    existing = db.query(Block).filter(Block.blocker_id == user.id, Block.blocked_id == user_id).first()
    if existing:
        return {"ok": True, "already_blocked": True}
    db.add(Block(blocker_id=user.id, blocked_id=user_id))
    db.commit()
    return {"ok": True}

@app.delete("/api/users/{user_id}/block")
def unblock_user(user_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(Block).filter(Block.blocker_id == user.id, Block.blocked_id == user_id).delete()
    db.commit()
    return {"ok": True}

@app.get("/api/users/blocked")
def list_blocked(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    blocked = db.query(Block, User).join(User, User.id == Block.blocked_id).filter(Block.blocker_id == user.id).all()
    return [{"id": u.id, "username": u.username, "display_name": u.display_name, "avatar_url": u.avatar_url} for _, u in blocked]

# ===== CHAT SETTINGS (pin, theme, disappearing) =====

class ChatSettingsRequest(BaseModel):
    pinned_message_id: Optional[int] = None
    theme_color: Optional[str] = None
    disappearing_timer: Optional[int] = None
    is_hidden: Optional[bool] = None
    hidden_pin: Optional[str] = None

@app.put("/api/chats/{chat_id}/settings")
def update_chat_settings(chat_id: int, req: ChatSettingsRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == user.id).first()
    if not member: raise HTTPException(403, "Not a member")
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat: raise HTTPException(404, "Chat not found")
    
    if req.pinned_message_id is not None:
        if req.pinned_message_id == 0:
            chat.pinned_message_id = None
        else:
            msg = db.query(Message).filter(Message.id == req.pinned_message_id, Message.chat_id == chat_id).first()
            if not msg: raise HTTPException(404, "Message not found")
            chat.pinned_message_id = req.pinned_message_id
            msg.is_pinned = True
    
    if req.theme_color is not None:
        chat.theme_color = req.theme_color
    
    if req.disappearing_timer is not None:
        chat.disappearing_timer = req.disappearing_timer
    
    if req.is_hidden is not None:
        chat.is_hidden = req.is_hidden
        if req.is_hidden and req.hidden_pin:
            from passlib.context import CryptContext
            pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
            chat.hidden_pin_hash = pwd_context.hash(req.hidden_pin)
        elif not req.is_hidden:
            chat.hidden_pin_hash = None
    
    db.commit()
    db.refresh(chat)
    
    members = db.query(ChatMember).filter(ChatMember.chat_id == chat_id).all()
    _fire(manager.send_to_chat([m.user_id for m in members],
        {"type": "chat_update", "chat_id": chat_id, "name": chat.name, "avatar_url": chat.avatar_url, "theme_color": chat.theme_color, "pinned_message_id": chat.pinned_message_id, "disappearing_timer": chat.disappearing_timer}))
    
    return {"ok": True, "pinned_message_id": chat.pinned_message_id, "theme_color": chat.theme_color, "disappearing_timer": chat.disappearing_timer}

@app.post("/api/chats/{chat_id}/unhide")
def unhide_chat(chat_id: int, req: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat or not chat.is_hidden:
        raise HTTPException(404, "Chat not found or not hidden")
    if not chat.hidden_pin_hash:
        raise HTTPException(400, "No PIN set")
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    if not pwd_context.verify(req.get("pin", ""), chat.hidden_pin_hash):
        raise HTTPException(401, "Invalid PIN")
    return {"ok": True}

# ===== MESSAGE EXPORT =====

@app.get("/api/chats/{chat_id}/export")
def export_chat(chat_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == user.id).first()
    if not member: raise HTTPException(403, "Not a member")
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat: raise HTTPException(404, "Chat not found")
    messages = db.query(Message).filter(Message.chat_id == chat_id).order_by(Message.created_at.asc()).all()
    members = db.query(ChatMember, User).join(User, User.id == ChatMember.user_id).filter(ChatMember.chat_id == chat_id).all()
    
    return {
        "chat": {"id": chat.id, "name": chat.name, "is_group": chat.is_group},
        "members": [{"id": u.id, "username": u.username, "display_name": u.display_name, "avatar_url": u.avatar_url} for _, u in members],
        "messages": [{"id": m.id, "sender_id": m.sender_id, "content": m.content, "message_type": m.message_type, "file_url": m.file_url, "file_name": m.file_name, "reply_to_id": m.reply_to_id, "created_at": m.created_at.isoformat()} for m in messages]
    }

# ===== ACCOUNT DELETION =====

@app.delete("/api/me")
def delete_account(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Удаляем все данные пользователя
    db.query(PushSubscription).filter(PushSubscription.user_id == user.id).delete()
    db.query(Block).filter((Block.blocker_id == user.id) | (Block.blocked_id == user.id)).delete()
    # Сообщения в чатах, где пользователь участник
    chat_ids = [m.chat_id for m in db.query(ChatMember).filter(ChatMember.user_id == user.id).all()]
    for cid in chat_ids:
        db.query(Message).filter(Message.chat_id == cid, Message.sender_id == user.id).update({Message.is_deleted: True, Message.content: "[аккаунт удален]"})
    db.query(ChatMember).filter(ChatMember.user_id == user.id).delete()
    db.query(User).filter(User.id == user.id).delete()
    db.commit()
    return {"ok": True}

# ===== STICKERS =====

@app.get("/api/stickers")
def get_stickers(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    packs = db.query(StickerPack).order_by(StickerPack.id).all()
    return [{
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "cover_emoji": p.cover_emoji,
        "stickers": [{"id": s.id, "emoji": s.emoji, "image_url": s.image_url} for s in p.stickers]
    } for p in packs]

# ===== LINK PREVIEWS =====

@app.get("/api/link-preview")
def get_link_preview(url: str = Query(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cached = db.query(LinkPreview).filter(LinkPreview.url == url).first()
    if cached and (not cached.expires_at or cached.expires_at > datetime.now(timezone.utc)):
        return {"title": cached.title, "description": cached.description, "image_url": cached.image_url, "site_name": cached.site_name}
    
    # Простой парсинг (в продакшене лучше использовать отдельный сервис)
    try:
        import httpx
        from bs4 import BeautifulSoup
        resp = httpx.get(url, timeout=5, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, "html.parser")
        title = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name": "twitter:title"}) or soup.find("title")
        desc = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "twitter:description"}) or soup.find("meta", attrs={"name": "description"})
        img = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"})
        site = soup.find("meta", property="og:site_name") or soup.find("meta", attrs={"name": "twitter:site"})
        
        data = {
            "title": title.get("content") if title and title.get("content") else (title.text if title else ""),
            "description": desc.get("content") if desc and desc.get("content") else (desc.get("content") if desc else ""),
            "image_url": img.get("content") if img and img.get("content") else "",
            "site_name": site.get("content") if site and site.get("content") else ""
        }
        
        expires = datetime.now(timezone.utc).replace(hour=23, minute=59, second=59)
        if cached:
            cached.title = data["title"]
            cached.description = data["description"]
            cached.image_url = data["image_url"]
            cached.site_name = data["site_name"]
            cached.expires_at = expires
        else:
            db.add(LinkPreview(url=url, **data, expires_at=expires))
        db.commit()
        return data
    except Exception:
        return {"title": "", "description": "", "image_url": "", "site_name": ""}

# ===== ADMIN ENHANCEMENTS =====

@app.get("/api/admin/mod-logs")
def admin_mod_logs(admin_user: User = Depends(verify_admin), db: Session = Depends(get_db), limit: int = 100):
    logs = db.query(ModLog, User).join(User, User.id == ModLog.admin_id).order_by(ModLog.created_at.desc()).limit(limit).all()
    return [{
        "id": log.id,
        "admin": {"id": u.id, "username": u.username},
        "action": log.action,
        "target_type": log.target_type,
        "target_id": log.target_id,
        "reason": log.reason,
        "created_at": log.created_at.isoformat()
    } for log, u in logs]

@app.post("/api/admin/users/{user_id}/ban")
def admin_ban_user(user_id: int, req: dict, admin_user: User = Depends(verify_admin), db: Session = Depends(get_db)):
    if user_id == admin_user.id:
        raise HTTPException(403, "Cannot ban yourself")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "User not found")
    target.is_banned = True
    db.add(ModLog(admin_id=admin_user.id, action="ban", target_type="user", target_id=user_id, reason=req.get("reason")))
    db.commit()
    return {"ok": True}

@app.post("/api/admin/users/{user_id}/unban")
def admin_unban_user(user_id: int, admin_user: User = Depends(verify_admin), db: Session = Depends(get_db)):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "User not found")
    target.is_banned = False
    db.add(ModLog(admin_id=admin_user.id, action="unban", target_type="user", target_id=user_id))
    db.commit()
    return {"ok": True}

@app.post("/api/admin/users/{user_id}/shadow-ban")
def admin_shadow_ban_user(user_id: int, admin_user: User = Depends(verify_admin), db: Session = Depends(get_db)):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "User not found")
    target.is_shadow_banned = True
    db.add(ModLog(admin_id=admin_user.id, action="shadow_ban", target_type="user", target_id=user_id))
    db.commit()
    return {"ok": True}

@app.post("/api/admin/messages/{message_id}/delete")
def admin_delete_message(message_id: int, req: dict, admin_user: User = Depends(verify_admin), db: Session = Depends(get_db)):
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        raise HTTPException(404, "Message not found")
    msg.is_deleted = True
    msg.content = "[удалено модератором]"
    db.add(ModLog(admin_id=admin_user.id, action="delete_message", target_type="message", target_id=message_id, reason=req.get("reason")))
    db.commit()
    members = db.query(ChatMember).filter(ChatMember.chat_id == msg.chat_id).all()
    _fire(manager.send_to_chat([m.user_id for m in members],
        {"type": "message_deleted", "message_id": message_id, "chat_id": msg.chat_id}))
    return {"ok": True}

@app.get("/api/admin/stats/detailed")
def admin_detailed_stats(admin_user: User = Depends(verify_admin), db: Session = Depends(get_db)):
    from sqlalchemy import func
    total_users = db.query(func.count(User.id)).scalar() or 0
    banned_users = db.query(func.count(User.id)).filter(User.is_banned == True).scalar() or 0
    total_chats = db.query(func.count(Chat.id)).scalar() or 0
    total_messages = db.query(func.count(Message.id)).scalar() or 0
    total_reactions = db.query(func.count(Reaction.id)).scalar() or 0
    online = len(manager.get_online_user_ids())
    
    # DAU/MAU approximation
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    dau = db.query(func.count(Message.id)).filter(Message.created_at >= week_ago).scalar() or 0
    
    return {
        "users": {"total": total_users, "banned": banned_users, "online": online},
        "chats": total_chats,
        "messages": total_messages,
        "reactions": total_reactions,
        "dau_approx": dau
    }

# ===== PWA MANIFEST & SERVICE WORKER =====

@app.get("/manifest.json")
def manifest():
    return {
        "name": "h4ck Messenger",
        "short_name": "h4ck",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0a0a0f",
        "theme_color": "#6c5ce7",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
        ],
        "shortcuts": [
            {"name": "New Chat", "url": "/?new_chat=1", "description": "Start a new chat"},
            {"name": "Profile", "url": "/?profile=1", "description": "View profile"}
        ]
    }

@app.get("/sw.js")
def service_worker():
    sw_code = '''
const CACHE_NAME = 'h4ck-v__VERSION__';
const STATIC_ASSETS = ['/', '/static/style.css', '/static/app.js', '/static/crypto.js', '/manifest.json'];

self.addEventListener('install', e => {
    e.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS)));
    self.skipWaiting();
});

self.addEventListener('activate', e => {
    e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))));
    self.clients.claim();
});

self.addEventListener('fetch', e => {
    if (e.request.method !== 'GET') return;
    e.respondWith(
        caches.match(e.request).then(cached => {
            const network = fetch(e.request).then(resp => {
                if (resp.ok) caches.open(CACHE_NAME).then(cache => cache.put(e.request, resp.clone()));
                return resp;
            }).catch(() => cached);
            return cached || network;
        })
    );
});

self.addEventListener('push', e => {
    if (!e.data) return;
    const data = e.data.json();
    const options = {
        body: data.body,
        icon: '/static/icon-192.png',
        badge: '/static/icon-192.png',
        data: { url: data.url || '/' },
        actions: [{action: 'open', title: 'Open'}, {action: 'close', title: 'Close'}]
    };
    e.waitUntil(self.registration.showNotification(data.title, options));
});

self.addEventListener('notificationclick', e => {
    e.notification.close();
    if (e.action === 'close') return;
    e.waitUntil(clients.matchAll({type: 'window'}).then(clients => {
        for (const client of clients) {
            if (client.url.includes(self.location.origin) && 'focus' in client) return client.focus();
        }
        return clients.openWindow(e.notification.data.url || '/');
    }));
});

self.addEventListener('sync', e => {
    if (e.tag === 'send-messages') {
        e.waitUntil(sendQueuedMessages());
    }
});

async function sendQueuedMessages() {
    const db = await openDB();
    const tx = db.transaction('outbox', 'readwrite');
    const store = tx.objectStore('outbox');
    const messages = await store.getAll();
    for (const msg of messages) {
        try {
            await fetch(msg.url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(msg.body)});
            await store.delete(msg.id);
        } catch {}
    }
}

function openDB() {
    return new Promise((resolve, reject) => {
        const req = indexedDB.open('h4ck-outbox', 1);
        req.onupgradeneeded = e => e.target.result.createObjectStore('outbox', {keyPath: 'id', autoIncrement: true});
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
}
'''
    sw_code = sw_code.replace('__VERSION__', get_version())
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(sw_code, media_type="application/javascript")

@app.post("/api/outbox/queue")
def queue_offline_message(req: dict, user: User = Depends(get_current_user)):
    # Для хранения в IndexedDB на клиенте, здесь просто подтверждаем
    return {"ok": True, "id": str(uuid.uuid4())}

app.mount("/static", StaticFiles(directory=CLIENT_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    html = (Path(CLIENT_DIR) / "index.html").read_text(encoding="utf-8")
    html = html.replace('v2.2', f'v{get_version()}')
    return HTMLResponse(html)
