"""新增共享长期记忆表。"""

from alembic import op
import sqlalchemy as sa


revision = "0009_agent_memories"
down_revision = "0008_investigation_adoption"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_memories",

        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),

        sa.Column(
            "owner_id",
            sa.String(100),
            nullable=False,
        ),

        sa.Column(
            "research_task_id",
            sa.Integer(),
            sa.ForeignKey(
                "research_tasks.id",
                ondelete="CASCADE",
            ),
            nullable=True,
        ),

        sa.Column("memory_type", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(30), nullable=False),

        sa.Column("source_type", sa.String(40), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),

        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("embedding_model", sa.String(200), nullable=True),

        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),

        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )

    op.create_index(
        "ix_agent_memories_owner_task",
        "agent_memories",
        ["owner_id", "research_task_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_memories_owner_task",
        table_name="agent_memories",
    )

    op.drop_table("agent_memories")