import os
import base64
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

from .database import get_db, User

log = logging.getLogger("frendo.auth")

_SECRET_FILE = os.path.join(os.path.dirname(__file__), ".secret_key")


def _load_or_create_secret() -> str:
    env = os.environ.get("MESSENGER_SECRET")
    if env:
        return env
    try:
        if os.path.isfile(_SECRET_FILE):
            with open(_SECRET_FILE, "r") as f:
                val = f.read().strip()
            if val:
                return val
        val = os.urandom(32).hex()
        with open(_SECRET_FILE, "w") as f:
            f.write(val)
        return val
    except Exception as e:
        # Last resort: ephemeral random secret. All JWTs will be invalidated
        # on restart — log so it is never silent.
        log.warning(f"could not read/write {_SECRET_FILE}: {e}; using ephemeral secret")
        return os.urandom(32).hex()


SECRET_KEY = _load_or_create_secret()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

# VAPID keys for Web Push. Single source of truth: either both env vars, or a
# persisted keypair on disk — never re-generated per process, or every
# subscription would die on restart.
_VAPID_PRIVATE_FILE = os.path.join(os.path.dirname(__file__), ".vapid_private")
_VAPID_PUBLIC_FILE = os.path.join(os.path.dirname(__file__), ".vapid_public")


def _generate_vapid_keys():
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    private_b64 = base64.urlsafe_b64encode(
        private_key.private_numbers().private_value.to_bytes(32, 'big')
    ).decode().rstrip('=')
    public_b64 = base64.urlsafe_b64encode(
        public_key.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint
        )
    ).decode().rstrip('=')
    return private_b64, public_b64


def _load_or_create_vapid() -> tuple[str, str]:
    env_private = os.environ.get("VAPID_PRIVATE_KEY")
    env_public = os.environ.get("VAPID_PUBLIC_KEY")
    if env_private and env_public:
        return env_private, env_public
    try:
        if os.path.isfile(_VAPID_PRIVATE_FILE) and os.path.isfile(_VAPID_PUBLIC_FILE):
            private_val = Path(_VAPID_PRIVATE_FILE).read_text(encoding="utf-8").strip()
            public_val = Path(_VAPID_PUBLIC_FILE).read_text(encoding="utf-8").strip()
            if private_val and public_val:
                return private_val, public_val
        private_b64, public_b64 = _generate_vapid_keys()
        Path(_VAPID_PRIVATE_FILE).write_text(private_b64, encoding="utf-8")
        Path(_VAPID_PUBLIC_FILE).write_text(public_b64, encoding="utf-8")
        log.info("generated and persisted new VAPID keypair")
        return private_b64, public_b64
    except Exception as exc:
        log.error("could not persist VAPID keys (%s); using ephemeral pair", exc)
        return _generate_vapid_keys()


VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY = _load_or_create_vapid()

# Use pbkdf2_sha256 instead of bcrypt to avoid passlib bcrypt bug
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    return jwt.encode(
        {"sub": str(user_id), "exp": expire, "iss": "frendo"},
        SECRET_KEY, algorithm=ALGORITHM
    )


def decode_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM], options={"require_exp": True}, issuer="frendo")
        # Refresh tokens must never be used as access tokens.
        if payload.get("type") == "refresh":
            return None
        return payload
    except JWTError:
        return None


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(401, "Not authenticated")
    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(401, "Invalid or expired token")
    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user:
        raise HTTPException(401, "User not found")
    if user.is_banned:
        raise HTTPException(403, "Account is banned")
    return user


def authenticate_ws_token(token: str, db: Session) -> Optional[User]:
    payload = decode_token(token)
    if not payload:
        return None
    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user or user.is_banned:
        return None
    return user


def create_refresh_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=30)
    return jwt.encode(
        {"sub": str(user_id), "exp": expire, "iss": "frendo", "type": "refresh"},
        SECRET_KEY, algorithm=ALGORITHM
    )


def decode_refresh_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM], options={"require_exp": True}, issuer="frendo")
        if payload.get("type") != "refresh":
            return None
        return payload
    except JWTError:
        return None


def get_vapid_keys():
    return {"private_key": VAPID_PRIVATE_KEY, "public_key": VAPID_PUBLIC_KEY}
