import json
from urllib.request import Request, urlopen

from config import settings

OLLAMA_URL = settings.ollama_base_url + "/api/embed"
EMBEDDING_MODEL = settings.embedding_model

def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    if any(not text.strip() for text in texts):
        raise ValueError("不能为纯空白文本生成向量")

    body = json.dumps(
        {
            "model": EMBEDDING_MODEL,
            "input": texts,
            "truncate": False,
        }
    ).encode("utf-8")

    request = Request(
        OLLAMA_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urlopen(request, timeout=settings.embedding_timeout) as response:
        result = json.load(response)

    embeddings = result["embeddings"]

    if len(embeddings) != len(texts):
        raise ValueError("返回的向量数量与文本数量不一致")

    return embeddings

if __name__ == "__main__":
    texts = ["这篇论文研究大模型检索", "本文讨论如何搜索学术文献"]
    vectors = embed_texts(texts)

    print("文本数量：", len(texts))
    print("向量数量：", len(vectors))
    print("每个向量的数字个数：", len(vectors[0]))
    print("第一个向量的前 5 个数字：", vectors[0][:5])