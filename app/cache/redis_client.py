"""
Optimized Redis client with connection pooling and monitoring.

This module provides:
- Connection pooling for better performance
- Connection multiplexing and pipeline operations
- Redis cluster support
- Connection monitoring and metrics
- Automatic failover and retry logic
"""

import logging
import asyncio
import time
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List, Union
from dataclasses import dataclass
from urllib.parse import urlparse

import aioredis
from aioredis import ConnectionPool, Redis
from prometheus_client import Counter, Histogram, Gauge, Info

from app.core.config import settings

logger = logging.getLogger(__name__)

# Metrics for Redis connection monitoring
redis_connections_created = Counter('redis_connections_created_total', 'Total Redis connections created')
redis_connections_closed = Counter('redis_connections_closed_total', 'Total Redis connections closed')
redis_connection_pool_size = Gauge('redis_connection_pool_size', 'Current Redis connection pool size')
redis_connection_pool_available = Gauge('redis_connection_pool_available', 'Available Redis connections in pool')
redis_operation_duration = Histogram('redis_operation_duration_seconds', 'Redis operation duration', ['operation'])
redis_connection_errors = Counter('redis_connection_errors_total', 'Redis connection errors')
redis_pipeline_operations = Counter('redis_pipeline_operations_total', 'Redis pipeline operations')
redis_cluster_info = Info('redis_cluster_configuration', 'Redis cluster configuration')


@dataclass
class RedisPoolConfig:
    """Configuration for Redis connection pooling"""
    max_connections: int = 50
    retry_on_timeout: bool = True
    health_check_interval: int = 30
    socket_timeout: float = 5.0
    socket_connect_timeout: float = 5.0
    socket_keepalive: bool = True
    socket_keepalive_options: Dict = None
    connection_pool_class: type = ConnectionPool
    
    @classmethod
    def from_settings(cls) -> 'RedisPoolConfig':
        """Create configuration from application settings"""
        return cls(
            max_connections=getattr(settings, 'redis_max_connections', 50),
            retry_on_timeout=getattr(settings, 'redis_retry_on_timeout', True),
            health_check_interval=getattr(settings, 'redis_health_check_interval', 30),
            socket_timeout=getattr(settings, 'redis_socket_timeout', 5.0),
            socket_connect_timeout=getattr(settings, 'redis_socket_connect_timeout', 5.0),
            socket_keepalive=getattr(settings, 'redis_socket_keepalive', True),
            socket_keepalive_options=getattr(settings, 'redis_socket_keepalive_options', {}),
        )


class RedisConnectionManager:
    """Manages Redis connections with optimized pooling"""
    
    def __init__(self):
        self.pool: Optional[ConnectionPool] = None
        self.client: Optional[Redis] = None
        self.config = RedisPoolConfig.from_settings()
        self._initialized = False
        self._health_check_task: Optional[asyncio.Task] = None
        self._cluster_mode = False
        self._cluster_nodes = []
    
    async def initialize(self):
        """Initialize Redis connection pool"""
        if self._initialized:
            return
        
        await self._create_connection_pool()
        await self._setup_monitoring()
        self._start_health_check()
        self._initialized = True
        
        logger.info(
            f"Redis connection manager initialized with "
            f"max_connections={self.config.max_connections}, "
            f"cluster_mode={self._cluster_mode}"
        )
    
    async def _create_connection_pool(self):
        """Create Redis connection pool"""
        redis_url = settings.redis_url
        
        # Parse Redis URL to check for cluster configuration
        parsed_url = urlparse(redis_url)
        
        # Check if cluster mode is configured
        cluster_urls = getattr(settings, 'redis_cluster_urls', [])
        if cluster_urls:
            await self._create_cluster_pool(cluster_urls)
        else:
            await self._create_single_pool(redis_url)
    
    async def _create_single_pool(self, redis_url: str):
        """Create single Redis instance connection pool"""
        self.pool = ConnectionPool.from_url(
            redis_url,
            max_connections=self.config.max_connections,
            retry_on_timeout=self.config.retry_on_timeout,
            socket_timeout=self.config.socket_timeout,
            socket_connect_timeout=self.config.socket_connect_timeout,
            socket_keepalive=self.config.socket_keepalive,
            socket_keepalive_options=self.config.socket_keepalive_options or {},
        )
        
        self.client = Redis(connection_pool=self.pool)
        
        # Test connection
        await self.client.ping()
        logger.info(f"Connected to Redis at {redis_url}")
    
    async def _create_cluster_pool(self, cluster_urls: List[str]):
        """Create Redis cluster connection pool"""
        self._cluster_mode = True
        self._cluster_nodes = cluster_urls
        
        # For cluster mode, we'll use RedisCluster when available
        # For now, implement failover logic with multiple single connections
        for url in cluster_urls:
            try:
                await self._create_single_pool(url)
                logger.info(f"Connected to Redis cluster node: {url}")
                break
            except Exception as e:
                logger.warning(f"Failed to connect to Redis cluster node {url}: {e}")
                continue
        
        if not self.client:
            raise ConnectionError("Failed to connect to any Redis cluster node")
        
        redis_cluster_info.info({
            'cluster_mode': 'true',
            'cluster_nodes': ','.join(cluster_urls),
            'active_node': cluster_urls[0] if cluster_urls else 'unknown'
        })
    
    async def _setup_monitoring(self):
        """Setup Redis connection monitoring"""
        if not self.pool:
            return
        
        # Record initial metrics
        redis_connection_pool_size.set(self.config.max_connections)
        
        # Monitor pool statistics
        asyncio.create_task(self._monitor_pool_stats())
    
    async def _monitor_pool_stats(self):
        """Monitor connection pool statistics"""
        while self._initialized:
            try:
                if self.pool:
                    # Get pool statistics
                    created_connections = getattr(self.pool, '_created_connections', 0)
                    available_connections = getattr(self.pool, '_available_connections', [])
                    
                    redis_connection_pool_available.set(len(available_connections))
                
                await asyncio.sleep(10)  # Update every 10 seconds
            except Exception as e:
                logger.error(f"Error monitoring Redis pool stats: {e}")
                await asyncio.sleep(30)
    
    def _start_health_check(self):
        """Start periodic health check"""
        self._health_check_task = asyncio.create_task(self._health_check_loop())
    
    async def _health_check_loop(self):
        """Periodic health check for Redis connections"""
        while self._initialized:
            try:
                await asyncio.sleep(self.config.health_check_interval)
                
                if self.client:
                    start_time = time.time()
                    await self.client.ping()
                    duration = time.time() - start_time
                    redis_operation_duration.labels(operation='ping').observe(duration)
                    
            except Exception as e:
                redis_connection_errors.inc()
                logger.error(f"Redis health check failed: {e}")
                
                # Attempt to reconnect
                if self._cluster_mode and self._cluster_nodes:
                    await self._handle_cluster_failover()
    
    async def _handle_cluster_failover(self):
        """Handle Redis cluster failover"""
        logger.info("Attempting Redis cluster failover...")
        
        for url in self._cluster_nodes:
            try:
                # Close current connection
                if self.client:
                    await self.client.close()
                
                # Try to connect to next node
                await self._create_single_pool(url)
                logger.info(f"Failover successful to Redis node: {url}")
                return
                
            except Exception as e:
                logger.warning(f"Failover attempt failed for {url}: {e}")
                continue
        
        logger.error("All Redis cluster nodes are unavailable")
    
    @asynccontextmanager
    async def get_client(self) -> Redis:
        """Get Redis client with automatic connection management"""
        if not self._initialized:
            await self.initialize()
        
        start_time = time.time()
        try:
            yield self.client
        except Exception as e:
            redis_connection_errors.inc()
            logger.error(f"Redis operation error: {e}")
            raise
        finally:
            duration = time.time() - start_time
            redis_operation_duration.labels(operation='general').observe(duration)
    
    async def pipeline(self) -> aioredis.client.Pipeline:
        """Get Redis pipeline for bulk operations"""
        if not self._initialized:
            await self.initialize()
        
        redis_pipeline_operations.inc()
        return self.client.pipeline()
    
    async def execute_pipeline(self, pipeline: aioredis.client.Pipeline) -> List[Any]:
        """Execute Redis pipeline with monitoring"""
        start_time = time.time()
        try:
            results = await pipeline.execute()
            return results
        except Exception as e:
            redis_connection_errors.inc()
            logger.error(f"Redis pipeline execution error: {e}")
            raise
        finally:
            duration = time.time() - start_time
            redis_operation_duration.labels(operation='pipeline').observe(duration)
    
    def get_pool_stats(self) -> Dict[str, Any]:
        """Get current Redis connection pool statistics"""
        if not self.pool:
            return {"status": "not_initialized"}
        
        return {
            "max_connections": self.config.max_connections,
            "cluster_mode": self._cluster_mode,
            "cluster_nodes": self._cluster_nodes if self._cluster_mode else None,
            "health_check_interval": self.config.health_check_interval,
            "socket_timeout": self.config.socket_timeout,
        }
    
    async def close(self):
        """Close Redis connections and cleanup"""
        self._initialized = False
        
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
        
        if self.client:
            await self.client.close()
            logger.info("Redis client closed")
        
        if self.pool:
            await self.pool.disconnect()
            logger.info("Redis connection pool closed")


# Global Redis connection manager instance
redis_manager = RedisConnectionManager()
