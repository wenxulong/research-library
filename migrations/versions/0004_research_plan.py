"""为候选问题记录增加最新研究计划。"""
from alembic import op
import sqlalchemy as sa


revision = "0004_research_plan"
down_revision = "0003_research_questions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_question_sets",
        sa.Column("plan", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("research_question_sets", "plan")
