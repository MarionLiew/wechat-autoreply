#!/usr/bin/env python
"""从客户经理聊天记录 xlsx 提取 (客户消息, 客户经理回复) 对。

输入：data/<客户经理>聊天记录.xlsx（列：发送者/接收人/内容/消息类型/发送时间/消息状态）
输出：data/qa_pairs_<客户经理>.jsonl，每行 {customer, q, a, ts}

提取逻辑：
- 按 (客户经理, 客户) 分组对话
- 按时间排序
- 找 "客户发 → 客户经理回" 的紧邻对（同一客户连续多条 → 合并为一个 q）
- 过滤：非文本类型 / 营销模板 / 出方向欢迎语 / 太短(<=2字) 或 太长(>500字) 的消息
"""
import sys
import json
import re
from pathlib import Path
from collections import defaultdict

import pandas as pd


# 客户经理欢迎语 / 营销模板 / 系统通知 关键词（这些回复不进 QA 对，太机械）
OUTGOING_TEMPLATE_KEYWORDS = [
    ("南方航空", "客户经理"),
    ("南方航空", "为您提供"),
    ("南方航空", "感谢您选择"),
    "我已经添加了你，现在我们可以开始聊天了",
    "🎁",
    "生日有礼", "生日赢好礼",
    "飞享加赠", "里程加赠",
    "尊敬的会员",
    "请点击下方链接",
]


def is_template(text: str) -> bool:
    """判断是否营销/欢迎模板（应该排除出 QA 对）。"""
    if not text:
        return True
    for kw in OUTGOING_TEMPLATE_KEYWORDS:
        if isinstance(kw, tuple):
            if all(k in text for k in kw):
                return True
        elif kw in text:
            return True
    return False


def is_meaningful(text: str) -> bool:
    """是否值得作为 QA 对的内容。"""
    if not text or not isinstance(text, str):
        return False
    t = text.strip()
    if len(t) < 2:
        return False
    if len(t) > 500:
        return False
    # 纯表情/符号
    if re.fullmatch(r"[\W_]+", t):
        return False
    return True


def extract_pairs(xlsx_path: Path, manager_name: str) -> list[dict]:
    df = pd.read_excel(xlsx_path)
    # 只保留文本消息
    df = df[df["消息类型"] == "文本"].copy()
    df["发送时间"] = pd.to_datetime(df["发送时间"], errors="coerce")
    df = df.dropna(subset=["发送时间", "内容", "发送者", "接收人"])
    df = df.sort_values("发送时间")

    # 按"对方"分组：对方 = 不是客户经理的那一方
    pairs: list[dict] = []
    for (sender, receiver), group in df.groupby(["发送者", "接收人"]):
        # 跳过群聊 / 营销号收件人
        pass

    # 重组：每条消息归属于一个"对话"（manager <-> customer）
    # 对话主键 = 客户名（即非 manager 的那个人）
    df["__customer"] = df.apply(
        lambda r: r["接收人"] if r["发送者"] == manager_name else r["发送者"],
        axis=1,
    )
    # 仅保留 manager 参与的会话
    df = df[(df["发送者"] == manager_name) | (df["接收人"] == manager_name)]

    for customer, conv in df.groupby("__customer"):
        if customer == manager_name:
            continue
        if "、" in customer:  # 群聊
            continue
        conv = conv.sort_values("发送时间")
        msgs = list(conv.itertuples(index=False))

        i = 0
        while i < len(msgs):
            # 找连续的客户消息（聚合成一个 q）
            if msgs[i].发送者 != customer:
                i += 1
                continue
            q_parts = []
            ts_first = msgs[i].发送时间
            while i < len(msgs) and msgs[i].发送者 == customer:
                txt = str(msgs[i].内容).strip()
                if is_meaningful(txt) and not is_template(txt):
                    q_parts.append(txt)
                i += 1
            if not q_parts:
                continue
            # 接下来连续的客户经理回复（聚合成一个 a）
            a_parts = []
            while i < len(msgs) and msgs[i].发送者 == manager_name:
                txt = str(msgs[i].内容).strip()
                if is_meaningful(txt) and not is_template(txt):
                    a_parts.append(txt)
                i += 1
            if not a_parts:
                continue
            pairs.append({
                "customer": customer,
                "q": "\n".join(q_parts),
                "a": "\n".join(a_parts),
                "ts": ts_first.isoformat(),
            })

    return pairs


def main():
    base = Path("/tmp/chat_data")
    out_dir = Path("data")
    out_dir.mkdir(exist_ok=True)

    for manager, xlsx in [
        ("丘创永", base / "丘创永聊天记录.xlsx"),
        ("罗响", base / "罗响聊天记录.xlsx"),
    ]:
        print(f"\n=== 提取 {manager} ===")
        pairs = extract_pairs(xlsx, manager)
        out = out_dir / f"qa_pairs_{manager}.jsonl"
        with out.open("w", encoding="utf-8") as f:
            for p in pairs:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")

        # 简要统计
        n_unique_customers = len(set(p["customer"] for p in pairs))
        q_lens = [len(p["q"]) for p in pairs]
        a_lens = [len(p["a"]) for p in pairs]
        print(f"  QA 对: {len(pairs):,}")
        print(f"  独立客户: {n_unique_customers}")
        print(f"  q 长度 中位={sorted(q_lens)[len(q_lens)//2] if q_lens else 0}, p95={sorted(q_lens)[int(len(q_lens)*0.95)] if q_lens else 0}")
        print(f"  a 长度 中位={sorted(a_lens)[len(a_lens)//2] if a_lens else 0}, p95={sorted(a_lens)[int(len(a_lens)*0.95)] if a_lens else 0}")
        print(f"  写入 {out}")

        # 抽样 3 条
        print("\n  样本:")
        for p in pairs[:3]:
            print(f"    客户={p['customer'][:20]}")
            print(f"      Q: {p['q'][:80]}")
            print(f"      A: {p['a'][:80]}")
            print()


if __name__ == "__main__":
    main()
