"""
Database connection management with optimized connection pooling.

This module provides:
- Connection pool configuration and monitoring
- Async and sync database session management
- Connection leak detection and prevention
- Performance metrics collection
"""

import logging
import asyncio
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional, Dict, Any
from dataclasses import dataclass

from sqlalchemy import create_engine, event, pool
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool, QueuePool
from prometheus_client import Counter, Histogram, Gauge, Info

from app.core.config import settings

logger = logging.getLogger(__name__)

# Metrics for connection pool monitoring
db_connections_created = Counter('db_connections_created_total', 'Total database connections created')
db_connections_closed = Counter('db_connections_closed_total', 'Total database connections closed')
db_connection_pool_size = Gauge('db_connection_pool_size', 'Current database connection pool size')
db_connection_pool_checked_out = Gauge('db_connection_pool_checked_out', 'Currently checked out connections')
db_query_duration = Histogram('db_query_duration_seconds', 'Database query duration')
db_connection_errors = Counter('db_connection_errors_total', 'Database connection errors')
db_connection_timeouts = Counter('db_connection_timeouts_total', 'Database connection timeouts')
db_pool_info = Info('db_pool_configuration', 'Database pool configuration')


@dataclass
class ConnectionPoolConfig:
    """Configuration for database connection pooling"""
    pool_size: int = 20
    max_overflow: int = 30
    pool_timeout: int = 30
    pool_recycle: int = 3600
    pool_pre_ping: bool = True
    echo: bool = False
    
    @classmethod
    def from_settings(cls) -> 'ConnectionPoolConfig':
        """Create configuration from application settings"""
        return cls(
            pool_size=getattr(settings, 'db_pool_size', 20),
            max_overflow=getattr(settings, 'db_max_overflow', 30),
            pool_timeout=getattr(settings, 'db_pool_timeout', 30),
            pool_recycle=getattr(settings, 'db_pool_recycle', 3600),
            pool_pre_ping=getattr(settings, 'db_pool_pre_ping', True),
            echo=settings.debug,
        )


class DatabaseConnectionManager:
    """Manages database connections with optimized pooling"""
    
    def __init__(self):
        self.engine: Optional[object] = None
        self.async_engine: Optional[object] = None
        self.session_factory: Optional[sessionmaker] = None
        self.async_session_factory: Optional[async_sessionmaker] = None
        self.config = ConnectionPoolConfig.from_settings()
        self._initialized = False
        self._connection_leak_detector = ConnectionLeakDetector()
    
    def initialize(self):
        """Initialize database engines and session factories"""
        if self._initialized:
            return
        
        self._create_engines()
        self._setup_monitoring()
        self._initialized = True
        
        logger.info(
            f"Database connection manager initialized with "
            f"pool_size={self.config.pool_size}, "
            f"max_overflow={self.config.max_overflow}"
        )
    
    def _create_engines(self):
        """Create both sync and async database engines"""
        if settings.database_url.startswith("sqlite"):
            self._create_sqlite_engines()
        else:
            self._create_postgresql_engines()
        
        # Create session factories
        self.session_factory = sessionmaker(
            autocommit=False, 
            autoflush=False, 
            bind=self.engine
        )
        
        self.async_session_factory = async_sessionmaker(
            bind=self.async_engine,
            class_=AsyncSession,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
    
    def _create_sqlite_engines(self):
        """Create SQLite engines for development/testing"""
        self.engine = create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=self.config.echo,
        )
        
        # SQLite async support
        async_db_url = settings.database_url.replace("sqlite://", "sqlite+aiosqlite://")
        self.async_engine = create_async_engine(
            async_db_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=self.config.echo,
        )
    
    def _create_postgresql_engines(self):
        """Create PostgreSQL engines with connection pooling"""
        self.engine = create_engine(
            settings.database_url,
            poolclass=QueuePool,
            pool_size=self.config.pool_size,
            max_overflow=self.config.max_overflow,
            pool_timeout=self.config.pool_timeout,
            pool_recycle=self.config.pool_recycle,
            pool_pre_ping=self.config.pool_pre_ping,
            echo=self.config.echo,
        )
        
        # Async PostgreSQL engine
        async_db_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
        self.async_engine = create_async_engine(
            async_db_url,
            poolclass=pool.QueuePool,
            pool_size=self.config.pool_size,
            max_overflow=self.config.max_overflow,
            pool_timeout=self.config.pool_timeout,
            pool_recycle=self.config.pool_recycle,
            pool_pre_ping=self.config.pool_pre_ping,
            echo=self.config.echo,
        )
    
    def _setup_monitoring(self):
        """Setup connection pool monitoring and metrics"""
        if not self.engine or self.engine.url.drivername.startswith("sqlite"):
            return
        
        # Record pool configuration
        db_pool_info.info({
            'pool_size': str(self.config.pool_size),
            'max_overflow': str(self.config.max_overflow),
            'pool_timeout': str(self.config.pool_timeout),
            'pool_recycle': str(self.config.pool_recycle),
        })
        
        @event.listens_for(self.engine, "connect")
        def on_connect(dbapi_connection, connection_record):
            """Track connection creation"""
            db_connections_created.inc()
            self._connection_leak_detector.track_connection(connection_record)
            logger.debug("Database connection created")
        
        @event.listens_for(self.engine, "close")
        def on_close(dbapi_connection, connection_record):
            """Track connection closure"""
            db_connections_closed.inc()
            self._connection_leak_detector.untrack_connection(connection_record)
            logger.debug("Database connection closed")
        
        @event.listens_for(self.engine, "checkout")
        def on_checkout(dbapi_connection, connection_record, connection_proxy):
            """Track connection checkout"""
            self._update_pool_metrics()
        
        @event.listens_for(self.engine, "checkin")
        def on_checkin(dbapi_connection, connection_record):
            """Track connection checkin"""
            self._update_pool_metrics()
    
    def _update_pool_metrics(self):
        """Update connection pool metrics"""
        if self.engine and hasattr(self.engine, 'pool'):
            pool_obj = self.engine.pool
            db_connection_pool_size.set(pool_obj.size())
            db_connection_pool_checked_out.set(pool_obj.checkedout())
    
    @asynccontextmanager
    async def get_async_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get an async database session"""
        if not self._initialized:
            self.initialize()
        
        start_time = time.time()
        try:
            async with self.async_session_factory() as session:
                yield session
        except Exception as e:
            db_connection_errors.inc()
            logger.error(f"Async database session error: {e}")
            raise
        finally:
            duration = time.time() - start_time
            db_query_duration.observe(duration)
    
    def get_session(self):
        """Get a sync database session"""
        if not self._initialized:
            self.initialize()
        
        session = self.session_factory()
        try:
            yield session
        except Exception as e:
            db_connection_errors.inc()
            logger.error(f"Database session error: {e}")
            session.rollback()
            raise
        finally:
            session.close()
    
    def get_pool_stats(self) -> Dict[str, Any]:
        """Get current connection pool statistics"""
        if not self.engine or self.engine.url.drivername.startswith("sqlite"):
            return {"type": "sqlite", "pool_stats": "N/A"}
        
        pool_obj = self.engine.pool
        return {
            "type": "postgresql",
            "pool_size": pool_obj.size(),
            "checked_out": pool_obj.checkedout(),
            "overflow": pool_obj.overflow(),
            "checked_in": pool_obj.checkedin(),
            "configuration": {
                "pool_size": self.config.pool_size,
                "max_overflow": self.config.max_overflow,
                "pool_timeout": self.config.pool_timeout,
                "pool_recycle": self.config.pool_recycle,
            }
        }
    
    def close_connections(self):
        """Close all database connections"""
        if self.engine:
            self.engine.dispose()
            logger.info("Synchronous database engine disposed")
        
        if self.async_engine:
            asyncio.create_task(self.async_engine.adispose())
            logger.info("Asynchronous database engine disposed")


class ConnectionLeakDetector:
    """Detects and reports connection leaks"""
    
    def __init__(self):
        self.active_connections = {}
        self.max_connection_age = 3600  # 1 hour
    
    def track_connection(self, connection_record):
        """Track a new connection"""
        self.active_connections[id(connection_record)] = {
            'created_at': time.time(),
            'record': connection_record
        }
    
    def untrack_connection(self, connection_record):
        """Stop tracking a connection"""
        self.active_connections.pop(id(connection_record), None)
    
    def check_for_leaks(self):
        """Check for connection leaks and log warnings"""
        current_time = time.time()
        leaked_connections = []
        
        for conn_id, conn_info in self.active_connections.items():
            age = current_time - conn_info['created_at']
            if age > self.max_connection_age:
                leaked_connections.append((conn_id, age))
        
        if leaked_connections:
            logger.warning(
                f"Detected {len(leaked_connections)} potentially leaked connections. "
                f"Ages: {[f'{age:.1f}s' for _, age in leaked_connections]}"
            )
        
        return leaked_connections


# Global connection manager instance
connection_manager = DatabaseConnectionManager()
