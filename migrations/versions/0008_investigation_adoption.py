"""记录查证候选的采用时间。"""
from alembic import op
import sqlalchemy as sa

revision = "0008_investigation_adoption"
down_revision = "0007_claim_investigations"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("report_claim_investigations", sa.Column("adopted_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("report_claim_investigations", "adopted_at")
