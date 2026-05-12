#!/usr/bin/env python
"""把 data/qa_pairs_<manager>.jsonl 嵌入为向量索引。

输出：data/rag_index/<manager>/
  embeddings.npy   (N, dim) float32 numpy 矩阵
  metadata.jsonl   每行对应一行 qa pair 元数据（q, a, customer, ts）

模型：maidalun1020/bce-embedding-base_v1（网易有道，中文 RAG 调优）
- 768 维
- 首次下载 ~400MB（HuggingFace 缓存）
- 后续从 ~/.cache/huggingface 复用
"""
import sys
import json
import time
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer


MODEL_NAME = "maidalun1020/bce-embedding-base_v1"


def build_index(manager: str, src: Path, out_dir: Path, model: SentenceTransformer):
    pairs = [json.loads(l) for l in src.open(encoding="utf-8")]
    print(f"  {manager}: {len(pairs):,} 条 QA 对")

    questions = [p["q"] for p in pairs]
    t0 = time.monotonic()
    # BCE 推荐 normalize_embeddings=True 后用 dot product 等价 cosine
    embeddings = model.encode(
        questions,
        batch_size=64,
        show_progress_bar=True,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    elapsed = time.monotonic() - t0
    print(f"    嵌入完成：shape={embeddings.shape} 耗时 {elapsed:.1f}s ({len(pairs)/elapsed:.1f} 条/s)")

    out_dir.mkdir(parents=True, exist_ok=True)
    np.save(out_dir / "embeddings.npy", embeddings.astype(np.float32))
    with (out_dir / "metadata.jsonl").open("w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"    写入 {out_dir}/")


def main():
    print(f"加载模型 {MODEL_NAME} ...")
    t0 = time.monotonic()
    model = SentenceTransformer(MODEL_NAME)
    print(f"  模型加载 {time.monotonic()-t0:.1f}s, 维度={model.get_sentence_embedding_dimension()}")

    data_dir = Path("data")
    out_root = data_dir / "rag_index"

    for manager in ["丘创永", "罗响"]:
        src = data_dir / f"qa_pairs_{manager}.jsonl"
        if not src.exists():
            print(f"  跳过 {manager}: {src} 不存在")
            continue
        out_dir = out_root / manager
        build_index(manager, src, out_dir, model)


if __name__ == "__main__":
    main()
