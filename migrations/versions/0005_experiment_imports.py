"""新增实验结果导入记录。"""
from alembic import op
import sqlalchemy as sa


revision = "0005_experiment_imports"
down_revision = "0004_research_plan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiment_imports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("question_set_id", sa.Integer(),
                  sa.ForeignKey("research_question_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("raw_csv", sa.Text(), nullable=False),
        sa.Column("plan_snapshot", sa.JSON(), nullable=False),
        sa.Column("rows", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_experiment_imports_question_set_id", "experiment_imports", ["question_set_id"])


def downgrade() -> None:
    op.drop_index("ix_experiment_imports_question_set_id", table_name="experiment_imports")
    op.drop_table("experiment_imports")
