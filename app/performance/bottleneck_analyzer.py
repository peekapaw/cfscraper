"""
Bottleneck identification and performance analysis tools.

This module provides:
- Application profiling tools
- CPU and memory profiling during load tests
- Database query performance analysis
- API endpoint response time analysis
- Bottleneck identification and documentation
"""

import time
import logging
import asyncio
import cProfile
import pstats
import io
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, asdict
from contextlib import contextmanager, asynccontextmanager
import threading
from collections import defaultdict, deque

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from prometheus_client import Histogram, Counter, Gauge

logger = logging.getLogger(__name__)

# Performance metrics
endpoint_response_time = Histogram('endpoint_response_time_seconds', 'Endpoint response time', ['endpoint', 'method'])
database_query_time = Histogram('database_query_time_seconds', 'Database query execution time', ['query_type'])
slow_queries_total = Counter('slow_queries_total', 'Total slow database queries', ['query_type'])
bottlenecks_detected = Counter('bottlenecks_detected_total', 'Total bottlenecks detected', ['type'])
active_profiling_sessions = Gauge('active_profiling_sessions', 'Currently active profiling sessions')


@dataclass
class PerformanceIssue:
    """Performance issue data structure"""
    type: str  # 'slow_query', 'high_cpu', 'memory_leak', 'slow_endpoint'
    severity: str  # 'low', 'medium', 'high', 'critical'
    description: str
    location: str  # function/endpoint/query
    metrics: Dict[str, Any]
    timestamp: str
    suggestions: List[str]


@dataclass
class QueryProfile:
    """Database query profiling data"""
    query: str
    execution_time: float
    rows_affected: int
    query_type: str  # SELECT, INSERT, UPDATE, DELETE
    timestamp: str
    stack_trace: List[str]


class DatabaseProfiler:
    """Database query performance profiler"""
    
    def __init__(self, slow_query_threshold: float = 1.0):
        self.slow_query_threshold = slow_query_threshold
        self.query_profiles: List[QueryProfile] = []
        self.query_stats = defaultdict(list)
        self.enabled = False
    
    def enable(self):
        """Enable database profiling"""
        self.enabled = True
        
        @event.listens_for(Engine, "before_cursor_execute")
        def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            context._query_start_time = time.time()
            context._query_statement = statement
        
        @event.listens_for(Engine, "after_cursor_execute")
        def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            if not self.enabled:
                return
            
            execution_time = time.time() - context._query_start_time
            
            # Determine query type
            query_type = statement.strip().split()[0].upper() if statement.strip() else "UNKNOWN"
            
            # Record metrics
            database_query_time.labels(query_type=query_type).observe(execution_time)
            
            # Check for slow queries
            if execution_time > self.slow_query_threshold:
                slow_queries_total.labels(query_type=query_type).inc()
                
                # Get stack trace
                import traceback
                stack_trace = traceback.format_stack()
                
                # Create query profile
                profile = QueryProfile(
                    query=statement[:500],  # Truncate long queries
                    execution_time=execution_time,
                    rows_affected=cursor.rowcount if hasattr(cursor, 'rowcount') else 0,
                    query_type=query_type,
                    timestamp=time.strftime('%Y-%m-%d %H:%M:%S'),
                    stack_trace=stack_trace[-5:]  # Last 5 stack frames
                )
                
                self.query_profiles.append(profile)
                
                logger.warning(
                    f"Slow query detected: {execution_time:.3f}s - {statement[:100]}..."
                )
            
            # Update statistics
            self.query_stats[query_type].append(execution_time)
    
    def disable(self):
        """Disable database profiling"""
        self.enabled = False
    
    def get_slow_queries(self, limit: int = 10) -> List[QueryProfile]:
        """Get slowest queries"""
        return sorted(
            self.query_profiles,
            key=lambda x: x.execution_time,
            reverse=True
        )[:limit]
    
    def get_query_statistics(self) -> Dict[str, Dict[str, float]]:
        """Get query performance statistics"""
        stats = {}
        
        for query_type, times in self.query_stats.items():
            if times:
                stats[query_type] = {
                    'count': len(times),
                    'avg_time': sum(times) / len(times),
                    'max_time': max(times),
                    'min_time': min(times),
                    'total_time': sum(times)
                }
        
        return stats


class EndpointProfiler:
    """API endpoint performance profiler"""
    
    def __init__(self, slow_endpoint_threshold: float = 2.0):
        self.slow_endpoint_threshold = slow_endpoint_threshold
        self.endpoint_profiles = defaultdict(list)
        self.enabled = False
    
    def profile_endpoint(self, endpoint: str, method: str = "GET"):
        """Decorator to profile endpoint performance"""
        def decorator(func):
            async def async_wrapper(*args, **kwargs):
                if not self.enabled:
                    return await func(*args, **kwargs)
                
                start_time = time.time()
                try:
                    result = await func(*args, **kwargs)
                    return result
                finally:
                    execution_time = time.time() - start_time
                    self._record_endpoint_performance(endpoint, method, execution_time)
            
            def sync_wrapper(*args, **kwargs):
                if not self.enabled:
                    return func(*args, **kwargs)
                
                start_time = time.time()
                try:
                    result = func(*args, **kwargs)
                    return result
                finally:
                    execution_time = time.time() - start_time
                    self._record_endpoint_performance(endpoint, method, execution_time)
            
            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            else:
                return sync_wrapper
        
        return decorator
    
    def _record_endpoint_performance(self, endpoint: str, method: str, execution_time: float):
        """Record endpoint performance metrics"""
        endpoint_response_time.labels(endpoint=endpoint, method=method).observe(execution_time)
        
        self.endpoint_profiles[f"{method} {endpoint}"].append({
            'execution_time': execution_time,
            'timestamp': time.time()
        })
        
        if execution_time > self.slow_endpoint_threshold:
            logger.warning(
                f"Slow endpoint detected: {method} {endpoint} - {execution_time:.3f}s"
            )
    
    def enable(self):
        """Enable endpoint profiling"""
        self.enabled = True
    
    def disable(self):
        """Disable endpoint profiling"""
        self.enabled = False
    
    def get_slow_endpoints(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get slowest endpoints"""
        slow_endpoints = []
        
        for endpoint, profiles in self.endpoint_profiles.items():
            if profiles:
                avg_time = sum(p['execution_time'] for p in profiles) / len(profiles)
                max_time = max(p['execution_time'] for p in profiles)
                
                slow_endpoints.append({
                    'endpoint': endpoint,
                    'avg_time': avg_time,
                    'max_time': max_time,
                    'request_count': len(profiles)
                })
        
        return sorted(slow_endpoints, key=lambda x: x['avg_time'], reverse=True)[:limit]


class BottleneckAnalyzer:
    """Comprehensive bottleneck analysis"""
    
    def __init__(self):
        self.db_profiler = DatabaseProfiler()
        self.endpoint_profiler = EndpointProfiler()
        self.performance_issues: List[PerformanceIssue] = []
        self.analysis_enabled = False
    
    def start_analysis(self):
        """Start bottleneck analysis"""
        self.analysis_enabled = True
        self.db_profiler.enable()
        self.endpoint_profiler.enable()
        active_profiling_sessions.inc()
        
        logger.info("Bottleneck analysis started")
    
    def stop_analysis(self):
        """Stop bottleneck analysis"""
        self.analysis_enabled = False
        self.db_profiler.disable()
        self.endpoint_profiler.disable()
        active_profiling_sessions.dec()
        
        logger.info("Bottleneck analysis stopped")
    
    @contextmanager
    def profile_code_block(self, name: str):
        """Context manager to profile a code block"""
        start_time = time.time()
        profiler = cProfile.Profile()
        profiler.enable()
        
        try:
            yield
        finally:
            profiler.disable()
            execution_time = time.time() - start_time
            
            # Generate profile report
            s = io.StringIO()
            ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
            ps.print_stats(10)
            
            logger.info(f"Code block '{name}' profile:\n{s.getvalue()}")
            
            if execution_time > 1.0:  # Slow code block threshold
                self._add_performance_issue(
                    type="slow_code_block",
                    severity="medium" if execution_time < 5.0 else "high",
                    description=f"Code block '{name}' is slow",
                    location=name,
                    metrics={"execution_time": execution_time},
                    suggestions=[
                        "Profile the code block to identify bottlenecks",
                        "Consider optimizing algorithms or data structures",
                        "Check for unnecessary loops or operations"
                    ]
                )
    
    def analyze_performance_issues(self) -> List[PerformanceIssue]:
        """Analyze collected data for performance issues"""
        issues = []
        
        # Analyze slow queries
        slow_queries = self.db_profiler.get_slow_queries()
        for query in slow_queries:
            severity = "high" if query.execution_time > 5.0 else "medium"
            
            issue = PerformanceIssue(
                type="slow_query",
                severity=severity,
                description=f"Slow database query detected",
                location=f"Query: {query.query[:50]}...",
                metrics={
                    "execution_time": query.execution_time,
                    "query_type": query.query_type,
                    "rows_affected": query.rows_affected
                },
                timestamp=query.timestamp,
                suggestions=[
                    "Add appropriate database indexes",
                    "Optimize query structure",
                    "Consider query result caching",
                    "Review database schema design"
                ]
            )
            issues.append(issue)
        
        # Analyze slow endpoints
        slow_endpoints = self.endpoint_profiler.get_slow_endpoints()
        for endpoint_data in slow_endpoints:
            if endpoint_data['avg_time'] > 2.0:
                severity = "high" if endpoint_data['avg_time'] > 5.0 else "medium"
                
                issue = PerformanceIssue(
                    type="slow_endpoint",
                    severity=severity,
                    description=f"Slow API endpoint detected",
                    location=endpoint_data['endpoint'],
                    metrics={
                        "avg_response_time": endpoint_data['avg_time'],
                        "max_response_time": endpoint_data['max_time'],
                        "request_count": endpoint_data['request_count']
                    },
                    timestamp=time.strftime('%Y-%m-%d %H:%M:%S'),
                    suggestions=[
                        "Profile endpoint code for bottlenecks",
                        "Optimize database queries",
                        "Add response caching",
                        "Consider async processing for heavy operations"
                    ]
                )
                issues.append(issue)
        
        # Update metrics
        for issue in issues:
            bottlenecks_detected.labels(type=issue.type).inc()
        
        self.performance_issues.extend(issues)
        return issues
    
    def _add_performance_issue(self, type: str, severity: str, description: str, 
                             location: str, metrics: Dict[str, Any], suggestions: List[str]):
        """Add a performance issue"""
        issue = PerformanceIssue(
            type=type,
            severity=severity,
            description=description,
            location=location,
            metrics=metrics,
            timestamp=time.strftime('%Y-%m-%d %H:%M:%S'),
            suggestions=suggestions
        )
        
        self.performance_issues.append(issue)
        bottlenecks_detected.labels(type=type).inc()
    
    def generate_report(self) -> Dict[str, Any]:
        """Generate comprehensive performance report"""
        issues = self.analyze_performance_issues()
        
        # Categorize issues by severity
        critical_issues = [i for i in issues if i.severity == "critical"]
        high_issues = [i for i in issues if i.severity == "high"]
        medium_issues = [i for i in issues if i.severity == "medium"]
        low_issues = [i for i in issues if i.severity == "low"]
        
        # Get database statistics
        db_stats = self.db_profiler.get_query_statistics()
        
        # Get endpoint statistics
        endpoint_stats = self.endpoint_profiler.get_slow_endpoints()
        
        report = {
            "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
            "summary": {
                "total_issues": len(issues),
                "critical_issues": len(critical_issues),
                "high_issues": len(high_issues),
                "medium_issues": len(medium_issues),
                "low_issues": len(low_issues)
            },
            "issues": [asdict(issue) for issue in issues],
            "database_performance": db_stats,
            "endpoint_performance": endpoint_stats,
            "recommendations": self._generate_recommendations(issues)
        }
        
        return report
    
    def _generate_recommendations(self, issues: List[PerformanceIssue]) -> List[str]:
        """Generate high-level recommendations based on issues"""
        recommendations = []
        
        # Count issue types
        issue_types = defaultdict(int)
        for issue in issues:
            issue_types[issue.type] += 1
        
        if issue_types["slow_query"] > 3:
            recommendations.append("Consider database optimization: add indexes, optimize queries")
        
        if issue_types["slow_endpoint"] > 2:
            recommendations.append("Optimize API endpoints: add caching, async processing")
        
        if any(issue.severity == "critical" for issue in issues):
            recommendations.append("Address critical performance issues immediately")
        
        if len(issues) > 10:
            recommendations.append("Implement comprehensive performance monitoring")
        
        return recommendations


# Global bottleneck analyzer instance
bottleneck_analyzer = BottleneckAnalyzer()
