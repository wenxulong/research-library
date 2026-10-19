"""新增论文初稿存储表。"""

from alembic import op
import sqlalchemy as sa


revision = "0006_paper_drafts"
down_revision = "0005_experiment_imports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_drafts",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "import_id",
            sa.Integer(),
            sa.ForeignKey(
                "experiment_imports.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "draft_data",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "materials_snapshot",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )

    op.create_index(
        "ix_paper_drafts_import_id",
        "paper_drafts",
        ["import_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_paper_drafts_import_id",
        table_name="paper_drafts",
    )

    op.drop_table("paper_drafts")
