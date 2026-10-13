"""新增论文准备后台作业表；保留所有已有业务表和记录。"""
from alembic import op
import sqlalchemy as sa

revision = "0002_paper_preparation"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为论文准备流程建立独立的作业记录表。"""
    op.create_table(
        "paper_preparation_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("paper_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(30), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_paper_preparation_jobs_paper_id", "paper_preparation_jobs", ["paper_id"], unique=False)


def downgrade() -> None:
    """撤销会删除准备作业历史，仅可在已备份且明确需要撤销时使用。"""
    op.drop_index("ix_paper_preparation_jobs_paper_id", table_name="paper_preparation_jobs")
    op.drop_table("paper_preparation_jobs")
