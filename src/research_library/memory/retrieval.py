"""共享记忆的向量索引与检索。"""

from research_library.db.model import AgentMemory
from research_library.memory.repository import MemoryRepository
from research_library.memory.schemas import (
    MemoryMatch,
    MemoryResponse,
)
from research_library.retrieval import embedding_service
from research_library.retrieval.vector_utils import cosine_similarity


class MemoryRetriever:
    """检索当前用户、当前任务可使用的记忆。"""

    def __init__(
        self,
        repository: MemoryRepository,
    ):
        self.repository = repository
        self.embedding_model = embedding_service.EMBEDDING_MODEL

    def get_required_memories(
        self,
        task_id: int | None = None,
    ) -> list[AgentMemory]:
        """读取偏好和约束，不依赖语义相似度。"""

        records = self.repository.list_for_retrieval(task_id)

        return [
            record
            for record in records
            if record.kind in ("preference", "constraint")
        ]

    def index_memories(
        self,
        task_id: int | None = None,
    ) -> int:
        """为事件和经验补充当前模型的向量。"""

        records = self.repository.list_for_retrieval(task_id)

        pending = [
            record
            for record in records
            if record.kind in ("event", "experience")
            and (
                record.embedding is None
                or record.embedding_model != self.embedding_model
            )
        ]

        saved_count = 0

        for start in range(0, len(pending), 16):
            batch = pending[start:start + 16]

            texts = [
                record.content
                for record in batch
            ]

            vectors = embedding_service.embed_texts(texts)

            for record, vector in zip(batch, vectors):
                saved = self.repository.save_embedding(
                    memory_id=record.id,
                    vector=vector,
                    model_name=self.embedding_model,
                )

                if saved:
                    saved_count += 1

        return saved_count

    def search(
        self,
        query: str,
        task_id: int | None = None,
        *,
        limit: int = 5,
        min_score: float = 0.3,
    ) -> list[MemoryMatch]:
        """根据问题检索相关事件和经验。"""

        query = query.strip()

        if not query:
            raise ValueError("记忆检索问题不能为空")

        if limit <= 0:
            raise ValueError("检索数量必须大于0")

        self.index_memories(task_id)

        records = self.repository.list_for_retrieval(task_id)

        candidates = [
            record
            for record in records
            if record.kind in ("event", "experience")
            and record.embedding is not None
            and record.embedding_model == self.embedding_model
        ]

        if not candidates:
            return []

        query_vector = embedding_service.embed_texts([query])[0]

        matches = []

        for record in candidates:
            score = cosine_similarity(
                query_vector,
                record.embedding,
            )

            if score < min_score:
                continue

            matches.append(
                MemoryMatch(
                    memory=MemoryResponse.model_validate(record),
                    score=score,
                )
            )

        matches.sort(
            key=lambda item: item.score,
            reverse=True,
        )

        return matches[:limit]