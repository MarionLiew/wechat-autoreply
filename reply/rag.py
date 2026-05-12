"""RAG 检索层：从客户经理聊天记录里检索最相似的历史 QA 对。

数据布局：
    data/rag_index/<manager>/
      embeddings.npy   (N, dim) float32, 已 L2 normalized
      metadata.jsonl   N 行 {q, a, customer, ts}

运行时：
    rag = RagRetriever(manager="罗响")
    hits = rag.search(query="罗经理麻烦帮我选个第一排", k=3)
    # hits = [{q, a, customer, ts, score, safe_for_direct}, ...]
    # safe_for_direct=True 才能直接复用 a 作回复（无客户姓名泄漏）

懒加载——首次 .search() 时才载入模型 + 索引；后续复用。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import numpy as np


MODEL_NAME = "maidalun1020/bce-embedding-base_v1"

# 模型只载入一次，跨 manager 共享
_model = None


# ── 历史回复清洗 + 安全性判定 ──────────────────────────────────────
# A 路径要直接复用 hist_a 时，必须不含其他客户的特定信息。

# 删除 WeCom 引用/回复消息块（多行块，以 "这是一条引用/回复消息：" 开头到 "------" 结束）
_QUOTE_BLOCK = re.compile(
    r"这是一条引用/回复消息.*?------",
    flags=re.DOTALL,
)

# 含具体客户称呼的句式：1-5 字（中文名/英文名/拼音）后跟称谓
# 例：'亮哥', '龙哥', '刘先生', '王总', 'Mike哥', '亮亮姐'
# 注意：不限定后续语境，因为'好的亮哥'、'好的龙哥'、'我帮您看看龙哥'都需识别
_NAME_ADDRESSING = re.compile(
    r"(?:[一-鿿]|[A-Za-z]){1,5}(先生|女士|小姐|哥|姐|爷|总|老师|博士|教授)"
)

# 客户经理欢迎语模板（应该被滤掉）
_WELCOME_TEMPLATE = re.compile(
    r"关注到您近期有出行计划|您乘坐.{0,20}航班|值机截载时间|提前选座和在线值机|尊敬的会员"
)

# 具体事实 / 上下文相关短语——含任何一个就不能直接复用（专属于别的客户的上下文）
_SPECIFIC_FACTS = re.compile(
    # 航班号：CZ3226 / MU5101 / CA1234 / HU7890 等
    r"[A-Z]{2}\d{2,5}"
    # 日期 / 时间 / 时段
    r"|\d{1,2}月\d{1,2}|\d{1,2}日|\d{1,2}号"
    r"|\d{1,2}[:：]\d{2}|\d{1,2}点"
    # 主要城市（航司客户常用出发到达地）
    r"|北京|上海|广州|深圳|成都|西安|武汉|杭州|南京|重庆"
    r"|昆明|乌鲁木齐|拉萨|哈尔滨|沈阳|长春|大连|青岛"
    r"|济南|郑州|长沙|福州|厦门|香港|澳门|台北|台中|海口|三亚"
    r"|机场|航班|航线|班机|航空"
    # 上下文相关短语（"刚刚我调了" "您上次..."）
    r"|刚刚|刚才|上次|之前我|我说的|我发的|我们之前"
    r"|您之前|您上次|您说的|您发的|那个航班|那个时间"
    # 数字+单位（金额、里程等）
    r"|\d+元|\d+块|\d+万|\d+千|\d+里程"
)

# 完成态承诺——bot 不能直接复用历史回复里这些"已经办好"的句式，
# 因为 bot 实际没有操作系统，会撒谎/误导客户。
_COMPLETION_CLAIM = re.compile(
    r"搞定了|搞掂|办好了|改好了|选好了|调好了|弄好了|帮您改了|帮您选了|帮您调了|"
    r"已经.{0,4}(?:好了|安排|搞定|改|选|调|办|处理)|"
    r"给您(?:改|选|调|办|加|送|领).{0,3}了|"
    r"成功(?:改|选|调|办|加|送|升|降|累).{0,3}"
)


def _sanitize_reply(text: str) -> str:
    """删除引用块、保留其他文本。"""
    if not text:
        return ""
    text = _QUOTE_BLOCK.sub("", text)
    # 多个空行合并
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_safe_for_direct(text: str) -> bool:
    """判断清洗后的回复是否安全可直接复用。

    不安全条件（任一即否）：
    1. 包含具体客户称呼（"亮哥"、"刘先生您好"等）
    2. 包含欢迎模板（"关注到您近期有出行"）
    3. 太长（> 80 字）——直接复用长回复风险高
    4. 太短（< 1 字）——空内容
    """
    if not text or len(text) > 80 or len(text) < 1:
        return False
    if _NAME_ADDRESSING.search(text):
        return False
    if _WELCOME_TEMPLATE.search(text):
        return False
    if _SPECIFIC_FACTS.search(text):
        return False
    if _COMPLETION_CLAIM.search(text):
        return False
    return True


def _clean_name(raw: str) -> str:
    """从 sender_id 抽干净人名：去性别括号 + ID 后缀 + 表情等。"""
    if not raw:
        return ""
    return raw.split("(")[0].split("（")[0].split("-")[0].strip()


_SURNAME_SUFFIXES = ("先生", "女士", "小姐", "太太", "夫人")
_NICKNAME_SUFFIXES = ("哥", "姐", "总", "爷", "老板", "老师", "经理", "博士", "教授")


def substitute_customer_name(text: str, hist_customer: str, current_customer: str) -> tuple[str, bool]:
    """把回复里的历史客户名换成当前客户名，遵循中文称谓习惯：
    - 后跟"先生/女士/小姐"等 → 用姓（current_customer 首字）→ '刘先生'
    - 后跟"哥/姐/总/爷"等 → 用名末字（current_customer 末字）→ '斌哥'
    - 其他（如直接呼名） → 用末字
    """
    if not text or not hist_customer or not current_customer:
        return text, False
    h = _clean_name(hist_customer)
    if not h or h not in text:
        return text, False
    c = _clean_name(current_customer)
    if not c:
        return text, False

    # 中文客户：按 姓/末字 规则替换。英文/拼音客户：用完整名字替换、连带把后续称谓后缀一并吃掉。
    has_chinese = any("一" <= ch <= "鿿" for ch in c)
    first_char = c[0]
    last_char = c[-1] if len(c) > 1 else c

    parts = []
    i = 0
    changed = False
    while i < len(text):
        idx = text.find(h, i)
        if idx == -1:
            parts.append(text[i:])
            break
        suffix_zone = text[idx + len(h):idx + len(h) + 4]
        if has_chinese:
            if suffix_zone.startswith(_SURNAME_SUFFIXES):
                new_addr = first_char
            elif suffix_zone.startswith(_NICKNAME_SUFFIXES):
                new_addr = last_char
            else:
                new_addr = last_char
            consume_extra = 0
        else:
            # 英文/拼音：直接整名替换，且把紧跟的中文称谓后缀（哥/姐/先生/...）也吃掉
            new_addr = c
            consume_extra = 0
            for suf in _SURNAME_SUFFIXES + _NICKNAME_SUFFIXES:
                if suffix_zone.startswith(suf):
                    consume_extra = len(suf)
                    break
        parts.append(text[i:idx])
        parts.append(new_addr)
        i = idx + len(h) + consume_extra
        changed = True
    return "".join(parts), changed


def is_safe_after_substitution(text: str, current_customer: str) -> bool:
    """替换历史人名为当前客户名后的安全检查。

    允许 X哥/X姐/X先生 等，只要 X 是当前客户名的字。
    """
    if not text or len(text) > 80:
        return False
    if _WELCOME_TEMPLATE.search(text):
        return False
    name = _clean_name(current_customer)
    if not name:
        # 没有当前客户名 → 不能验证，保守不允许
        return not _NAME_ADDRESSING.search(text)
    name_chars = set(name)
    # 所有 X哥/X姐 形式里，X 必须含当前客户名字符
    for m in _NAME_ADDRESSING.finditer(text):
        x_full = m.group(0)
        title = m.group(1)
        x_name = x_full[:-len(title)]
        # x_name 至少有一个字符在当前客户名字符集里才视为安全
        if not any(c in name_chars for c in x_name):
            return False
    return True


def _get_model():
    global _model
    if _model is None:
        # 延迟 import：让没用 RAG 的场景不付 200MB 包导入成本
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model


class RagRetriever:
    def __init__(self, manager: str, base_dir: Path | str = "data/rag_index"):
        self.manager = manager
        self.base = Path(base_dir) / manager
        self._embeddings: Optional[np.ndarray] = None
        self._meta: Optional[list[dict]] = None

    def _load(self):
        if self._embeddings is not None:
            return
        emb_path = self.base / "embeddings.npy"
        meta_path = self.base / "metadata.jsonl"
        if not emb_path.exists() or not meta_path.exists():
            raise FileNotFoundError(
                f"RAG 索引未找到: {emb_path} / {meta_path}。"
                "请先运行 scripts/build_rag_index.py 构建。"
            )
        self._embeddings = np.load(emb_path)
        with meta_path.open(encoding="utf-8") as f:
            self._meta = [json.loads(line) for line in f]
        if len(self._meta) != self._embeddings.shape[0]:
            raise ValueError(
                f"索引不一致: {self._embeddings.shape[0]} vectors vs {len(self._meta)} metadata"
            )

    def search(self, query: str, k: int = 3, min_score: float = 0.5) -> list[dict]:
        """检索 top-k 最相似 QA 对。score 是 cosine（normalized dot），范围 [-1,1]。

        min_score 过滤：太低分（不相关）的不返回，调用方可据此决定是否走 LLM 兜底。
        """
        self._load()
        model = _get_model()
        q_emb = model.encode(
            [query],
            normalize_embeddings=True,
            convert_to_numpy=True,
        )[0].astype(np.float32)
        # cosine = dot（都已 L2 normalized）
        scores = self._embeddings @ q_emb  # shape (N,)
        # 取 top-k
        if k >= len(scores):
            idx = np.argsort(-scores)
        else:
            idx = np.argpartition(-scores, k)[:k]
            idx = idx[np.argsort(-scores[idx])]
        hits = []
        for i in idx[:k]:
            s = float(scores[i])
            if s < min_score:
                continue
            m = self._meta[i]
            cleaned_a = _sanitize_reply(m["a"])
            hits.append({
                "q": m["q"],
                "a": cleaned_a,                                 # 清洗后的回复（去引用块）
                "a_raw": m["a"],                                # 原始回复（few-shot 用）
                "customer": m.get("customer", ""),
                "ts": m.get("ts", ""),
                "score": s,
                "safe_for_direct": _is_safe_for_direct(cleaned_a),
            })
        return hits


# 单例缓存——按 manager 复用
_retrievers: dict[str, RagRetriever] = {}


def get_retriever(manager: str) -> RagRetriever:
    if manager not in _retrievers:
        _retrievers[manager] = RagRetriever(manager)
    return _retrievers[manager]
