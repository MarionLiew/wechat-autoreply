"""手动触发日报邮件——基于 message_log（DB）+ leads_log（jsonl）即时统计。

用法：
    python scripts/send_daily_report.py                # 今天
    python scripts/send_daily_report.py 2026-05-12     # 指定某天
    python scripts/send_daily_report.py --test         # 只发"邮件通路测试"

不依赖 daemon 进程内的 DailyTracker，可以在 daemon 没跑时也能用。
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import settings  # noqa: E402
from storage import leads_log, mailer  # noqa: E402


def _query_replies_on(date_str: str) -> tuple[int, Counter]:
    """从 messages.db 统计某日成功 reply 条数 + 来源分布。"""
    import sqlite3
    db = ROOT / "messages.db"
    if not db.exists():
        return 0, Counter()
    src_counter: Counter = Counter()
    total = 0
    try:
        conn = sqlite3.connect(str(db))
        cur = conn.cursor()
        # messages 表 schema：默认 created_at TEXT
        cur.execute(
            "SELECT source FROM messages WHERE substr(created_at, 1, 10) = ?",
            (date_str,),
        )
        for (src,) in cur.fetchall():
            total += 1
            src_counter[src or "unknown"] += 1
        conn.close()
    except Exception as exc:
        print(f"读 messages.db 异常：{exc}", file=sys.stderr)
    return total, src_counter


def build_report(date_str: str) -> str:
    replied, by_src = _query_replies_on(date_str)
    leads = leads_log.count_on(date_str)
    lines = [
        f"📊 WeCom 自动回复日报 {date_str}",
        "",
        f"  ✅ 成功回复：{replied} 条",
        f"  🎯 经营线索：{leads} 条已发话术",
    ]
    if by_src:
        lines.append("")
        lines.append("  来源分布：")
        for src, n in sorted(by_src.items(), key=lambda x: -x[1]):
            lines.append(f"    {src:<16} {n}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--test":
        ok = mailer.send_mail(
            "[WeCom 日报] 邮件通路测试",
            "如果你看到这条，说明 SMTP 配置 OK。\n\n"
            f"发件：{settings.smtp_user}\n收件：{settings.smtp_to}\n",
        )
        return 0 if ok else 2

    if len(argv) >= 2:
        date_str = argv[1]
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")

    body = build_report(date_str)
    print(body)
    print()

    if not settings.daily_report_enabled:
        print("（DAILY_REPORT_ENABLED=false，不发邮件；仅打印）")
        return 0

    subject = f"[WeCom 日报] {date_str}"
    ok = mailer.send_mail(subject, body)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
