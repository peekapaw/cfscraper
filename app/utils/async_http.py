"""
Async HTTP client manager with connection pooling and optimization.

This module provides:
- Async HTTP client with connection pooling
- Request/response optimization
- Retry logic with exponential backoff
- Connection monitoring and metrics
- Proper async context management
"""

import logging
import asyncio
import time
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, Union, List
from dataclasses import dataclass

import httpx
from prometheus_client import Counter, Histogram, Gauge

from app.core.config import settings

logger = logging.getLogger(__name__)

# HTTP client metrics
http_requests_total = Counter('http_requests_total', 'Total HTTP requests', ['method', 'status_code'])
http_request_duration = Histogram('http_request_duration_seconds', 'HTTP request duration', ['method'])
http_connection_pool_size = Gauge('http_connection_pool_size', 'HTTP connection pool size')
http_connection_errors = Counter('http_connection_errors_total', 'HTTP connection errors')
http_retries_total = Counter('http_retries_total', 'Total HTTP retries', ['reason'])


@dataclass
class HttpClientConfig:
    """Configuration for HTTP client"""
    timeout: float = 30.0
    max_connections: int = 100
    max_keepalive_connections: int = 20
    keepalive_expiry: float = 5.0
    max_retries: int = 3
    retry_backoff_factor: float = 0.5
    verify_ssl: bool = True
    follow_redirects: bool = True
    
    @classmethod
    def from_settings(cls) -> 'HttpClientConfig':
        """Create configuration from application settings"""
        return cls(
            timeout=getattr(settings, 'http_timeout', 30.0),
            max_connections=getattr(settings, 'http_max_connections', 100),
            max_keepalive_connections=getattr(settings, 'http_max_keepalive_connections', 20),
            keepalive_expiry=getattr(settings, 'http_keepalive_expiry', 5.0),
            max_retries=getattr(settings, 'http_max_retries', 3),
            retry_backoff_factor=getattr(settings, 'http_retry_backoff_factor', 0.5),
            verify_ssl=getattr(settings, 'http_verify_ssl', True),
            follow_redirects=getattr(settings, 'http_follow_redirects', True),
        )


class AsyncHttpManager:
    """Manages async HTTP clients with connection pooling"""
    
    def __init__(self):
        self.config = HttpClientConfig.from_settings()
        self.client: Optional[httpx.AsyncClient] = None
        self._initialized = False
    
    async def initialize(self):
        """Initialize HTTP client with optimized settings"""
        if self._initialized:
            return
        
        # Create connection limits
        limits = httpx.Limits(
            max_connections=self.config.max_connections,
            max_keepalive_connections=self.config.max_keepalive_connections,
            keepalive_expiry=self.config.keepalive_expiry,
        )
        
        # Create timeout configuration
        timeout = httpx.Timeout(
            connect=self.config.timeout,
            read=self.config.timeout,
            write=self.config.timeout,
            pool=self.config.timeout,
        )
        
        # Create HTTP client
        self.client = httpx.AsyncClient(
            limits=limits,
            timeout=timeout,
            verify=self.config.verify_ssl,
            follow_redirects=self.config.follow_redirects,
        )
        
        self._initialized = True
        
        # Update metrics
        http_connection_pool_size.set(self.config.max_connections)
        
        logger.info(
            f"HTTP client initialized with "
            f"max_connections={self.config.max_connections}, "
            f"timeout={self.config.timeout}s"
        )
    
    @asynccontextmanager
    async def get_client(self) -> httpx.AsyncClient:
        """Get HTTP client with automatic initialization"""
        if not self._initialized:
            await self.initialize()
        
        yield self.client
    
    async def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Union[Dict[str, Any], str, bytes]] = None,
        json: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
    ) -> httpx.Response:
        """Make HTTP request with retry logic and monitoring"""
        if not self._initialized:
            await self.initialize()
        
        retries = retries if retries is not None else self.config.max_retries
        timeout = timeout or self.config.timeout
        
        last_exception = None
        
        for attempt in range(retries + 1):
            start_time = time.time()
            
            try:
                # Create timeout for this request
                request_timeout = httpx.Timeout(timeout)
                
                response = await self.client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    data=data,
                    json=json,
                    timeout=request_timeout,
                )
                
                # Record metrics
                duration = time.time() - start_time
                http_request_duration.labels(method=method.upper()).observe(duration)
                http_requests_total.labels(
                    method=method.upper(),
                    status_code=response.status_code
                ).inc()
                
                return response
                
            except (httpx.TimeoutException, httpx.ConnectTimeout) as e:
                last_exception = e
                http_connection_errors.inc()
                http_retries_total.labels(reason='timeout').inc()
                
                if attempt < retries:
                    backoff_time = self.config.retry_backoff_factor * (2 ** attempt)
                    logger.warning(
                        f"HTTP request timeout (attempt {attempt + 1}/{retries + 1}), "
                        f"retrying in {backoff_time:.2f}s: {url}"
                    )
                    await asyncio.sleep(backoff_time)
                    continue
                
            except (httpx.ConnectError, httpx.NetworkError) as e:
                last_exception = e
                http_connection_errors.inc()
                http_retries_total.labels(reason='network').inc()
                
                if attempt < retries:
                    backoff_time = self.config.retry_backoff_factor * (2 ** attempt)
                    logger.warning(
                        f"HTTP network error (attempt {attempt + 1}/{retries + 1}), "
                        f"retrying in {backoff_time:.2f}s: {url}"
                    )
                    await asyncio.sleep(backoff_time)
                    continue
                
            except Exception as e:
                last_exception = e
                http_connection_errors.inc()
                logger.error(f"HTTP request failed: {e}")
                break
        
        # If we get here, all retries failed
        logger.error(f"HTTP request failed after {retries + 1} attempts: {url}")
        raise last_exception
    
    async def get(self, url: str, **kwargs) -> httpx.Response:
        """Make GET request"""
        return await self.request("GET", url, **kwargs)
    
    async def post(self, url: str, **kwargs) -> httpx.Response:
        """Make POST request"""
        return await self.request("POST", url, **kwargs)
    
    async def put(self, url: str, **kwargs) -> httpx.Response:
        """Make PUT request"""
        return await self.request("PUT", url, **kwargs)
    
    async def delete(self, url: str, **kwargs) -> httpx.Response:
        """Make DELETE request"""
        return await self.request("DELETE", url, **kwargs)
    
    async def patch(self, url: str, **kwargs) -> httpx.Response:
        """Make PATCH request"""
        return await self.request("PATCH", url, **kwargs)
    
    async def head(self, url: str, **kwargs) -> httpx.Response:
        """Make HEAD request"""
        return await self.request("HEAD", url, **kwargs)
    
    async def batch_requests(
        self,
        requests: List[Dict[str, Any]],
        max_concurrent: int = 10
    ) -> List[httpx.Response]:
        """Execute multiple HTTP requests concurrently"""
        if not self._initialized:
            await self.initialize()
        
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def make_request(request_config: Dict[str, Any]) -> httpx.Response:
            async with semaphore:
                return await self.request(**request_config)
        
        tasks = [make_request(req) for req in requests]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter out exceptions and log them
        valid_responses = []
        for i, response in enumerate(responses):
            if isinstance(response, Exception):
                logger.error(f"Batch request {i} failed: {response}")
            else:
                valid_responses.append(response)
        
        return valid_responses
    
    def get_stats(self) -> Dict[str, Any]:
        """Get HTTP client statistics"""
        return {
            "max_connections": self.config.max_connections,
            "max_keepalive_connections": self.config.max_keepalive_connections,
            "keepalive_expiry": self.config.keepalive_expiry,
            "timeout": self.config.timeout,
            "max_retries": self.config.max_retries,
            "initialized": self._initialized,
        }
    
    async def close(self):
        """Close HTTP client and cleanup"""
        if self.client:
            await self.client.aclose()
            logger.info("HTTP client closed")
        
        self._initialized = False


# Global HTTP manager instance
http_manager = AsyncHttpManager()
