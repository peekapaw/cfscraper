"""Add performance indexes for jobs and job_results

Revision ID: 173c3bda8858
Revises: add_tags_priority_fields
Create Date: 2025-07-16 22:55:14.458897

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision: str = '173c3bda8858'
down_revision: Union[str, Sequence[str], None] = 'add_tags_priority_fields'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add performance indexes to existing tables."""
    # Add indexes to jobs table for better query performance
    op.create_index('ix_jobs_status', 'jobs', ['status'])
    op.create_index('ix_jobs_scraper_type', 'jobs', ['scraper_type'])
    op.create_index('ix_jobs_url', 'jobs', ['url'])
    op.create_index('ix_jobs_priority', 'jobs', ['priority'])
    op.create_index('ix_jobs_created_at', 'jobs', ['created_at'])
    op.create_index('ix_jobs_started_at', 'jobs', ['started_at'])
    op.create_index('ix_jobs_completed_at', 'jobs', ['completed_at'])

    # Composite indexes for common query patterns
    op.create_index('idx_job_status_created', 'jobs', ['status', 'created_at'])
    op.create_index('idx_job_status_scraper_type', 'jobs', ['status', 'scraper_type'])
    op.create_index('idx_job_created_status', 'jobs', ['created_at', 'status'])
    op.create_index('idx_job_priority_created', 'jobs', ['priority', 'created_at'])
    op.create_index('idx_job_url_status', 'jobs', ['url', 'status'])
    op.create_index('idx_job_scraper_status_created', 'jobs', ['scraper_type', 'status', 'created_at'])

    # Add indexes to job_results table
    op.create_index('ix_job_results_status_code', 'job_results', ['status_code'])
    op.create_index('ix_job_results_response_time', 'job_results', ['response_time'])
    op.create_index('ix_job_results_content_type', 'job_results', ['content_type'])
    op.create_index('ix_job_results_created_at', 'job_results', ['created_at'])

    # Composite indexes for job_results
    op.create_index('idx_job_result_task_created', 'job_results', ['task_id', 'created_at'])
    op.create_index('idx_job_result_status_time', 'job_results', ['status_code', 'response_time'])
    op.create_index('idx_job_result_job_created', 'job_results', ['job_id', 'created_at'])

    # Add foreign key constraint if not exists
    try:
        op.create_foreign_key('fk_job_results_job_id', 'job_results', 'jobs', ['job_id'], ['id'])
    except:
        pass  # Foreign key might already exist


def downgrade() -> None:
    """Remove performance indexes."""
    # Drop composite indexes
    op.drop_index('idx_job_result_job_created', 'job_results')
    op.drop_index('idx_job_result_status_time', 'job_results')
    op.drop_index('idx_job_result_task_created', 'job_results')
    op.drop_index('idx_job_scraper_status_created', 'jobs')
    op.drop_index('idx_job_url_status', 'jobs')
    op.drop_index('idx_job_priority_created', 'jobs')
    op.drop_index('idx_job_created_status', 'jobs')
    op.drop_index('idx_job_status_scraper_type', 'jobs')
    op.drop_index('idx_job_status_created', 'jobs')

    # Drop single column indexes
    op.drop_index('ix_job_results_created_at', 'job_results')
    op.drop_index('ix_job_results_content_type', 'job_results')
    op.drop_index('ix_job_results_response_time', 'job_results')
    op.drop_index('ix_job_results_status_code', 'job_results')
    op.drop_index('ix_jobs_completed_at', 'jobs')
    op.drop_index('ix_jobs_started_at', 'jobs')
    op.drop_index('ix_jobs_created_at', 'jobs')
    op.drop_index('ix_jobs_priority', 'jobs')
    op.drop_index('ix_jobs_url', 'jobs')
    op.drop_index('ix_jobs_scraper_type', 'jobs')
    op.drop_index('ix_jobs_status', 'jobs')

    # Drop foreign key constraint if exists
    try:
        op.drop_constraint('fk_job_results_job_id', 'job_results', type_='foreignkey')
    except:
        pass  # Foreign key might not exist
