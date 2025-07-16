"""
Job management endpoints with async optimization
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, or_, desc, asc, select, func
from typing import Optional, List
from datetime import datetime, timedelta, timezone
import uuid
import asyncio

from app.core.database import get_db, get_async_db_dependency
from app.models.job import Job, JobStatus, ScraperType
from app.models.requests import JobSearchRequest
from app.models.responses import JobListResponse, JobStatusResponse, JobResult
from .common import (
    get_job_queue,
    get_job_executor,
    build_job_result,
    build_job_status_response,
    handle_route_exception
)

router = APIRouter()


@router.get("/", response_model=JobListResponse)
async def list_jobs(
    # Filtering parameters
    status: Optional[List[JobStatus]] = Query(None, description="Filter by job status"),
    scraper_type: Optional[List[ScraperType]] = Query(None, description="Filter by scraper type"),
    url_contains: Optional[str] = Query(None, description="Filter by URL containing text"),
    tags: Optional[List[str]] = Query(None, description="Filter by tags"),
    date_from: Optional[datetime] = Query(None, description="Filter jobs created after this date"),
    date_to: Optional[datetime] = Query(None, description="Filter jobs created before this date"),

    # Pagination
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page (1-100)"),

    # Sorting
    sort_by: str = Query("created_at", description="Sort field"),
    sort_order: str = Query("desc", description="Sort order (asc, desc)"),

    db: AsyncSession = Depends(get_async_db_dependency)
):
    """
    List jobs with optional filtering and pagination
    
    Retrieves a list of jobs with support for filtering by status, scraper type,
    URL, tags, and date range. Results are paginated and can be sorted.
    
    Args:
        status: Filter by job status (can specify multiple)
        scraper_type: Filter by scraper type (can specify multiple)
        url_contains: Filter by URL containing specific text
        tags: Filter by job tags (can specify multiple)
        date_from: Filter jobs created after this date
        date_to: Filter jobs created before this date
        page: Page number for pagination
        page_size: Number of items per page
        sort_by: Field to sort by
        sort_order: Sort order (asc or desc)
        db: Database session
        
    Returns:
        JobListResponse with paginated job list
    """
    try:
        # Build async query
        query = select(Job)

        # Apply filters
        if status:
            query = query.where(Job.status.in_(status))

        if scraper_type:
            query = query.where(Job.scraper_type.in_(scraper_type))

        if url_contains:
            query = query.where(Job.url.contains(url_contains))

        if date_from:
            query = query.where(Job.created_at >= date_from)

        if date_to:
            query = query.where(Job.created_at <= date_to)

        # Apply sorting
        sort_field = getattr(Job, sort_by, Job.created_at)
        if sort_order.lower() == "asc":
            query = query.order_by(asc(sort_field))
        else:
            query = query.order_by(desc(sort_field))

        # Get total count and jobs concurrently
        count_query = select(func.count()).select_from(query.subquery())

        # Execute count and paginated query concurrently
        offset = (page - 1) * page_size
        paginated_query = query.offset(offset).limit(page_size)

        # Use asyncio.gather for concurrent execution
        count_result, jobs_result = await asyncio.gather(
            db.execute(count_query),
            db.execute(paginated_query)
        )

        total = count_result.scalar()
        jobs = jobs_result.scalars().all()

        # Convert to response format
        job_responses = []
        for job in jobs:
            job_response = build_job_status_response(job)
            job_responses.append(job_response)

        # Calculate pagination info
        total_pages = (total + page_size - 1) // page_size
        has_next = page < total_pages
        has_previous = page > 1

        return JobListResponse(
            jobs=job_responses,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            has_next=has_next,
            has_previous=has_previous
        )

    except Exception as e:
        raise handle_route_exception(e, "list jobs")


@router.post("/search", response_model=JobListResponse)
async def search_jobs(
    request: JobSearchRequest,
    db: Session = Depends(get_db)
):
    """
    Search jobs with advanced filtering
    
    Performs advanced search across jobs with text-based queries and
    complex filtering options.
    
    Args:
        request: Job search request with filtering criteria
        db: Database session
        
    Returns:
        JobListResponse with search results
    """
    try:
        # Build query
        query = db.query(Job)
        
        # Apply text search
        if request.query:
            query = query.filter(
                or_(
                    Job.url.contains(request.query),
                    Job.task_id.contains(request.query),
                    Job.progress_message.contains(request.query)
                )
            )
        
        # Apply filters
        if request.status:
            query = query.filter(Job.status.in_(request.status))
        
        if request.scraper_type:
            query = query.filter(Job.scraper_type.in_(request.scraper_type))
        
        if request.date_from:
            try:
                date_from = datetime.fromisoformat(request.date_from.replace('Z', '+00:00'))
                query = query.filter(Job.created_at >= date_from)
            except ValueError:
                raise HTTPException(
                    status_code=400, 
                    detail="Invalid date_from format. Use ISO format."
                )
        
        if request.date_to:
            try:
                date_to = datetime.fromisoformat(request.date_to.replace('Z', '+00:00'))
                query = query.filter(Job.created_at <= date_to)
            except ValueError:
                raise HTTPException(
                    status_code=400, 
                    detail="Invalid date_to format. Use ISO format."
                )
        
        # Apply sorting
        sort_field = getattr(Job, request.sort_by, Job.created_at)
        if request.sort_order == "asc":
            query = query.order_by(asc(sort_field))
        else:
            query = query.order_by(desc(sort_field))
        
        # Get total count
        total = query.count()
        
        # Apply pagination
        offset = (request.page - 1) * request.page_size
        jobs = query.offset(offset).limit(request.page_size).all()
        
        # Convert to response format
        job_responses = []
        for job in jobs:
            job_response = build_job_status_response(job)
            job_responses.append(job_response)
        
        # Calculate pagination info
        total_pages = (total + request.page_size - 1) // request.page_size
        has_next = request.page < total_pages
        has_previous = request.page > 1
        
        return JobListResponse(
            jobs=job_responses,
            total=total,
            page=request.page,
            page_size=request.page_size,
            total_pages=total_pages,
            has_next=has_next,
            has_previous=has_previous
        )
        
    except Exception as e:
        raise handle_route_exception(e, "search jobs")


@router.post("/bulk/cancel")
async def cancel_bulk_jobs(
    job_ids: List[str],
    db: Session = Depends(get_db)
):
    """
    Cancel multiple jobs in bulk
    
    Cancels multiple jobs at once. Only queued and running jobs can be cancelled.
    
    Args:
        job_ids: List of job IDs to cancel
        db: Database session
        
    Returns:
        Summary of cancelled jobs
    """
    try:
        if len(job_ids) > 100:
            raise HTTPException(
                status_code=400, 
                detail="Maximum 100 jobs can be cancelled at once"
            )
        
        # Get jobs from database
        jobs = db.query(Job).filter(Job.task_id.in_(job_ids)).all()
        
        if not jobs:
            raise HTTPException(status_code=404, detail="No jobs found")
        
        cancelled_jobs = []
        failed_jobs = []
        
        for job in jobs:
            if job.status in [JobStatus.QUEUED, JobStatus.RUNNING]:
                try:
                    # Update job status in queue
                    await get_job_queue().update_job_status(job.task_id, JobStatus.CANCELLED)
                    
                    # Update job in database
                    job.status = JobStatus.CANCELLED
                    job.completed_at = datetime.now(timezone.utc)
                    cancelled_jobs.append(job.task_id)
                    
                except Exception as e:
                    failed_jobs.append({
                        'job_id': job.task_id,
                        'error': str(e)
                    })
            else:
                failed_jobs.append({
                    'job_id': job.task_id,
                    'error': f'Cannot cancel job with status: {job.status}'
                })
        
        db.commit()
        
        return {
            'message': f'Bulk cancel operation completed',
            'cancelled_jobs': cancelled_jobs,
            'failed_jobs': failed_jobs,
            'total_requested': len(job_ids),
            'total_cancelled': len(cancelled_jobs),
            'total_failed': len(failed_jobs)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to cancel bulk jobs: {str(e)}"
        )


@router.delete("/bulk")
async def delete_bulk_jobs(
    job_ids: List[str],
    force: bool = Query(False, description="Force delete even if running"),
    db: Session = Depends(get_db)
):
    """
    Delete multiple jobs in bulk
    
    Deletes multiple jobs from the database. By default, only completed, failed,
    or cancelled jobs can be deleted unless force is enabled.
    
    Args:
        job_ids: List of job IDs to delete
        force: Force delete even if jobs are running
        db: Database session
        
    Returns:
        Summary of deleted jobs
    """
    try:
        if len(job_ids) > 100:
            raise HTTPException(
                status_code=400, 
                detail="Maximum 100 jobs can be deleted at once"
            )
        
        # Get jobs from database
        jobs = db.query(Job).filter(Job.task_id.in_(job_ids)).all()
        
        if not jobs:
            raise HTTPException(status_code=404, detail="No jobs found")
        
        deleted_jobs = []
        failed_jobs = []
        
        for job in jobs:
            if force or job.status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]:
                try:
                    # Remove from queue if still there
                    await get_job_queue().remove_job(job.task_id)
                    
                    # Delete from database
                    db.delete(job)
                    deleted_jobs.append(job.task_id)
                    
                except Exception as e:
                    failed_jobs.append({
                        'job_id': job.task_id,
                        'error': str(e)
                    })
            else:
                failed_jobs.append({
                    'job_id': job.task_id,
                    'error': f'Cannot delete job with status: {job.status}. Use force=true to override.'
                })
        
        db.commit()
        
        return {
            'message': f'Bulk delete operation completed',
            'deleted_jobs': deleted_jobs,
            'failed_jobs': failed_jobs,
            'total_requested': len(job_ids),
            'total_deleted': len(deleted_jobs),
            'total_failed': len(failed_jobs)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to delete bulk jobs: {str(e)}"
        )


@router.get("/stats")
async def get_job_stats(
    days: int = Query(7, ge=1, le=365, description="Number of days to include in stats"),
    db: Session = Depends(get_db)
):
    """
    Get job statistics
    
    Provides statistics about jobs over a specified time period.
    
    Args:
        days: Number of days to include in statistics
        db: Database session
        
    Returns:
        Job statistics
    """
    try:
        # Calculate date range
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)
        
        # Get jobs in date range
        jobs = db.query(Job).filter(
            Job.created_at >= start_date,
            Job.created_at <= end_date
        ).all()
        
        # Calculate statistics
        stats = {
            'total_jobs': len(jobs),
            'status_breakdown': {},
            'scraper_type_breakdown': {},
            'daily_stats': {},
            'average_response_time': 0,
            'success_rate': 0
        }
        
        # Status breakdown
        for status in JobStatus:
            stats['status_breakdown'][status.value] = len([j for j in jobs if j.status == status])
        
        # Scraper type breakdown
        for scraper_type in ScraperType:
            stats['scraper_type_breakdown'][scraper_type.value] = len([j for j in jobs if j.scraper_type == scraper_type])
        
        # Daily stats
        for i in range(days):
            date = start_date + timedelta(days=i)
            date_str = date.strftime('%Y-%m-%d')
            day_jobs = [j for j in jobs if j.created_at.date() == date.date()]
            stats['daily_stats'][date_str] = len(day_jobs)
        
        # Calculate averages
        completed_jobs = [j for j in jobs if j.status == JobStatus.COMPLETED and j.result]
        if completed_jobs:
            response_times = [j.result.get('response_time', 0) for j in completed_jobs if j.result.get('response_time')]
            if response_times:
                stats['average_response_time'] = sum(response_times) / len(response_times)
            
            stats['success_rate'] = len(completed_jobs) / len(jobs) if jobs else 0
        
        return stats
        
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to get job statistics: {str(e)}"
        )


@router.get("/queue/status")
async def get_queue_status():
    """
    Get current queue status
    
    Returns information about the current state of the job queue.
    
    Returns:
        Queue status information
    """
    try:
        queue_size = await get_job_queue().get_queue_size()
        
        executor = get_job_executor()
        if executor:
            running_jobs = executor.get_running_jobs()
            max_concurrent = executor.max_concurrent_jobs
        else:
            running_jobs = []
            max_concurrent = 10  # Default value
        
        return {
            'queue_size': queue_size,
            'running_jobs': len(running_jobs),
            'max_concurrent_jobs': max_concurrent,
            'running_job_ids': running_jobs,
            'queue_type': 'in-memory' if hasattr(get_job_queue(), 'queue') else 'redis'
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to get queue status: {str(e)}"
        )


@router.post("/queue/clear")
async def clear_queue():
    """
    Clear all jobs from the queue
    
    Removes all pending jobs from the queue. Running jobs are not affected.
    
    Returns:
        Success message
    """
    try:
        await get_job_queue().clear_queue()
        return {'message': 'Queue cleared successfully'}
        
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to clear queue: {str(e)}"
        )