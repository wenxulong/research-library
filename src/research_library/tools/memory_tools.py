"""提供给证据Agent使用的记忆查询与保存工具。"""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from research_library.memory.manager import MemoryManager
from research_library.memory.repository import MemoryRepository
from research_library.memory.schemas import MemoryCreate


class SearchMemoryArguments(BaseModel):
    """模型调用记忆查询工具时提供的参数。"""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    query: str = Field(
        min_length=1,
        max_length=1000,
        description="需要回顾的历史事件、查证经验或处理方法。",
    )

    limit: int = Field(
        default=3,
        ge=1,
        le=5,
        description="返回相关记忆的数量，默认3条，最多5条。",
    )

class RememberMemoryArguments(BaseModel):
    """模型主动保存事件或经验时提供的参数。"""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    kind: Literal["event", "experience"] = Field(
        description="event表示本次事件，experience表示可复用经验。",
    )

    content: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "需要保留的事件摘要或经验，"
            "写清适用条件，不把未经核实的推断写成确定事实。"
        ),
    )

MEMORY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_memories",
            "description": (
                "检索当前用户和当前研究任务范围内的历史事件与经验。"
                "当初始记忆不足，或者执行中出现新的查证方向时使用。"
                "历史记忆只能辅助选择行动，不能代替论文原文证据。"
                "同一问题没有新需求时，不要反复检索相同记忆。"
            ),
            "parameters": SearchMemoryArguments.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_memory",
            "description": (
                "保存当前研究任务中值得保留的事件摘要或查证经验。"
                "仅在本次执行确实获得有价值的信息时使用，不必每次调用。"
                "内容应注明适用条件或尚未确认的部分。"
                "不得将未经核实的研究结论保存为确定事实。"
                "已有相同记忆时复用，不修改正式报告。"
            ),
            "parameters": RememberMemoryArguments.model_json_schema(),
        },
    },
]

def execute_memory_search(
    db: Session,
    task_id: int,
    arguments: dict,
) -> dict:
    """校验模型参数，并在当前任务范围内检索记忆。"""

    parsed = SearchMemoryArguments.model_validate(
        arguments,
        strict=True,
    )

    repository = MemoryRepository(
        db=db,
        owner_id="local-user",
    )

    manager = MemoryManager(
        repository=repository,
        max_chars=4000,
    )

    context = manager.search(
        query=parsed.query,
        task_id=task_id,
        limit=parsed.limit,
    )

    memory_payload = None

    if context.content:
        memory_payload = json.loads(
            context.content,
        )

    return {
        "memory_context": memory_payload,
        "memory_ids": context.memory_ids,
        "omitted_ids": context.omitted_ids,
    }

def execute_memory_write(
    db: Session,
    task_id: int,
    arguments: dict,
    *,
    report_job_id: int | None = None,
) -> dict:
    """将模型提出的事件或经验保存到当前任务。"""

    parsed = RememberMemoryArguments.model_validate(
        arguments,
        strict=True,
    )

    memory_type = (
        "episodic"
        if parsed.kind == "event"
        else "semantic"
    )

    data = MemoryCreate(
        research_task_id=task_id,
        memory_type=memory_type,
        kind=parsed.kind,
        content=parsed.content,
        origin="model_summary",
        source_type=(
            "report_job"
            if report_job_id is not None
            else None
        ),
        source_id=report_job_id,
    )

    repository = MemoryRepository(
        db=db,
        owner_id="local-user",
    )

    manager = MemoryManager(
        repository=repository,
    )

    result = manager.remember(data)

    return {
        "memory_id": result.memory.id,
        "created": result.created,
        "kind": result.memory.kind,
        "content": result.memory.content,
        "research_task_id": result.memory.research_task_id,
        "source_type": result.memory.source_type,
        "source_id": result.memory.source_id,
    }
