from datetime import datetime, timedelta

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from storage.db import Base, Session, engine


class MessageLog(Base):
    __tablename__ = "message_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    msg_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    customer_id: Mapped[str] = mapped_column(String(256))
    message: Mapped[str] = mapped_column(String(4096))
    reply: Mapped[str] = mapped_column(String(4096))
    source: Mapped[str] = mapped_column(String(16))  # "rules" or "claude"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def init_db() -> None:
    Base.metadata.create_all(engine)


def save(
    msg_hash: str,
    customer_id: str,
    message: str,
    reply: str,
    source: str,
) -> None:
    with Session() as s:
        log = MessageLog(
            msg_hash=msg_hash,
            customer_id=customer_id,
            message=message,
            reply=reply,
            source=source,
        )
        s.add(log)
        s.commit()


def get_recent_hashes(hours: int = 24) -> set[str]:
    """Return msg_hash values saved in the last N hours (for dedup on restart)."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    with Session() as s:
        rows = s.execute(
            select(MessageLog.msg_hash).where(MessageLog.created_at >= cutoff)
        ).scalars().all()
    return set(rows)


def get_recent_logs(limit: int = 200) -> list[MessageLog]:
    """Return the most recent message logs (for admin UI)."""
    with Session() as s:
        rows = s.execute(
            select(MessageLog).order_by(MessageLog.created_at.desc()).limit(limit)
        ).scalars().all()
        # Detach from session so they can be used outside
        s.expunge_all()
        return rows
