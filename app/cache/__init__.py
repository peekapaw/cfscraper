"""
Cache package for CFScraper.

This package provides optimized caching with:
- Redis connection pooling and optimization
- Multi-level caching (memory + Redis)
- Cache invalidation strategies
- Performance monitoring and metrics
"""

from .redis_client import redis_manager, RedisConnectionManager, RedisPoolConfig
from .caching import cache_manager, CacheManager, CacheConfig, cached

__all__ = [
    'redis_manager',
    'RedisConnectionManager', 
    'RedisPoolConfig',
    'cache_manager',
    'CacheManager',
    'CacheConfig',
    'cached',
]
