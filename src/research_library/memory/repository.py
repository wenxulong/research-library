"""记忆数据库仓库，不调用模型、不生成向量。"""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from research_library.db.model import (
    AgentMemory,
    ResearchTask,
)

from research_library.memory.schemas import (
    MemoryCreate,
    MemoryUpdate,
)


class MemoryRepository:
    """绑定本次执行的数据库会话和所属用户。"""

    def __init__(
        self,
        db: Session,
        owner_id: str,
    ):
        owner_id = owner_id.strip()

        if not owner_id or len(owner_id) > 100:
            raise ValueError(
                "用户标识必须为1到100个字符"
            )

        self.db = db
        self.owner_id = owner_id

    def add(
        self,
        data: MemoryCreate,
    ) -> AgentMemory:
        """新增并保存一条记忆。"""

        if data.research_task_id is not None:
            task = self.db.get(
                ResearchTask,
                data.research_task_id,
            )

            if task is None:
                raise ValueError("研究任务不存在")

        record = AgentMemory(
            owner_id=self.owner_id,
            **data.model_dump(),
        )

        self.db.add(record)
        self._commit()
        self.db.refresh(record)

        return record

    def get(
        self,
        memory_id: int,
    ) -> AgentMemory | None:
        """仅查询当前用户的记忆。"""

        statement = select(AgentMemory).where(
            AgentMemory.id == memory_id,
            AgentMemory.owner_id == self.owner_id,
        )

        return self.db.scalar(statement)

    def list_candidates(
        self,
        task_id: int | None = None,
        *,
        limit: int = 50,
    ) -> list[AgentMemory]:
        """读取用户级记忆，以及指定任务的记忆。"""

        if task_id is not None and task_id <= 0:
            raise ValueError("任务编号必须大于0")

        if not 1 <= limit <= 100:
            raise ValueError("读取数量必须为1到100")

        scope = AgentMemory.research_task_id.is_(None)

        if task_id is not None:
            scope = or_(
                scope,
                AgentMemory.research_task_id == task_id,
            )

        statement = (
            select(AgentMemory)
            .where(
                AgentMemory.owner_id == self.owner_id,
                scope,
            )
            .order_by(AgentMemory.id.desc())
            .limit(limit)
        )

        return list(
            self.db.scalars(statement).all()
        )

    def update(
        self,
        memory_id: int,
        data: MemoryUpdate,
    ) -> AgentMemory | None:
        """修改正文，并清除已失效的旧向量。"""

        record = self.get(memory_id)

        if record is None:
            return None

        if record.content != data.content:
            record.content = data.content
            record.embedding = None
            record.embedding_model = None

            self._commit()
            self.db.refresh(record)

        return record

    def delete(
        self,
        memory_id: int,
    ) -> bool:
        """删除当前用户的一条记忆。"""

        record = self.get(memory_id)

        if record is None:
            return False

        self.db.delete(record)
        self._commit()

        return True

    def _commit(self) -> None:
        """提交失败时回滚，并继续向上抛出异常。"""

        try:
            self.db.commit()

        except Exception:
            self.db.rollback()
            raise

    def list_for_retrieval(
        self,
        task_id: int | None = None,
    ) -> list[AgentMemory]:
        """读取当前用户、当前任务范围内的记忆。"""

        if task_id is not None and task_id <= 0:
            raise ValueError("任务编号必须大于0")

        scope = AgentMemory.research_task_id.is_(None)

        if task_id is not None:
            scope = or_(
                scope,
                AgentMemory.research_task_id == task_id,
            )

        statement = (
            select(AgentMemory)
            .where(
                AgentMemory.owner_id == self.owner_id,
                scope,
            )
            .order_by(AgentMemory.id.desc())
        )

        return list(
            self.db.scalars(statement).all()
        )

    def save_embedding(
        self,
        memory_id: int,
        vector: list[float],
        model_name: str,
    ) -> bool:
        """保存当前用户某条记忆的向量。"""

        record = self.get(memory_id)

        if record is None:
            return False

        record.embedding = vector
        record.embedding_model = model_name

        self._commit()

        return True
