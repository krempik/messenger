import os
import base64
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

from .database import get_db, User

SECRET_KEY = os.environ.get("MESSENGER_SECRET", os.urandom(32).hex())
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

# VAPID keys for Web Push
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY")

if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    VAPID_PRIVATE_KEY = base64.urlsafe_b64encode(
        private_key.private_numbers().private_value.to_bytes(32, 'big')
    ).decode().rstrip('=')
    VAPID_PUBLIC_KEY = base64.urlsafe_b64encode(
        public_key.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint
        )
    ).decode().rstrip('=')

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
        {"sub": str(user_id), "exp": expire, "iss": "h4ck-messenger"},
        SECRET_KEY, algorithm=ALGORITHM
    )


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM], options={"require_exp": True}, issuer="h4ck-messenger")
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
    return user


def authenticate_ws_token(token: str, db: Session) -> Optional[User]:
    payload = decode_token(token)
    if not payload:
        return None
    return db.query(User).filter(User.id == int(payload["sub"])).first()


def create_refresh_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=30)
    return jwt.encode(
        {"sub": str(user_id), "exp": expire, "iss": "h4ck-messenger", "type": "refresh"},
        SECRET_KEY, algorithm=ALGORITHM
    )


def decode_refresh_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM], options={"require_exp": True}, issuer="h4ck-messenger")
        if payload.get("type") != "refresh":
            return None
        return payload
    except JWTError:
        return None


def get_secret_key() -> str:
    return SECRET_KEY


def get_vapid_keys():
    return {"private_key": VAPID_PRIVATE_KEY, "public_key": VAPID_PUBLIC_KEY}
