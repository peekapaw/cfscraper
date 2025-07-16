"""
Async job execution and management utilities with optimization.

This module provides:
- Async job execution with connection pooling
- Optimized background job processing
- Async context managers for resource management
- Proper async exception handling
- Performance monitoring and metrics
"""

import asyncio
import logging
import time
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from contextlib import asynccontextmanager
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from prometheus_client import Counter, Histogram, Gauge

from app.models.job import Job, JobStatus, JobResult, ScraperType
from app.scrapers.factory import create_scraper
from app.utils.queue import JobQueue
from app.database.connection import connection_manager
from app.core.config import settings
from app.utils.webhooks import send_job_completed_webhook, send_job_failed_webhook
from app.utils.async_http import http_manager

logger = logging.getLogger(__name__)

# Job execution metrics
jobs_executed_total = Counter('jobs_executed_total', 'Total jobs executed', ['status', 'scraper_type'])
job_execution_duration = Histogram('job_execution_duration_seconds', 'Job execution duration', ['scraper_type'])
active_jobs = Gauge('active_jobs', 'Currently active jobs')
job_queue_size = Gauge('job_queue_size', 'Current job queue size')
job_errors_total = Counter('job_errors_total', 'Total job errors', ['error_type'])


class AsyncJobExecutor:
    """Async job executor with optimized resource management"""
    
    def __init__(self, job_queue: JobQueue):
        self.job_queue = job_queue
        self.running_jobs: Dict[str, asyncio.Task] = {}
        self.max_concurrent_jobs = settings.max_concurrent_jobs
        self.job_timeout = settings.job_timeout
        self._semaphore = asyncio.Semaphore(self.max_concurrent_jobs)
        self._shutdown_event = asyncio.Event()
    
    @asynccontextmanager
    async def get_db_session(self) -> AsyncSession:
        """Get async database session with proper error handling"""
        async with connection_manager.get_async_session() as session:
            try:
                yield session
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error(f"Database session error: {e}")
                raise
    
    async def execute_job(self, job_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a single scraping job asynchronously
        
        Args:
            job_info: Job information from the queue
            
        Returns:
            Dictionary with execution results
        """
        task_id = job_info['task_id']
        job_data = job_info['data']
        
        start_time = time.time()
        scraper_type = job_data.get('scraper_type', ScraperType.CLOUDSCRAPER)
        
        # Update active jobs metric
        active_jobs.inc()
        
        try:
            async with self.get_db_session() as db:
                # Create or update job record in database
                job_stmt = select(Job).where(Job.task_id == task_id)
                result = await db.execute(job_stmt)
                job = result.scalar_one_or_none()
                
                if not job:
                    job = Job(
                        task_id=task_id,
                        status=JobStatus.RUNNING,
                        url=job_data['url'],
                        method=job_data.get('method', 'GET'),
                        headers=job_data.get('headers', {}),
                        data=job_data.get('data', {}),
                        params=job_data.get('params', {}),
                        scraper_type=scraper_type,
                        tags=job_data.get('tags', []),
                        priority=job_data.get('priority', 0),
                        max_retries=job_data.get('max_retries', 3),
                        started_at=datetime.now(timezone.utc)
                    )
                    db.add(job)
                else:
                    # Update existing job
                    update_stmt = update(Job).where(Job.task_id == task_id).values(
                        status=JobStatus.RUNNING,
                        started_at=datetime.now(timezone.utc)
                    )
                    await db.execute(update_stmt)
                
                await db.commit()
            
            # Execute scraping with timeout
            try:
                result = await asyncio.wait_for(
                    self._execute_scraping(job_data),
                    timeout=self.job_timeout
                )
                
                # Update job with success
                await self._update_job_success(task_id, result)
                
                # Send webhook notification
                asyncio.create_task(
                    send_job_completed_webhook(task_id, result)
                )
                
                # Record metrics
                duration = time.time() - start_time
                job_execution_duration.labels(scraper_type=scraper_type).observe(duration)
                jobs_executed_total.labels(status='completed', scraper_type=scraper_type).inc()
                
                return {
                    'task_id': task_id,
                    'status': 'completed',
                    'result': result,
                    'execution_time': duration
                }
                
            except asyncio.TimeoutError:
                error_msg = f"Job timed out after {self.job_timeout} seconds"
                await self._update_job_error(task_id, error_msg)
                job_errors_total.labels(error_type='timeout').inc()
                jobs_executed_total.labels(status='failed', scraper_type=scraper_type).inc()
                
                asyncio.create_task(
                    send_job_failed_webhook(task_id, error_msg)
                )
                
                return {
                    'task_id': task_id,
                    'status': 'failed',
                    'error': error_msg
                }
                
        except Exception as e:
            error_msg = f"Job execution failed: {str(e)}"
            logger.error(f"Job {task_id} failed: {e}")
            
            await self._update_job_error(task_id, error_msg)
            job_errors_total.labels(error_type='execution').inc()
            jobs_executed_total.labels(status='failed', scraper_type=scraper_type).inc()
            
            asyncio.create_task(
                send_job_failed_webhook(task_id, error_msg)
            )
            
            return {
                'task_id': task_id,
                'status': 'failed',
                'error': error_msg
            }
        
        finally:
            active_jobs.dec()
    
    async def _execute_scraping(self, job_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the actual scraping operation"""
        scraper_type = job_data.get('scraper_type', ScraperType.CLOUDSCRAPER)
        
        # Create scraper instance
        scraper = await create_scraper(scraper_type)
        
        try:
            # Execute scraping
            result = await scraper.scrape(
                url=job_data['url'],
                method=job_data.get('method', 'GET'),
                headers=job_data.get('headers'),
                data=job_data.get('data'),
                params=job_data.get('params')
            )
            
            return {
                'status_code': result.status_code,
                'content': result.content,
                'headers': dict(result.headers),
                'response_time': result.response_time,
                'success': result.success,
                'error': result.error
            }
            
        finally:
            await scraper.close()
    
    async def _update_job_success(self, task_id: str, result: Dict[str, Any]):
        """Update job with successful result"""
        async with self.get_db_session() as db:
            update_stmt = update(Job).where(Job.task_id == task_id).values(
                status=JobStatus.COMPLETED,
                completed_at=datetime.now(timezone.utc),
                result=result,
                progress=100
            )
            await db.execute(update_stmt)
            
            # Create job result record
            job_result = JobResult(
                task_id=task_id,
                status_code=result.get('status_code'),
                response_headers=result.get('headers'),
                response_content=result.get('content'),
                response_time=result.get('response_time'),
                content_length=len(result.get('content', '')),
                content_type=result.get('headers', {}).get('content-type')
            )
            db.add(job_result)
    
    async def _update_job_error(self, task_id: str, error_msg: str):
        """Update job with error"""
        async with self.get_db_session() as db:
            update_stmt = update(Job).where(Job.task_id == task_id).values(
                status=JobStatus.FAILED,
                completed_at=datetime.now(timezone.utc),
                error_message=error_msg
            )
            await db.execute(update_stmt)
    
    async def process_jobs(self):
        """Main job processing loop with async optimization"""
        logger.info(f"Starting job processor with max {self.max_concurrent_jobs} concurrent jobs")
        
        while not self._shutdown_event.is_set():
            try:
                # Check if we can process more jobs
                if len(self.running_jobs) >= self.max_concurrent_jobs:
                    await asyncio.sleep(0.1)
                    continue
                
                # Get next job from queue
                job_info = await self.job_queue.dequeue()
                if not job_info:
                    await asyncio.sleep(1)
                    continue
                
                # Update queue size metric
                queue_size = await self.job_queue.size()
                job_queue_size.set(queue_size)
                
                # Execute job with semaphore for concurrency control
                task = asyncio.create_task(
                    self._execute_job_with_semaphore(job_info)
                )
                
                task_id = job_info['task_id']
                self.running_jobs[task_id] = task
                
                # Clean up completed tasks
                await self._cleanup_completed_tasks()
                
            except Exception as e:
                logger.error(f"Error in job processing loop: {e}")
                await asyncio.sleep(5)
    
    async def _execute_job_with_semaphore(self, job_info: Dict[str, Any]):
        """Execute job with semaphore for concurrency control"""
        async with self._semaphore:
            try:
                return await self.execute_job(job_info)
            finally:
                task_id = job_info['task_id']
                self.running_jobs.pop(task_id, None)
    
    async def _cleanup_completed_tasks(self):
        """Clean up completed tasks"""
        completed_tasks = []
        for task_id, task in self.running_jobs.items():
            if task.done():
                completed_tasks.append(task_id)
        
        for task_id in completed_tasks:
            self.running_jobs.pop(task_id, None)
    
    async def shutdown(self):
        """Gracefully shutdown the job executor"""
        logger.info("Shutting down job executor...")
        self._shutdown_event.set()
        
        # Wait for running jobs to complete
        if self.running_jobs:
            logger.info(f"Waiting for {len(self.running_jobs)} running jobs to complete...")
            await asyncio.gather(*self.running_jobs.values(), return_exceptions=True)
        
        logger.info("Job executor shutdown complete")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get executor statistics"""
        return {
            'running_jobs': len(self.running_jobs),
            'max_concurrent_jobs': self.max_concurrent_jobs,
            'job_timeout': self.job_timeout,
        }
