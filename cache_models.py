from pydantic import BaseModel

from schemas import RAGSourceResponse

class QuestionFeatures(BaseModel):
    """保存问题的基础知识特征。"""

    intent: str
    entities: list[str]
    numbers: list[str]
    negative: bool

class CacheEntry(BaseModel):
    """表示一条完整的问答缓存。"""

    id: str
    question: str
    answer: str

    sources: list[RAGSourceResponse]
    features: QuestionFeatures

    created_at: float
    expires_at: float
    generation_seconds: float

    hits: int = 0
