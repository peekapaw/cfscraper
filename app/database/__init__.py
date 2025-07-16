"""
Database package for CFScraper.

This package provides optimized database connection management with:
- Connection pooling for better performance
- Async and sync database operations
- Connection monitoring and metrics
- Leak detection and prevention
"""

from .connection import connection_manager, ConnectionPoolConfig, DatabaseConnectionManager

__all__ = [
    'connection_manager',
    'ConnectionPoolConfig', 
    'DatabaseConnectionManager',
]
