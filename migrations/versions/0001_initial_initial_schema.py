"""initial schema

Revision ID: 0001_initial
Revises: 
Create Date: 2026-10-11 15:59:58.040087

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
revision: str = '0001_initial'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('papers', sa.Column('id', sa.Integer(), autoincrement=True, nullable=False), sa.Column('title', sa.String(length=200), nullable=False), sa.Column('author', sa.String(length=100), nullable=False), sa.Column('year', sa.Integer(), nullable=False), sa.Column('notes', sa.String(length=1000), nullable=False), sa.PrimaryKeyConstraint('id'))
    op.create_table('research_tasks', sa.Column('id', sa.Integer(), autoincrement=True, nullable=False), sa.Column('title', sa.String(length=200), nullable=False), sa.Column('question', sa.Text(), nullable=False), sa.Column('status', sa.String(length=20), nullable=False), sa.Column('result', sa.Text(), nullable=True), sa.Column('review_result', sa.Text(), nullable=True), sa.PrimaryKeyConstraint('id'))
    op.create_table('paper_documents', sa.Column('id', sa.Integer(), autoincrement=True, nullable=False), sa.Column('paper_id', sa.Integer(), nullable=False), sa.Column('original_name', sa.String(length=255), nullable=False), sa.Column('stored_name', sa.String(length=255), nullable=False), sa.Column('file_path', sa.String(length=1000), nullable=False), sa.Column('content_type', sa.String(length=100), nullable=False), sa.Column('file_size', sa.Integer(), nullable=False), sa.Column('uploaded_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False), sa.ForeignKeyConstraint(['paper_id'], ['papers.id'], ondelete='CASCADE'), sa.PrimaryKeyConstraint('id'), sa.UniqueConstraint('paper_id'), sa.UniqueConstraint('stored_name'))
    op.create_table('report_jobs', sa.Column('id', sa.Integer(), autoincrement=True, nullable=False), sa.Column('task_id', sa.Integer(), nullable=False), sa.Column('status', sa.String(length=20), nullable=False), sa.Column('stage', sa.String(length=50), nullable=False), sa.Column('analysis_snapshot', sa.JSON(), nullable=False), sa.Column('result', sa.JSON(), nullable=True), sa.Column('error_message', sa.Text(), nullable=True), sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False), sa.Column('started_at', sa.DateTime(), nullable=True), sa.Column('finished_at', sa.DateTime(), nullable=True), sa.ForeignKeyConstraint(['task_id'], ['research_tasks.id']), sa.PrimaryKeyConstraint('id'))
    op.create_index(op.f('ix_report_jobs_task_id'), 'report_jobs', ['task_id'], unique=False)
    op.create_table('research_task_analysis_versions', sa.Column('id', sa.Integer(), autoincrement=True, nullable=False), sa.Column('task_id', sa.Integer(), nullable=False), sa.Column('previous_analysis', sa.Text(), nullable=False), sa.Column('updated_analysis', sa.Text(), nullable=False), sa.Column('previous_review', sa.Text(), nullable=True), sa.Column('reason', sa.Text(), nullable=False), sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False), sa.ForeignKeyConstraint(['task_id'], ['research_tasks.id'], ondelete='CASCADE'), sa.PrimaryKeyConstraint('id'))
    op.create_index(op.f('ix_research_task_analysis_versions_task_id'), 'research_task_analysis_versions', ['task_id'], unique=False)
    op.create_table('research_task_comparison_reports', sa.Column('id', sa.Integer(), autoincrement=True, nullable=False), sa.Column('task_id', sa.Integer(), nullable=False), sa.Column('analysis_snapshot', sa.Text(), nullable=False), sa.Column('checked_report_json', sa.Text(), nullable=False), sa.Column('saved_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False), sa.ForeignKeyConstraint(['task_id'], ['research_tasks.id'], ondelete='CASCADE'), sa.PrimaryKeyConstraint('id'), sa.UniqueConstraint('task_id'))
    op.create_table('research_task_papers', sa.Column('id', sa.Integer(), autoincrement=True, nullable=False), sa.Column('task_id', sa.Integer(), nullable=False), sa.Column('paper_id', sa.Integer(), nullable=False), sa.Column('note', sa.String(length=500), nullable=False), sa.ForeignKeyConstraint(['paper_id'], ['papers.id'], ondelete='CASCADE'), sa.ForeignKeyConstraint(['task_id'], ['research_tasks.id'], ondelete='CASCADE'), sa.PrimaryKeyConstraint('id'), sa.UniqueConstraint('task_id', 'paper_id', name='uq_research_task_paper'))
    op.create_table('paper_chunks', sa.Column('id', sa.Integer(), autoincrement=True, nullable=False), sa.Column('document_id', sa.Integer(), nullable=False), sa.Column('page_number', sa.Integer(), nullable=False), sa.Column('chunk_index', sa.Integer(), nullable=False), sa.Column('text', sa.Text(), nullable=False), sa.Column('char_count', sa.Integer(), nullable=False), sa.ForeignKeyConstraint(['document_id'], ['paper_documents.id'], ondelete='CASCADE'), sa.PrimaryKeyConstraint('id'), sa.UniqueConstraint('document_id', 'page_number', 'chunk_index', name='uq_document_page_number'))
    op.create_table('paper_pages', sa.Column('id', sa.Integer(), autoincrement=True, nullable=False), sa.Column('document_id', sa.Integer(), nullable=False), sa.Column('page_number', sa.Integer(), nullable=False), sa.Column('text', sa.Text(), nullable=False), sa.Column('char_count', sa.Integer(), nullable=False), sa.ForeignKeyConstraint(['document_id'], ['paper_documents.id'], ondelete='CASCADE'), sa.PrimaryKeyConstraint('id'), sa.UniqueConstraint('document_id', 'page_number', name='uq_document_page_number'))
    op.create_table('paper_chunk_embeddings', sa.Column('chunk_id', sa.Integer(), nullable=False), sa.Column('model_name', sa.String(length=100), nullable=False), sa.Column('vector', sa.JSON(), nullable=False), sa.ForeignKeyConstraint(['chunk_id'], ['paper_chunks.id'], ondelete='CASCADE'), sa.PrimaryKeyConstraint('chunk_id'))

def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('paper_chunk_embeddings')
    op.drop_table('paper_pages')
    op.drop_table('paper_chunks')
    op.drop_table('research_task_papers')
    op.drop_table('research_task_comparison_reports')
    op.drop_index(op.f('ix_research_task_analysis_versions_task_id'), table_name='research_task_analysis_versions')
    op.drop_table('research_task_analysis_versions')
    op.drop_index(op.f('ix_report_jobs_task_id'), table_name='report_jobs')
    op.drop_table('report_jobs')
    op.drop_table('paper_documents')
    op.drop_table('research_tasks')
    op.drop_table('papers')
