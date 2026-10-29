"""记忆写入和读取的数据结构。"""

from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


MemoryType = Literal[
    "episodic",
    "semantic",
]

MemoryKind = Literal[
    "event",
    "preference",
    "constraint",
    "experience",
]

MemoryOrigin = Literal[
    "user_statement",
    "tool_observation",
    "model_summary",
]


class MemoryCreate(BaseModel):
    """新增记忆时使用的数据。"""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    research_task_id: int | None = Field(
        default=None,
        gt=0,
    )

    memory_type: MemoryType = "semantic"

    kind: MemoryKind

    content: str = Field(
        min_length=1,
        max_length=2000,
    )

    origin: MemoryOrigin

    source_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=40,
    )

    source_id: int | None = Field(
        default=None,
        gt=0,
    )

    @model_validator(mode="after")
    def validate_classification_and_source(self) -> "MemoryCreate":
        """检查记忆分类及来源是否完整。"""

        expected_type = (
            "episodic"
            if self.kind == "event"
            else "semantic"
        )

        if self.memory_type != expected_type:
            raise ValueError(
                "event属于episodic，其余kind属于semantic"
            )

        if (self.source_type is None) != (self.source_id is None):
            raise ValueError(
                "来源类型和来源编号必须同时提供或同时省略"
            )

        return self


class MemoryUpdate(BaseModel):
    """修改记忆正文。"""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    content: str = Field(
        min_length=1,
        max_length=2000,
    )


class MemoryResponse(BaseModel):
    """从数据库对象读取记忆，不返回检索向量。"""

    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    owner_id: str
    research_task_id: int | None

    memory_type: MemoryType
    kind: MemoryKind
    content: str
    origin: MemoryOrigin

    source_type: str | None
    source_id: int | None

    created_at: datetime
    updated_at: datetime

class MemoryMatch(BaseModel):
    """一条相关记忆及其相似度。"""

    memory: MemoryResponse
    score: float


class MemoryContext(BaseModel):
    """本次提供给Agent的记忆上下文。"""

    content: str
    memory_ids: list[int]
    omitted_ids: list[int]

class MemoryWriteResult(BaseModel):
    """一次记忆保存操作的结果。"""

    memory: MemoryResponse
    created: bool

class ExtractedMemory(BaseModel):
    """从一次已完成调查中提取的事件或经验。"""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    kind: Literal["event", "experience"]

    content: str = Field(
        min_length=1,
        max_length=1000,
    )


class MemoryExtractionResponse(BaseModel):
    """模型返回的记忆候选，可以没有候选。"""

    model_config = ConfigDict(
        extra="forbid",
    )

    memories: list[ExtractedMemory] = Field(
        max_length=3,
    )
