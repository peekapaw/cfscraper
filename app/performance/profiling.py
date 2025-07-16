"""
Memory profiling and optimization utilities.

This module provides:
- Memory profiling and leak detection
- Streaming data processing for large responses
- Garbage collection optimization
- Memory usage monitoring and alerting
- Memory-efficient data structures
"""

import gc
import logging
import psutil
import time
import weakref
from typing import Dict, Any, Optional, Generator, Iterator, List
from dataclasses import dataclass
from contextlib import contextmanager
import asyncio
import sys

from prometheus_client import Gauge, Counter, Histogram

logger = logging.getLogger(__name__)

# Memory metrics
memory_usage_bytes = Gauge('memory_usage_bytes', 'Current memory usage in bytes', ['type'])
memory_leaks_detected = Counter('memory_leaks_detected_total', 'Total memory leaks detected')
gc_collections = Counter('gc_collections_total', 'Total garbage collections', ['generation'])
gc_duration = Histogram('gc_duration_seconds', 'Garbage collection duration')
memory_allocations = Counter('memory_allocations_total', 'Total memory allocations', ['size_category'])


@dataclass
class MemoryStats:
    """Memory usage statistics"""
    rss: int  # Resident Set Size
    vms: int  # Virtual Memory Size
    percent: float  # Memory percentage
    available: int  # Available memory
    total: int  # Total memory
    
    @classmethod
    def current(cls) -> 'MemoryStats':
        """Get current memory statistics"""
        process = psutil.Process()
        memory_info = process.memory_info()
        memory_percent = process.memory_percent()
        virtual_memory = psutil.virtual_memory()
        
        return cls(
            rss=memory_info.rss,
            vms=memory_info.vms,
            percent=memory_percent,
            available=virtual_memory.available,
            total=virtual_memory.total
        )


class MemoryProfiler:
    """Memory profiler for detecting leaks and monitoring usage"""
    
    def __init__(self):
        self.tracked_objects: Dict[str, weakref.WeakSet] = {}
        self.baseline_stats: Optional[MemoryStats] = None
        self.monitoring_enabled = True
        self._monitoring_task: Optional[asyncio.Task] = None
    
    def track_object(self, obj: Any, category: str = "general"):
        """Track an object for memory leak detection"""
        if category not in self.tracked_objects:
            self.tracked_objects[category] = weakref.WeakSet()
        
        self.tracked_objects[category].add(obj)
        
        # Categorize allocation size
        size = sys.getsizeof(obj)
        if size < 1024:
            size_category = "small"
        elif size < 1024 * 1024:
            size_category = "medium"
        else:
            size_category = "large"
        
        memory_allocations.labels(size_category=size_category).inc()
    
    def get_tracked_count(self, category: str) -> int:
        """Get count of tracked objects in a category"""
        if category not in self.tracked_objects:
            return 0
        return len(self.tracked_objects[category])
    
    def set_baseline(self):
        """Set memory baseline for leak detection"""
        self.baseline_stats = MemoryStats.current()
        logger.info(f"Memory baseline set: {self.baseline_stats.rss / 1024 / 1024:.2f} MB RSS")
    
    def check_for_leaks(self) -> Dict[str, Any]:
        """Check for memory leaks"""
        current_stats = MemoryStats.current()
        
        leak_info = {
            'current_memory': current_stats,
            'baseline_memory': self.baseline_stats,
            'tracked_objects': {cat: len(objs) for cat, objs in self.tracked_objects.items()},
            'potential_leaks': []
        }
        
        if self.baseline_stats:
            memory_growth = current_stats.rss - self.baseline_stats.rss
            growth_mb = memory_growth / 1024 / 1024
            
            if growth_mb > 100:  # More than 100MB growth
                leak_info['potential_leaks'].append({
                    'type': 'memory_growth',
                    'growth_mb': growth_mb,
                    'severity': 'high' if growth_mb > 500 else 'medium'
                })
                memory_leaks_detected.inc()
        
        # Check for object count growth
        for category, objects in self.tracked_objects.items():
            count = len(objects)
            if count > 10000:  # Arbitrary threshold
                leak_info['potential_leaks'].append({
                    'type': 'object_count',
                    'category': category,
                    'count': count,
                    'severity': 'high' if count > 50000 else 'medium'
                })
        
        return leak_info
    
    def force_gc(self) -> Dict[str, int]:
        """Force garbage collection and return statistics"""
        start_time = time.time()
        
        # Collect statistics before GC
        before_stats = MemoryStats.current()
        
        # Force garbage collection for all generations
        collected = {}
        for generation in range(3):
            collected[f'gen_{generation}'] = gc.collect(generation)
            gc_collections.labels(generation=str(generation)).inc()
        
        # Record GC duration
        duration = time.time() - start_time
        gc_duration.observe(duration)
        
        # Get statistics after GC
        after_stats = MemoryStats.current()
        freed_mb = (before_stats.rss - after_stats.rss) / 1024 / 1024
        
        logger.info(f"Garbage collection freed {freed_mb:.2f} MB in {duration:.3f}s")
        
        return {
            'collected_objects': collected,
            'duration_seconds': duration,
            'memory_freed_mb': freed_mb,
            'before_rss_mb': before_stats.rss / 1024 / 1024,
            'after_rss_mb': after_stats.rss / 1024 / 1024
        }
    
    async def start_monitoring(self, interval: int = 60):
        """Start continuous memory monitoring"""
        if self._monitoring_task:
            return
        
        self._monitoring_task = asyncio.create_task(self._monitoring_loop(interval))
        logger.info(f"Memory monitoring started with {interval}s interval")
    
    async def stop_monitoring(self):
        """Stop memory monitoring"""
        if self._monitoring_task:
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass
            self._monitoring_task = None
            logger.info("Memory monitoring stopped")
    
    async def _monitoring_loop(self, interval: int):
        """Memory monitoring loop"""
        while self.monitoring_enabled:
            try:
                stats = MemoryStats.current()
                
                # Update metrics
                memory_usage_bytes.labels(type='rss').set(stats.rss)
                memory_usage_bytes.labels(type='vms').set(stats.vms)
                memory_usage_bytes.labels(type='available').set(stats.available)
                
                # Check for high memory usage
                if stats.percent > 80:
                    logger.warning(f"High memory usage: {stats.percent:.1f}%")
                    
                    # Force GC if memory usage is very high
                    if stats.percent > 90:
                        logger.warning("Critical memory usage, forcing garbage collection")
                        self.force_gc()
                
                # Check for leaks periodically
                if hasattr(self, '_leak_check_counter'):
                    self._leak_check_counter += 1
                else:
                    self._leak_check_counter = 1
                
                if self._leak_check_counter % 10 == 0:  # Every 10 intervals
                    leak_info = self.check_for_leaks()
                    if leak_info['potential_leaks']:
                        logger.warning(f"Potential memory leaks detected: {leak_info['potential_leaks']}")
                
                await asyncio.sleep(interval)
                
            except Exception as e:
                logger.error(f"Error in memory monitoring: {e}")
                await asyncio.sleep(interval)


class StreamingProcessor:
    """Memory-efficient streaming processor for large datasets"""
    
    def __init__(self, chunk_size: int = 8192):
        self.chunk_size = chunk_size
    
    def stream_json_array(self, data: List[Dict[str, Any]]) -> Generator[str, None, None]:
        """Stream JSON array in chunks to reduce memory usage"""
        yield "["
        
        for i, item in enumerate(data):
            if i > 0:
                yield ","
            
            # Convert item to JSON string
            import json
            yield json.dumps(item)
            
            # Yield control periodically to prevent blocking
            if i % 100 == 0:
                yield ""
        
        yield "]"
    
    def stream_csv_data(self, data: Iterator[Dict[str, Any]], headers: List[str]) -> Generator[str, None, None]:
        """Stream CSV data in chunks"""
        import csv
        import io
        
        # Yield headers
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=headers)
        writer.writeheader()
        yield output.getvalue()
        
        # Stream data in chunks
        chunk = []
        for item in data:
            chunk.append(item)
            
            if len(chunk) >= self.chunk_size:
                output = io.StringIO()
                writer = csv.DictWriter(output, fieldnames=headers)
                writer.writerows(chunk)
                yield output.getvalue()
                chunk = []
        
        # Yield remaining data
        if chunk:
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=headers)
            writer.writerows(chunk)
            yield output.getvalue()
    
    @contextmanager
    def memory_limit_context(self, max_memory_mb: int = 500):
        """Context manager to monitor memory usage during processing"""
        start_stats = MemoryStats.current()
        max_memory_bytes = max_memory_mb * 1024 * 1024
        
        try:
            yield
        finally:
            current_stats = MemoryStats.current()
            memory_used = current_stats.rss - start_stats.rss
            
            if memory_used > max_memory_bytes:
                logger.warning(
                    f"Memory usage exceeded limit: {memory_used / 1024 / 1024:.2f} MB "
                    f"(limit: {max_memory_mb} MB)"
                )
                
                # Force garbage collection
                profiler.force_gc()


# Global memory profiler instance
profiler = MemoryProfiler()


@contextmanager
def memory_profiling(category: str = "general"):
    """Context manager for memory profiling"""
    start_stats = MemoryStats.current()
    
    try:
        yield profiler
    finally:
        end_stats = MemoryStats.current()
        memory_used = end_stats.rss - start_stats.rss
        
        logger.debug(
            f"Memory usage for {category}: {memory_used / 1024 / 1024:.2f} MB"
        )


def optimize_gc_settings():
    """Optimize garbage collection settings for better performance"""
    # Set GC thresholds for better performance
    # (threshold0, threshold1, threshold2)
    gc.set_threshold(700, 10, 10)
    
    # Enable automatic garbage collection
    gc.enable()
    
    logger.info("Garbage collection settings optimized")


def get_memory_efficient_dict() -> Dict[str, Any]:
    """Get a memory-efficient dictionary implementation"""
    # Use __slots__ for memory efficiency when possible
    class MemoryEfficientDict(dict):
        __slots__ = ()

    return MemoryEfficientDict()


# Performance profiling decorators and utilities
import cProfile
import pstats
import io
from functools import wraps

def profile_function(sort_by='cumulative', lines_to_print=20):
    """Decorator to profile a function's performance"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            profiler = cProfile.Profile()
            profiler.enable()

            try:
                result = func(*args, **kwargs)
            finally:
                profiler.disable()

                # Generate profile report
                s = io.StringIO()
                ps = pstats.Stats(profiler, stream=s).sort_stats(sort_by)
                ps.print_stats(lines_to_print)

                logger.info(f"Profile for {func.__name__}:\n{s.getvalue()}")

            return result
        return wrapper
    return decorator


def profile_async_function(sort_by='cumulative', lines_to_print=20):
    """Decorator to profile an async function's performance"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            profiler = cProfile.Profile()
            profiler.enable()

            try:
                result = await func(*args, **kwargs)
            finally:
                profiler.disable()

                # Generate profile report
                s = io.StringIO()
                ps = pstats.Stats(profiler, stream=s).sort_stats(sort_by)
                ps.print_stats(lines_to_print)

                logger.info(f"Profile for {func.__name__}:\n{s.getvalue()}")

            return result
        return wrapper
    return decorator
