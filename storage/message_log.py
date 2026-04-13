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
    # 发送诊断：实际使用的文本写入方式（AXValue / setString / sendKeys）
    send_method: Mapped[str] = mapped_column(String(32), default="")
    # 从检测到未读消息到发送完成的毫秒数
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)


def init_db() -> None:
    Base.metadata.create_all(engine)
    # 轻量迁移：给老库补上新列
    from sqlalchemy import text
    with engine.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql(
            "PRAGMA table_info(message_logs)"
        ).fetchall()}
        if "send_method" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE message_logs ADD COLUMN send_method VARCHAR(32) DEFAULT ''"
            )
        if "latency_ms" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE message_logs ADD COLUMN latency_ms INTEGER DEFAULT 0"
            )


def save(
    msg_hash: str,
    customer_id: str,
    message: str,
    reply: str,
    source: str,
    send_method: str = "",
    latency_ms: int = 0,
) -> None:
    with Session() as s:
        log = MessageLog(
            msg_hash=msg_hash,
            customer_id=customer_id,
            message=message,
            reply=reply,
            source=source,
            send_method=send_method,
            latency_ms=latency_ms,
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


def delete_by_ids(ids: list[int]) -> int:
    """按 id 列表删除日志；返回实际删除条数。"""
    if not ids:
        return 0
    from sqlalchemy import delete
    with Session() as s:
        result = s.execute(delete(MessageLog).where(MessageLog.id.in_(ids)))
        s.commit()
        return result.rowcount or 0


def delete_all() -> int:
    """清空整个 message_logs 表；返回删除条数。"""
    from sqlalchemy import delete
    with Session() as s:
        result = s.execute(delete(MessageLog))
        s.commit()
        return result.rowcount or 0


def get_by_sender(sender: str, limit: int = 5) -> list[MessageLog]:
    """按客户 id 获取最近的对话记录（按时间降序），用于 LLM 上下文。"""
    if not sender:
        return []
    with Session() as s:
        rows = s.execute(
            select(MessageLog)
            .where(MessageLog.customer_id == sender)
            .order_by(MessageLog.created_at.desc())
            .limit(limit)
        ).scalars().all()
        s.expunge_all()
        return list(reversed(rows))  # 返回时按时间升序


def get_recent_logs(limit: int = 200) -> list[MessageLog]:
    """Return the most recent message logs (for admin UI)."""
    with Session() as s:
        rows = s.execute(
            select(MessageLog).order_by(MessageLog.created_at.desc()).limit(limit)
        ).scalars().all()
        # Detach from session so they can be used outside
        s.expunge_all()
        return rows
