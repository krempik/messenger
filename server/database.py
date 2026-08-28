import os
import json
import logging
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Boolean, UniqueConstraint, Index, LargeBinary, inspect
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime, timezone

log = logging.getLogger("h4ck.db")

DB_PATH = os.environ.get("MESSENGER_DB", os.path.join(os.path.dirname(os.path.dirname(__file__)), "messenger.db"))
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False}, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, index=True, nullable=False)
    display_name = Column(String(128), nullable=False)
    bio = Column(String(512), nullable=True, default="")
    status = Column(String(128), nullable=True, default="")
    password_hash = Column(String(256), nullable=False)
    public_key = Column(Text, nullable=False)
    private_key_encrypted = Column(Text, nullable=True)  # для мульти-девайс
    avatar_url = Column(String(512), nullable=True)
    last_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    is_admin = Column(Boolean, default=False, index=True)
    is_banned = Column(Boolean, default=False, index=True)
    is_shadow_banned = Column(Boolean, default=False)
    vapid_p256dh = Column(String(512), nullable=True)
    vapid_auth = Column(String(512), nullable=True)
    sent_messages = relationship("Message", back_populates="sender", foreign_keys="Message.sender_id")


class Chat(Base):
    __tablename__ = "chats"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=True)
    is_group = Column(Boolean, default=False)
    avatar_url = Column(String(512), nullable=True)
    theme_color = Column(String(7), nullable=True, default=None)
    pinned_message_id = Column(Integer, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    disappearing_timer = Column(Integer, default=0)  # секунды: 0=off, 3600=1h, 86400=24h, 604800=7d
    is_hidden = Column(Boolean, default=False)
    hidden_pin_hash = Column(String(256), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    members = relationship("ChatMember", back_populates="chat", cascade="all, delete-orphan")
    messages = relationship("Message", back_populates="chat", cascade="all, delete-orphan", foreign_keys="Message.chat_id")
    pinned_message = relationship("Message", foreign_keys=[pinned_message_id])


class ChatMember(Base):
    __tablename__ = "chat_members"
    __table_args__ = (UniqueConstraint("chat_id", "user_id", name="uq_chat_member"),)
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, ForeignKey("chats.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(32), default="member")
    last_read_id = Column(Integer, default=0)
    joined_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    chat = relationship("Chat", back_populates="members")
    user = relationship("User")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_chat_created", "chat_id", "created_at"),)
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, ForeignKey("chats.id", ondelete="CASCADE"), nullable=False)
    sender_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)
    encrypted_key = Column(Text, nullable=True)
    sender_encrypted_key = Column(Text, nullable=True)
    message_type = Column(String(32), default="text")  # text, file, voice, sticker, system
    file_url = Column(String(512), nullable=True)
    file_name = Column(String(256), nullable=True)
    file_size = Column(Integer, nullable=True)
    file_mime = Column(String(128), nullable=True)
    voice_duration = Column(Integer, nullable=True)  # миллисекунды
    sticker_id = Column(Integer, ForeignKey("stickers.id", ondelete="SET NULL"), nullable=True)
    reply_to_id = Column(Integer, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    is_edited = Column(Boolean, default=False)
    is_deleted = Column(Boolean, default=False)
    is_pinned = Column(Boolean, default=False)
    expires_at = Column(DateTime, nullable=True)  # для исчезающих сообщений
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    chat = relationship("Chat", back_populates="messages", foreign_keys=[chat_id])
    sender = relationship("User", back_populates="sent_messages")
    reply_to = relationship("Message", remote_side=[id], backref="replies")
    sticker = relationship("Sticker")


class Reaction(Base):
    __tablename__ = "reactions"
    __table_args__ = (UniqueConstraint("message_id", "user_id", "emoji", name="uq_reaction"),)
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    emoji = Column(String(16), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    message = relationship("Message", backref="reactions")
    user = relationship("User")


class MessageRead(Base):
    __tablename__ = "message_reads"
    __table_args__ = (UniqueConstraint("message_id", "user_id", name="uq_msg_read"),)
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    read_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    message = relationship("Message", backref="read_records")
    user = relationship("User")


class GroupKey(Base):
    __tablename__ = "group_keys"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, ForeignKey("chats.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    encrypted_key = Column(Text, nullable=False)
    key_version = Column(Integer, default=1)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint("chat_id", "user_id", name="uq_group_key"),)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    endpoint = Column(Text, nullable=False)
    p256dh = Column(String(512), nullable=False)
    auth = Column(String(512), nullable=False)
    user_agent = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    user = relationship("User")


class Block(Base):
    __tablename__ = "blocks"
    __table_args__ = (UniqueConstraint("blocker_id", "blocked_id", name="uq_block"),)
    id = Column(Integer, primary_key=True, index=True)
    blocker_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    blocked_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    blocker = relationship("User", foreign_keys=[blocker_id])
    blocked = relationship("User", foreign_keys=[blocked_id])


class ModLog(Base):
    __tablename__ = "mod_logs"
    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    action = Column(String(64), nullable=False)  # ban, unban, delete_user, delete_chat, delete_message, pin, unpin
    target_type = Column(String(32), nullable=False)  # user, chat, message
    target_id = Column(Integer, nullable=False)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    admin = relationship("User")


class StickerPack(Base):
    __tablename__ = "sticker_packs"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(64), nullable=False)
    description = Column(String(256), nullable=True)
    cover_emoji = Column(String(16), nullable=True)
    is_official = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    stickers = relationship("Sticker", back_populates="pack", cascade="all, delete-orphan")


class Sticker(Base):
    __tablename__ = "stickers"
    id = Column(Integer, primary_key=True, index=True)
    pack_id = Column(Integer, ForeignKey("sticker_packs.id", ondelete="CASCADE"), nullable=False)
    emoji = Column(String(16), nullable=False)
    image_url = Column(String(512), nullable=False)
    order = Column(Integer, default=0)
    pack = relationship("StickerPack", back_populates="stickers")


class LinkPreview(Base):
    __tablename__ = "link_previews"
    id = Column(Integer, primary_key=True, index=True)
    url = Column(String(2048), unique=True, nullable=False, index=True)
    title = Column(String(256), nullable=True)
    description = Column(Text, nullable=True)
    image_url = Column(String(512), nullable=True)
    site_name = Column(String(128), nullable=True)
    fetched_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=True)


def _auto_migrate():
    """Add missing columns to existing tables without destroying data."""
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    migrated = []

    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            continue
        existing_cols = {c["name"] for c in inspector.get_columns(table_name)}
        for col in table.columns:
            if col.name not in existing_cols:
                col_type = col.type.compile(engine.dialect)
                nullable = "NULL" if col.nullable else "NOT NULL"
                default_val = ""
                if col.default is not None and col.default.is_scalar:
                    default_val = f" DEFAULT {repr(col.default.arg)}"
                elif col.nullable:
                    default_val = " DEFAULT NULL"
                sql = f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type} {nullable}{default_val}"
                try:
                    with engine.connect() as conn:
                        conn.execute(__import__("sqlalchemy").text(sql))
                        conn.commit()
                    migrated.append(f"{table_name}.{col.name}")
                except Exception as e:
                    log.warning(f"Migration skip {table_name}.{col.name}: {e}")

    if migrated:
        log.info(f"Auto-migrated columns: {', '.join(migrated)}")


Base.metadata.create_all(bind=engine)
_auto_migrate()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
