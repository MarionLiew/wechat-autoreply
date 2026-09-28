"""One customer-address policy shared by native WeCom AI and our fallback AI."""
from __future__ import annotations

import re


def _identity(sender: str | None) -> tuple[str, str | None]:
    if not sender:
        return "", None
    name = re.split(r"[（(]", sender, maxsplit=1)[0].strip()
    name = re.sub(r"-\d+$", "", name).strip()
    # Only the explicit CRM gender tag is reliable; do not infer from a name or chat history.
    match = re.search(r"[（(]([男女])[）)]", sender)
    return name, match.group(1) if match else None


def address_instruction(sender: str | None) -> str:
    name, gender = _identity(sender)
    chinese_name = bool(name and re.fullmatch(r"[\u4e00-\u9fff]+", name))
    if chinese_name and gender:
        formal = f"{name[0]}{'先生' if gender == '男' else '女士'}"
        target = f"这位客户的备注为「{name}」，性别已确认。称呼客户时只用「{formal}」或直接用「您」。"
    else:
        target = (f"客户备注为「{name}」。" if name else "") + "不要猜测性别；没有可靠的中文姓氏及性别时直接用「您」，不加性别称谓。"
    return (
        target + "不要用「X哥」「X姐」「小姐姐」「帅哥」「老哥」「X总」或直呼名字末字；"
        "不要照搬示例里的X哥/X姐等历史称呼。此规则优先于基础提示词、聊天记录和RAG示例。"
    )


def normalize_address(text: str, sender: str | None) -> str:
    """Repair customer-directed informal titles without rewriting references to relatives/others."""
    name, gender = _identity(sender)
    formal = f"{name[0]}{'先生' if gender == '男' else '女士'}" if name and gender and re.fullmatch(r"[\u4e00-\u9fff]+", name) else ""
    if name:
        # Exact current-name vocatives only: never replace another passenger's name.
        stems = sorted({name, name[0], name[-1]}, key=len, reverse=True)
        pattern = re.compile(r"(?:" + "|".join(map(re.escape, stems)) + r")(?:哥|姐|小姐|总|爷)")
        text = pattern.sub(formal or (name if not re.fullmatch(r"[\u4e00-\u9fff]+", name) else ""), text)
    # Standalone customer vocatives; avoid phrases such as “您的哥哥/姐姐”.
    generic = re.compile(r"(?<![的您])(老哥|小姐姐|帅哥)(?=[，,。！!？?\s]|稍等|您好|好|您|$)")
    text = generic.sub(formal, text)
    return text
