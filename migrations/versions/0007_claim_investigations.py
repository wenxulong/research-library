"""新增陈述查证候选记录表。"""
from alembic import op
import sqlalchemy as sa

revision = "0007_claim_investigations"
down_revision = "0006_paper_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_claim_investigations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("report_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("claim_id", sa.String(32), nullable=False),
        sa.Column("base_report", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_report_claim_investigations_job_id", "report_claim_investigations", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_report_claim_investigations_job_id", table_name="report_claim_investigations")
    op.drop_table("report_claim_investigations")
