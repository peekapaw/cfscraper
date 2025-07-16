"""
Database configuration and utilities for CFScraper.

This module provides backward compatibility while leveraging the new
optimized connection management system.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.orm import declarative_base
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import connection_manager

logger = logging.getLogger(__name__)

# Create base class for models
Base = declarative_base()

# Backward compatibility - expose engines from connection manager
@property
def engine():
    """Get the synchronous database engine"""
    if not connection_manager._initialized:
        connection_manager.initialize()
    return connection_manager.engine

@property
def async_engine():
    """Get the asynchronous database engine"""
    if not connection_manager._initialized:
        connection_manager.initialize()
    return connection_manager.async_engine


def get_db():
    """Dependency to get synchronous database session"""
    yield from connection_manager.get_session()


@asynccontextmanager
async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager for database sessions"""
    async with connection_manager.get_async_session() as session:
        yield session


async def get_async_db_dependency() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for async database sessions"""
    async with get_async_db() as session:
        yield session


def init_db():
    """Initialize database tables"""
    if not connection_manager._initialized:
        connection_manager.initialize()
    Base.metadata.create_all(bind=connection_manager.engine)


async def init_async_db():
    """Initialize database tables asynchronously"""
    if not connection_manager._initialized:
        connection_manager.initialize()

    async with connection_manager.async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def get_connection_pool_stats() -> dict:
    """Get current connection pool statistics"""
    return connection_manager.get_pool_stats()


def close_db_connections():
    """Close all database connections"""
    connection_manager.close_connections()


# Backward compatibility functions - these are now handled by connection_manager
# but kept for existing code that might import them

# Re-export commonly used items for backward compatibility
__all__ = [
    'Base',
    'get_db',
    'get_async_db',
    'get_async_db_dependency',
    'init_db',
    'init_async_db',
    'get_connection_pool_stats',
    'close_db_connections',
    'engine',
    'async_engine',
]