"""
Performance optimization package for CFScraper.

This package provides:
- Memory profiling and leak detection
- Performance monitoring and metrics
- Bottleneck identification and analysis
- Optimization utilities
"""

from .profiling import (
    profiler,
    MemoryProfiler,
    MemoryStats,
    StreamingProcessor,
    memory_profiling,
    optimize_gc_settings,
    get_memory_efficient_dict,
    profile_function,
    profile_async_function
)

from .bottleneck_analyzer import (
    bottleneck_analyzer,
    BottleneckAnalyzer,
    DatabaseProfiler,
    EndpointProfiler,
    PerformanceIssue,
    QueryProfile
)

__all__ = [
    'profiler',
    'MemoryProfiler',
    'MemoryStats',
    'StreamingProcessor',
    'memory_profiling',
    'optimize_gc_settings',
    'get_memory_efficient_dict',
    'profile_function',
    'profile_async_function',
    'bottleneck_analyzer',
    'BottleneckAnalyzer',
    'DatabaseProfiler',
    'EndpointProfiler',
    'PerformanceIssue',
    'QueryProfile',
]
