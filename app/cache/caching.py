"""
Multi-level caching implementation with Redis and in-memory caching.

This module provides:
- Redis caching for API responses
- In-memory caching for configuration data
- Database query result caching
- Cache invalidation strategies
- Cache hit/miss ratio monitoring
"""

import logging
import json
import time
import hashlib
from typing import Any, Optional, Dict, Union, Callable
from dataclasses import dataclass
from functools import wraps
import asyncio

from prometheus_client import Counter, Histogram, Gauge

from app.cache.redis_client import redis_manager
from app.core.config import settings

logger = logging.getLogger(__name__)

# Cache metrics
cache_hits = Counter('cache_hits_total', 'Total cache hits', ['cache_type', 'key_prefix'])
cache_misses = Counter('cache_misses_total', 'Total cache misses', ['cache_type', 'key_prefix'])
cache_operations = Histogram('cache_operation_duration_seconds', 'Cache operation duration', ['operation', 'cache_type'])
cache_size = Gauge('cache_size_bytes', 'Current cache size in bytes', ['cache_type'])
cache_hit_ratio = Gauge('cache_hit_ratio', 'Cache hit ratio', ['cache_type'])


@dataclass
class CacheConfig:
    """Configuration for caching system"""
    default_ttl: int = 3600  # 1 hour
    max_memory_cache_size: int = 100 * 1024 * 1024  # 100MB
    memory_cache_ttl: int = 300  # 5 minutes
    redis_key_prefix: str = "cfscraper:cache:"
    enable_compression: bool = True
    compression_threshold: int = 1024  # Compress data larger than 1KB
    
    @classmethod
    def from_settings(cls) -> 'CacheConfig':
        """Create configuration from application settings"""
        return cls(
            default_ttl=getattr(settings, 'cache_default_ttl', 3600),
            max_memory_cache_size=getattr(settings, 'cache_max_memory_size', 100 * 1024 * 1024),
            memory_cache_ttl=getattr(settings, 'cache_memory_ttl', 300),
            redis_key_prefix=getattr(settings, 'cache_redis_prefix', "cfscraper:cache:"),
            enable_compression=getattr(settings, 'cache_enable_compression', True),
            compression_threshold=getattr(settings, 'cache_compression_threshold', 1024),
        )


class MemoryCache:
    """In-memory cache with TTL support"""
    
    def __init__(self, max_size: int = 100 * 1024 * 1024):
        self.max_size = max_size
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.access_times: Dict[str, float] = {}
        self.current_size = 0
    
    def _calculate_size(self, data: Any) -> int:
        """Calculate approximate size of data"""
        try:
            return len(json.dumps(data, default=str).encode('utf-8'))
        except:
            return len(str(data).encode('utf-8'))
    
    def _evict_expired(self):
        """Remove expired entries"""
        current_time = time.time()
        expired_keys = []
        
        for key, entry in self.cache.items():
            if entry['expires_at'] < current_time:
                expired_keys.append(key)
        
        for key in expired_keys:
            self._remove_key(key)
    
    def _evict_lru(self, needed_space: int):
        """Evict least recently used entries to free space"""
        while self.current_size + needed_space > self.max_size and self.cache:
            # Find least recently used key
            lru_key = min(self.access_times.keys(), key=lambda k: self.access_times[k])
            self._remove_key(lru_key)
    
    def _remove_key(self, key: str):
        """Remove a key from cache"""
        if key in self.cache:
            entry = self.cache.pop(key)
            self.access_times.pop(key, None)
            self.current_size -= entry.get('size', 0)
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from memory cache"""
        self._evict_expired()
        
        if key not in self.cache:
            return None
        
        entry = self.cache[key]
        if entry['expires_at'] < time.time():
            self._remove_key(key)
            return None
        
        # Update access time
        self.access_times[key] = time.time()
        return entry['data']
    
    def set(self, key: str, value: Any, ttl: int = 300):
        """Set value in memory cache"""
        self._evict_expired()
        
        data_size = self._calculate_size(value)
        
        # Check if we need to evict entries
        if key not in self.cache:
            self._evict_lru(data_size)
        else:
            # Remove old entry size
            old_entry = self.cache[key]
            self.current_size -= old_entry.get('size', 0)
        
        # Add new entry
        expires_at = time.time() + ttl
        self.cache[key] = {
            'data': value,
            'expires_at': expires_at,
            'size': data_size
        }
        self.access_times[key] = time.time()
        self.current_size += data_size
        
        # Update metrics
        cache_size.labels(cache_type='memory').set(self.current_size)
    
    def delete(self, key: str):
        """Delete key from memory cache"""
        self._remove_key(key)
        cache_size.labels(cache_type='memory').set(self.current_size)
    
    def clear(self):
        """Clear all entries from memory cache"""
        self.cache.clear()
        self.access_times.clear()
        self.current_size = 0
        cache_size.labels(cache_type='memory').set(0)


class CacheManager:
    """Multi-level cache manager"""
    
    def __init__(self):
        self.config = CacheConfig.from_settings()
        self.memory_cache = MemoryCache(self.config.max_memory_cache_size)
        self._hit_counts = {'memory': 0, 'redis': 0}
        self._miss_counts = {'memory': 0, 'redis': 0}
    
    def _generate_cache_key(self, key: str, prefix: str = "") -> str:
        """Generate cache key with prefix"""
        if prefix:
            return f"{self.config.redis_key_prefix}{prefix}:{key}"
        return f"{self.config.redis_key_prefix}{key}"
    
    def _serialize_data(self, data: Any) -> str:
        """Serialize data for caching"""
        try:
            serialized = json.dumps(data, default=str)
            
            # Compress if enabled and data is large enough
            if (self.config.enable_compression and 
                len(serialized) > self.config.compression_threshold):
                import gzip
                compressed = gzip.compress(serialized.encode('utf-8'))
                return f"gzip:{compressed.hex()}"
            
            return serialized
        except Exception as e:
            logger.error(f"Failed to serialize cache data: {e}")
            return str(data)
    
    def _deserialize_data(self, data: str) -> Any:
        """Deserialize cached data"""
        try:
            if data.startswith("gzip:"):
                # Decompress data
                import gzip
                compressed_hex = data[5:]  # Remove "gzip:" prefix
                compressed = bytes.fromhex(compressed_hex)
                decompressed = gzip.decompress(compressed).decode('utf-8')
                return json.loads(decompressed)
            
            return json.loads(data)
        except Exception as e:
            logger.error(f"Failed to deserialize cache data: {e}")
            return data
    
    async def get(self, key: str, prefix: str = "") -> Optional[Any]:
        """Get value from cache (memory first, then Redis)"""
        cache_key = self._generate_cache_key(key, prefix)
        
        # Try memory cache first
        start_time = time.time()
        memory_value = self.memory_cache.get(cache_key)
        if memory_value is not None:
            cache_hits.labels(cache_type='memory', key_prefix=prefix).inc()
            self._hit_counts['memory'] += 1
            self._update_hit_ratio()
            
            duration = time.time() - start_time
            cache_operations.labels(operation='get', cache_type='memory').observe(duration)
            return memory_value
        
        cache_misses.labels(cache_type='memory', key_prefix=prefix).inc()
        self._miss_counts['memory'] += 1
        
        # Try Redis cache
        start_time = time.time()
        try:
            async with redis_manager.get_client() as redis_client:
                redis_value = await redis_client.get(cache_key)
                
                if redis_value:
                    cache_hits.labels(cache_type='redis', key_prefix=prefix).inc()
                    self._hit_counts['redis'] += 1
                    
                    # Deserialize and store in memory cache
                    deserialized_value = self._deserialize_data(redis_value.decode('utf-8'))
                    self.memory_cache.set(cache_key, deserialized_value, self.config.memory_cache_ttl)
                    
                    duration = time.time() - start_time
                    cache_operations.labels(operation='get', cache_type='redis').observe(duration)
                    self._update_hit_ratio()
                    return deserialized_value
                
                cache_misses.labels(cache_type='redis', key_prefix=prefix).inc()
                self._miss_counts['redis'] += 1
                
        except Exception as e:
            logger.error(f"Redis cache get error: {e}")
        
        duration = time.time() - start_time
        cache_operations.labels(operation='get', cache_type='redis').observe(duration)
        self._update_hit_ratio()
        return None
    
    async def set(self, key: str, value: Any, ttl: Optional[int] = None, prefix: str = ""):
        """Set value in cache (both memory and Redis)"""
        cache_key = self._generate_cache_key(key, prefix)
        ttl = ttl or self.config.default_ttl
        
        # Set in memory cache
        start_time = time.time()
        self.memory_cache.set(cache_key, value, min(ttl, self.config.memory_cache_ttl))
        duration = time.time() - start_time
        cache_operations.labels(operation='set', cache_type='memory').observe(duration)
        
        # Set in Redis cache
        start_time = time.time()
        try:
            serialized_value = self._serialize_data(value)
            async with redis_manager.get_client() as redis_client:
                await redis_client.setex(cache_key, ttl, serialized_value)
                
        except Exception as e:
            logger.error(f"Redis cache set error: {e}")
        
        duration = time.time() - start_time
        cache_operations.labels(operation='set', cache_type='redis').observe(duration)
    
    async def delete(self, key: str, prefix: str = ""):
        """Delete key from cache"""
        cache_key = self._generate_cache_key(key, prefix)
        
        # Delete from memory cache
        self.memory_cache.delete(cache_key)
        
        # Delete from Redis cache
        try:
            async with redis_manager.get_client() as redis_client:
                await redis_client.delete(cache_key)
        except Exception as e:
            logger.error(f"Redis cache delete error: {e}")
    
    async def clear_prefix(self, prefix: str):
        """Clear all keys with given prefix"""
        pattern = self._generate_cache_key("*", prefix)
        
        try:
            async with redis_manager.get_client() as redis_client:
                keys = await redis_client.keys(pattern)
                if keys:
                    await redis_client.delete(*keys)
                    
                    # Also clear from memory cache
                    for key in keys:
                        self.memory_cache.delete(key.decode('utf-8'))
                        
        except Exception as e:
            logger.error(f"Redis cache clear prefix error: {e}")
    
    def _update_hit_ratio(self):
        """Update cache hit ratio metrics"""
        for cache_type in ['memory', 'redis']:
            total = self._hit_counts[cache_type] + self._miss_counts[cache_type]
            if total > 0:
                ratio = self._hit_counts[cache_type] / total
                cache_hit_ratio.labels(cache_type=cache_type).set(ratio)


# Global cache manager instance
cache_manager = CacheManager()


def cached(ttl: int = 3600, prefix: str = "", key_func: Optional[Callable] = None):
    """Decorator for caching function results"""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Generate cache key
            if key_func:
                cache_key = key_func(*args, **kwargs)
            else:
                # Generate key from function name and arguments
                key_parts = [func.__name__]
                key_parts.extend(str(arg) for arg in args)
                key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
                cache_key = hashlib.md5(":".join(key_parts).encode()).hexdigest()
            
            # Try to get from cache
            cached_result = await cache_manager.get(cache_key, prefix)
            if cached_result is not None:
                return cached_result
            
            # Execute function and cache result
            result = await func(*args, **kwargs)
            await cache_manager.set(cache_key, result, ttl, prefix)
            return result
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            # For sync functions, we need to handle caching differently
            # This is a simplified version - in practice, you might want to use a sync cache
            return func(*args, **kwargs)
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator
