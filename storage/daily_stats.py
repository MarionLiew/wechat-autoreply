"""每日运维统计：在内存里逐 tick 累加，按日切割。

不需要持久化复杂：daemon 重启即重新计数（用 message_log + leads_log 兜底也能查到原始数据）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Counter


@dataclass
class DailyStats:
    date: str  # YYYY-MM-DD
    replied: int = 0                                # 成功发送回复数
    failed: int = 0                                 # 发送失败数
    by_source: Counter = field(default_factory=Counter)  # rules / rag_direct / rag_fewshot / llm / filler / ...
    llm_429: int = 0                                # 429/过载次数
    leads_processed: int = 0                        # 经营线索成功处理数

    def to_text(self) -> str:
        lines = [
            f"📊 WeCom 自动回复日报 {self.date}",
            "",
            f"  ✅ 成功回复：{self.replied} 条",
            f"  ❌ 发送失败：{self.failed} 条",
            f"  🎯 经营线索：{self.leads_processed} 条已发话术",
        ]
        if self.by_source:
            lines.append("")
            lines.append("  来源分布：")
            for src, n in sorted(self.by_source.items(), key=lambda x: -x[1]):
                lines.append(f"    {src:<16} {n}")
        if self.llm_429:
            lines.append("")
            lines.append(f"  ⚠ LLM 429/过载：{self.llm_429} 次")
        return "\n".join(lines)


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class DailyTracker:
    """逐 tick 累计；提供 day-rollover 检测 → 返回上日 DailyStats 给调用方发邮件。"""

    def __init__(self) -> None:
        self._cur = DailyStats(date=today_str())

    def record_reply(self, source: str) -> None:
        self._cur.replied += 1
        if source:
            self._cur.by_source[source] += 1

    def record_failure(self) -> None:
        self._cur.failed += 1

    def record_llm_429(self) -> None:
        self._cur.llm_429 += 1

    def record_lead(self) -> None:
        self._cur.leads_processed += 1

    def rollover_if_new_day(self) -> DailyStats | None:
        """如果系统时间已跨日 → 返回昨日 stats（调用方负责发邮件），重置当日计数。"""
        td = today_str()
        if td == self._cur.date:
            return None
        prev = self._cur
        self._cur = DailyStats(date=td)
        return prev

    @property
    def current(self) -> DailyStats:
        return self._cur
