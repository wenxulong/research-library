"""新增候选研究问题及用户选择记录。"""

from alembic import op
import sqlalchemy as sa


revision = "0003_research_questions"
down_revision = "0002_paper_preparation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_question_sets",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("research_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "report_job_id",
            sa.Integer(),
            sa.ForeignKey("report_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("selected_candidate_number", sa.Integer(), nullable=True),
        sa.Column("selected_question", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )

    op.create_index(
        "ix_research_question_sets_task_id",
        "research_question_sets",
        ["task_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_question_sets_task_id",
        table_name="research_question_sets",
    )

    op.drop_table("research_question_sets")
