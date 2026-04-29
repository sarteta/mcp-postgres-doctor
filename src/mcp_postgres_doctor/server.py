"""MCP server entry point. Wires diagnostic tools to a connection pool."""
from __future__ import annotations

import os
from collections.abc import Iterable
from contextlib import contextmanager
from typing import Any

import psycopg
from mcp.server.fastmcp import FastMCP

from . import queries
from .safety import (
    DEFAULT_STATEMENT_TIMEOUT_MS,
    SafetyError,
    assert_read_only,
    with_statement_timeout,
)


def _conn_string() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SafetyError(
            "DATABASE_URL not set. Point it at the read-only role described in SECURITY.md."
        )
    return dsn


@contextmanager
def _diagnostic_conn():
    """Yield a transaction-scoped connection with statement_timeout enforced."""
    with psycopg.connect(_conn_string(), autocommit=False) as conn:
        with conn.transaction():
            with_statement_timeout(conn, DEFAULT_STATEMENT_TIMEOUT_MS)
            yield conn


def _rows_to_dicts(cur: psycopg.Cursor) -> list[dict[str, Any]]:
    cols = [d[0] for d in cur.description] if cur.description else []
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def build_server() -> FastMCP:
    """Create and configure the MCP server.

    Performs a startup safety check: if the connecting role is not read-only
    we refuse to register any tools.
    """
    # Startup safety check — fail loudly if posture is wrong.
    with psycopg.connect(_conn_string()) as conn:
        posture = assert_read_only(conn)

    server = FastMCP(
        "postgres-doctor",
        instructions=(
            "Read-only Postgres operational diagnostics. Tools surface what "
            f"the role '{posture.role}' can see in pg_stat_*, pg_locks, and "
            "related catalogs. No DDL or DML is performed; statement_timeout "
            f"is enforced at {DEFAULT_STATEMENT_TIMEOUT_MS}ms per call."
        ),
    )

    @server.tool()
    def lock_contention(limit: int = 25) -> list[dict[str, Any]]:
        """List currently-blocked queries and the queries blocking them.

        Use when callers report 'the app is hanging' or pg_stat_activity
        shows wait_event_type=Lock. Returns blocker/blocked pid pairs and
        the queries on each side, ordered by how long the blocked txn
        has been waiting.
        """
        limit = max(1, min(limit, 200))
        with _diagnostic_conn() as conn, conn.cursor() as cur:
            cur.execute(queries.LOCK_CONTENTION, (limit,))
            return _rows_to_dicts(cur)

    @server.tool()
    def long_running_transactions(
        min_age_seconds: int = 60, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Transactions older than min_age_seconds in the current database.

        The classic on-call signal: an idle-in-transaction connection holding
        locks. min_age_seconds defaults to 60; tighten to 5-10 during an
        active incident.
        """
        min_age_seconds = max(1, min_age_seconds)
        limit = max(1, min(limit, 200))
        with _diagnostic_conn() as conn, conn.cursor() as cur:
            cur.execute(queries.LONG_RUNNING_TRANSACTIONS, (min_age_seconds, limit))
            return _rows_to_dicts(cur)

    @server.tool()
    def replication_lag() -> list[dict[str, Any]]:
        """Physical and logical replication lag for this primary.

        Returns one row per replica, with byte and time-based lag. Empty
        list means either no replicas, or this server is itself a replica.
        """
        with _diagnostic_conn() as conn, conn.cursor() as cur:
            cur.execute(queries.REPLICATION_LAG)
            return _rows_to_dicts(cur)

    @server.tool()
    def table_bloat_estimate(limit: int = 25) -> list[dict[str, Any]]:
        """Tables ordered by dead-tuple percentage.

        Heuristic — uses pg_stat_user_tables counters, not pgstattuple.
        Use as a first-pass screen; for exact numbers run pgstattuple
        directly.
        """
        limit = max(1, min(limit, 200))
        with _diagnostic_conn() as conn, conn.cursor() as cur:
            cur.execute(queries.TABLE_BLOAT_ESTIMATE, (limit,))
            return _rows_to_dicts(cur)

    @server.tool()
    def slow_queries_top(limit: int = 20) -> list[dict[str, Any]] | dict[str, str]:
        """Top N queries by total execution time from pg_stat_statements.

        Returns a clear message if pg_stat_statements is not installed.
        """
        limit = max(1, min(limit, 200))
        with _diagnostic_conn() as conn, conn.cursor() as cur:
            cur.execute(queries.PG_STAT_STATEMENTS_AVAILABLE)
            available = cur.fetchone()[0]
            if not available:
                return {
                    "error": "pg_stat_statements extension is not installed in this database.",
                    "hint": "CREATE EXTENSION pg_stat_statements; (requires superuser)",
                }
            cur.execute(queries.SLOW_QUERIES_TOP, (limit,))
            return _rows_to_dicts(cur)

    @server.tool()
    def connection_state() -> list[dict[str, Any]]:
        """Connection counts grouped by state, application_name, and user.

        Useful when chasing 'too many connections' errors or finding which
        application is leaking idle connections.
        """
        with _diagnostic_conn() as conn, conn.cursor() as cur:
            cur.execute(queries.CONNECTION_STATE)
            return _rows_to_dicts(cur)

    @server.tool()
    def unused_indexes(limit: int = 25) -> list[dict[str, Any]]:
        """Indexes with zero scans since the last stats reset.

        Excludes primary key and unique-constraint backing indexes since
        those are correctness-required regardless of scan count. Sorted
        by index size.
        """
        limit = max(1, min(limit, 200))
        with _diagnostic_conn() as conn, conn.cursor() as cur:
            cur.execute(queries.UNUSED_INDEXES, (limit,))
            return _rows_to_dicts(cur)

    @server.tool()
    def cache_hit_ratio(limit: int = 25) -> list[dict[str, Any]]:
        """Per-table buffer-cache hit percentage.

        Targets >99% on warm production tables. A table at 80% hit and high
        block reads is either undersized shared_buffers or a candidate for
        more aggressive index coverage.
        """
        limit = max(1, min(limit, 200))
        with _diagnostic_conn() as conn, conn.cursor() as cur:
            cur.execute(queries.CACHE_HIT_RATIO, (limit,))
            return _rows_to_dicts(cur)

    @server.tool()
    def database_size() -> list[dict[str, Any]]:
        """Sizes of all non-template databases on this server."""
        with _diagnostic_conn() as conn, conn.cursor() as cur:
            cur.execute(queries.DATABASE_SIZE)
            return _rows_to_dicts(cur)

    return server


def main() -> None:
    server = build_server()
    server.run()


if __name__ == "__main__":
    main()
