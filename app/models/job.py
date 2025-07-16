from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any

from sqlalchemy import Column, Integer, String, DateTime, Text, JSON, Boolean, Index, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.core.database import Base


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScraperType(str, Enum):
    CLOUDSCRAPER = "cloudscraper"
    SELENIUM = "selenium"


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String, unique=True, index=True)
    status = Column(String, default=JobStatus.QUEUED, index=True)  # Index for status queries
    scraper_type = Column(String, default=ScraperType.CLOUDSCRAPER, index=True)  # Index for scraper type queries

    # Job configuration
    url = Column(String, nullable=False, index=True)  # Index for URL searches
    method = Column(String, default="GET")
    headers = Column(JSON, default=dict)
    data = Column(JSON, default=dict)
    params = Column(JSON, default=dict)

    # Job metadata
    tags = Column(JSON, default=list)
    priority = Column(Integer, default=0, index=True)  # Index for priority-based queries
    created_at = Column(DateTime, default=func.now(), index=True)  # Index for date range queries
    started_at = Column(DateTime, nullable=True, index=True)  # Index for execution time queries
    completed_at = Column(DateTime, nullable=True, index=True)  # Index for completion time queries

    # Results
    result = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)

    # Retry configuration
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)

    # Progress tracking
    progress = Column(Integer, default=0)  # 0-100
    progress_message = Column(String, nullable=True)

    # Relationship to job results
    job_results = relationship("JobResult", back_populates="job", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Job(id={self.id}, task_id='{self.task_id}', status='{self.status}')>"


# Composite indexes for common query patterns
Index('idx_job_status_created', Job.status, Job.created_at)
Index('idx_job_status_scraper_type', Job.status, Job.scraper_type)
Index('idx_job_created_status', Job.created_at, Job.status)
Index('idx_job_priority_created', Job.priority.desc(), Job.created_at)
Index('idx_job_url_status', Job.url, Job.status)
Index('idx_job_scraper_status_created', Job.scraper_type, Job.status, Job.created_at)


class JobResult(Base):
    __tablename__ = "job_results"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey('jobs.id'), index=True)
    task_id = Column(String, index=True)

    # Response data
    status_code = Column(Integer, nullable=True, index=True)  # Index for status code queries
    response_headers = Column(JSON, nullable=True)
    response_content = Column(Text, nullable=True)

    # Metadata
    response_time = Column(Integer, nullable=True, index=True)  # Index for performance queries
    content_length = Column(Integer, nullable=True)
    content_type = Column(String, nullable=True, index=True)  # Index for content type queries

    # Timestamps
    created_at = Column(DateTime, default=func.now(), index=True)  # Index for date queries

    # Relationship to job
    job = relationship("Job", back_populates="job_results")

    def __repr__(self):
        return f"<JobResult(id={self.id}, job_id={self.job_id}, status_code={self.status_code})>"


# Composite indexes for JobResult
Index('idx_job_result_task_created', JobResult.task_id, JobResult.created_at)
Index('idx_job_result_status_time', JobResult.status_code, JobResult.response_time)
Index('idx_job_result_job_created', JobResult.job_id, JobResult.created_at)