"""Database connection pool for PostGIS."""

import os

import psycopg2
from psycopg2 import pool

_connection_pool = None


def get_pool():
    """Get or create the connection pool."""
    global _connection_pool
    if _connection_pool is None:
        _connection_pool = pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            host=os.environ.get("DB_HOST", "localhost"),
            port=int(os.environ.get("DB_PORT", 5432)),
            dbname=os.environ.get("DB_NAME", "basal"),
            user=os.environ.get("DB_USER", "basal"),
            password=os.environ.get("DB_PASSWORD", "basal_dev"),
        )
    return _connection_pool


def get_connection():
    """Get a connection from the pool."""
    return get_pool().getconn()


def release_connection(conn):
    """Return a connection to the pool."""
    get_pool().putconn(conn)


def close_all():
    """Close all connections in the pool."""
    global _connection_pool
    if _connection_pool is not None:
        _connection_pool.closeall()
        _connection_pool = None
