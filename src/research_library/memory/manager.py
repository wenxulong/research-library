"""共享记忆的读取与上下文组装入口。"""

import json

from research_library.db.model import AgentMemory
from research_library.memory.repository import MemoryRepository
from research_library.memory.retrieval import MemoryRetriever
from research_library.memory.extractor import MemoryExtractor
from research_library.schemas import SavedReportClaimInvestigationResponse
from research_library.memory.schemas import (
    MemoryContext,
    MemoryCreate,
    MemoryMatch,
    MemoryResponse,
    MemoryWriteResult,
)

class MemoryManager:
    """协调记忆检索与上下文预算。"""

    def __init__(
        self,
        repository: MemoryRepository,
        *,
        max_chars: int = 4000,
    ):
        if max_chars <= 0:
            raise ValueError("记忆上下文预算必须大于0")

        self.repository = repository
        self.retriever = MemoryRetriever(repository)
        self.max_chars = max_chars

    def recall(
        self,
        query: str,
        task_id: int | None = None,
        *,
        limit: int = 5,
        min_score: float = 0.3,
    ) -> MemoryContext:
        """读取必要记忆和相关经验，返回可用上下文。"""

        required = self.retriever.get_required_memories(task_id)

        self.build_context(
            required=required,
            related=[],
        )

        related = self.retriever.search(
            query=query,
            task_id=task_id,
            limit=limit,
            min_score=min_score,
        )

        return self.build_context(
            required=required,
            related=related,
        )

    def build_context(
        self,
        required: list[AgentMemory],
        related: list[MemoryMatch],
    ) -> MemoryContext:
        """保留必要记忆，再按预算加入相关记忆。"""

        selected = [
            MemoryResponse.model_validate(record)
            for record in required
        ]

        selected_ids = {
            memory.id
            for memory in selected
        }

        content = self._render(selected)

        if len(content) > self.max_chars:
            raise ValueError(
                "必要偏好和约束超过记忆预算，请精简记忆或调整预算"
            )

        omitted_ids = []

        for match in related:
            memory = match.memory

            if memory.id in selected_ids:
                continue

            proposed = selected + [memory]
            proposed_content = self._render(proposed)

            if len(proposed_content) > self.max_chars:
                omitted_ids.append(memory.id)
                continue

            selected = proposed
            content = proposed_content
            selected_ids.add(memory.id)

        return MemoryContext(
            content=content,
            memory_ids=[
                memory.id
                for memory in selected
            ],
            omitted_ids=omitted_ids,
        )

    def _render(
        self,
        memories: list[MemoryResponse],
    ) -> str:
        """组织记忆正文及来源，不使用论文资料编号。"""

        if not memories:
            return ""

        items = [
            {
                "memory_id": memory.id,
                "memory_type": memory.memory_type,
                "kind": memory.kind,
                "origin": memory.origin,
                "research_task_id": memory.research_task_id,
                "content": memory.content,
                "source_type": memory.source_type,
                "source_id": memory.source_id,
            }
            for memory in memories
        ]

        return json.dumps(
            {
                "usage_note": (
                    "以下是辅助记忆，不是论文证据。"
                    "当前明确要求优先于旧偏好。"
                    "不得将记忆直接作为学术引用。"
                ),
                "memories": items,
            },
            ensure_ascii=False,
            indent=2,
        )

    def search(
        self,
        query: str,
        task_id: int | None = None,
        *,
        limit: int = 3,
        min_score: float = 0.3,
    ) -> MemoryContext:
        """按需检索事件和经验，不重复加载偏好与约束。"""

        related = self.retriever.search(
            query=query,
            task_id=task_id,
            limit=limit,
            min_score=min_score,
        )

        return self.build_context(
            required=[],
            related=related,
        )

    def remember(
        self,
        data: MemoryCreate,
    ) -> MemoryWriteResult:
        """相同范围、类型、来源性质和正文的记忆不重复新增。"""

        records = self.repository.list_for_retrieval(
            task_id=data.research_task_id,
        )

        for record in records:
            if (
                record.research_task_id == data.research_task_id
                and record.memory_type == data.memory_type
                and record.kind == data.kind
                and record.origin == data.origin
                and record.content == data.content
            ):
                return MemoryWriteResult(
                    memory=MemoryResponse.model_validate(record),
                    created=False,
                )

        record = self.repository.add(data)

        return MemoryWriteResult(
            memory=MemoryResponse.model_validate(record),
            created=True,
        )

    def learn_from_run(
        self,
        investigation: SavedReportClaimInvestigationResponse,
    ) -> list[MemoryWriteResult]:
        """提取已保存调查中的记忆，并复用现有保存流程。"""

        extractor = MemoryExtractor()

        extracted = extractor.extract(
            investigation,
        )

        results = []

        for candidate in extracted.memories:
            memory_type = (
                "episodic"
                if candidate.kind == "event"
                else "semantic"
            )

            data = MemoryCreate(
                research_task_id=investigation.result.task_id,
                memory_type=memory_type,
                kind=candidate.kind,
                content=candidate.content,
                origin="model_summary",
                source_type="investigation",
                source_id=investigation.id,
            )

            results.append(
                self.remember(data)
            )

        return results