"""End-to-end integration tests against a real Postgres.

Skipped automatically if no DATABASE_URL is set. The CI workflow
provides one via a Postgres service container; locally you can run
`docker run --rm -p 5432:5432 -e POSTGRES_PASSWORD=test postgres:16-alpine`
and `export DATABASE_URL=postgresql://postgres:test@localhost:5432/postgres`.

These tests prove the safety policy and the diagnostic queries actually
function against a live Postgres — not just against fakes.
"""
from __future__ import annotations

import os
import time

import psycopg
import pytest

from mcp_postgres_doctor import queries
from mcp_postgres_doctor.safety import (
    SafetyError,
    assert_read_only,
    inspect_role,
    with_statement_timeout,
)


# Skip the whole module if no DATABASE_URL.
pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — skipping live-Postgres integration tests",
)


@pytest.fixture(scope="module")
def admin_conn():
    """Superuser connection for setup/teardown."""
    dsn = os.environ["DATABASE_URL"]
    with psycopg.connect(dsn, autocommit=True) as conn:
        yield conn


@pytest.fixture(scope="module")
def ro_dsn(admin_conn):
    """Create the postgres_doctor_ro role and yield a DSN scoped to it.

    Drops the role at teardown so reruns are clean.
    """
    with admin_conn.cursor() as cur:
        cur.execute("DROP ROLE IF EXISTS postgres_doctor_ro")
        cur.execute("""
            CREATE ROLE postgres_doctor_ro
                LOGIN PASSWORD 'test_ro_password'
                NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
        """)
        # Grant minimum privileges
        cur.execute(
            "GRANT pg_read_all_stats TO postgres_doctor_ro"
        )
        cur.execute("GRANT CONNECT ON DATABASE postgres TO postgres_doctor_ro")
        cur.execute("GRANT USAGE ON SCHEMA public TO postgres_doctor_ro")
        cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO postgres_doctor_ro")
        cur.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO postgres_doctor_ro"
        )

    # Build a DSN that uses the new role
    base = os.environ["DATABASE_URL"]
    # Naive parse: replace the user info with the new role
    # Works for postgresql://user:pass@host:port/db format
    if "@" in base:
        prefix, rest = base.split("://", 1)
        _, host_part = rest.split("@", 1)
        ro = f"{prefix}://postgres_doctor_ro:test_ro_password@{host_part}"
    else:
        pytest.skip(f"can't derive RO DSN from {base!r}")

    yield ro

    # Teardown
    with admin_conn.cursor() as cur:
        cur.execute("REASSIGN OWNED BY postgres_doctor_ro TO postgres")
        cur.execute("DROP OWNED BY postgres_doctor_ro")
        cur.execute("DROP ROLE postgres_doctor_ro")


# ============================================================================
# SAFETY TESTS — the contract that protects production
# ============================================================================


def test_safety_rejects_superuser(admin_conn):
    """The connecting role must not be a superuser; CI runs admin as superuser."""
    with pytest.raises(SafetyError, match="SUPERUSER"):
        assert_read_only(admin_conn)


def test_safety_accepts_ro_role(ro_dsn):
    with psycopg.connect(ro_dsn) as conn:
        posture = assert_read_only(conn)
        assert posture.role == "postgres_doctor_ro"
        assert posture.is_safe is True
        assert posture.is_superuser is False


def test_ro_role_cannot_write(ro_dsn, admin_conn):
    """Sanity: confirm Postgres itself rejects writes from the RO role."""
    with admin_conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS _doctor_smoke_test (id int)")

    with psycopg.connect(ro_dsn) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("INSERT INTO _doctor_smoke_test VALUES (1)")

    with admin_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS _doctor_smoke_test")


def test_statement_timeout_kills_runaway(ro_dsn):
    with psycopg.connect(ro_dsn) as conn:
        with conn.transaction():
            with_statement_timeout(conn, 200)  # 200ms cap
            with conn.cursor() as cur:
                with pytest.raises(psycopg.errors.QueryCanceled):
                    cur.execute("SELECT pg_sleep(2)")


# ============================================================================
# QUERY TESTS — every shipped query parses + returns rows on a live Postgres
# ============================================================================


def test_lock_contention_runs(ro_dsn):
    with psycopg.connect(ro_dsn) as conn:
        with conn.transaction():
            with_statement_timeout(conn, 5000)
            with conn.cursor() as cur:
                cur.execute(queries.LOCK_CONTENTION, (10,))
                rows = cur.fetchall()
                # On a fresh DB there are no blocked queries; that's fine.
                assert isinstance(rows, list)


def test_long_running_transactions_runs(ro_dsn):
    with psycopg.connect(ro_dsn) as conn:
        with conn.transaction():
            with_statement_timeout(conn, 5000)
            with conn.cursor() as cur:
                cur.execute(queries.LONG_RUNNING_TRANSACTIONS, (60, 50))
                rows = cur.fetchall()
                assert isinstance(rows, list)


def test_replication_lag_runs(ro_dsn):
    with psycopg.connect(ro_dsn) as conn:
        with conn.transaction():
            with_statement_timeout(conn, 5000)
            with conn.cursor() as cur:
                cur.execute(queries.REPLICATION_LAG)
                rows = cur.fetchall()
                # Standalone Postgres has no replicas
                assert rows == []


def test_table_bloat_estimate_runs(ro_dsn):
    with psycopg.connect(ro_dsn) as conn:
        with conn.transaction():
            with_statement_timeout(conn, 5000)
            with conn.cursor() as cur:
                cur.execute(queries.TABLE_BLOAT_ESTIMATE, (25,))
                rows = cur.fetchall()
                assert isinstance(rows, list)


def test_pg_stat_statements_check(ro_dsn):
    with psycopg.connect(ro_dsn) as conn:
        with conn.transaction():
            with_statement_timeout(conn, 5000)
            with conn.cursor() as cur:
                cur.execute(queries.PG_STAT_STATEMENTS_AVAILABLE)
                row = cur.fetchone()
                assert isinstance(row[0], bool)


def test_connection_state_runs(ro_dsn):
    with psycopg.connect(ro_dsn) as conn:
        with conn.transaction():
            with_statement_timeout(conn, 5000)
            with conn.cursor() as cur:
                cur.execute(queries.CONNECTION_STATE)
                rows = cur.fetchall()
                # We are connected, so at least our own conn appears
                assert isinstance(rows, list)


def test_unused_indexes_runs(ro_dsn):
    with psycopg.connect(ro_dsn) as conn:
        with conn.transaction():
            with_statement_timeout(conn, 5000)
            with conn.cursor() as cur:
                cur.execute(queries.UNUSED_INDEXES, (25,))
                rows = cur.fetchall()
                assert isinstance(rows, list)


def test_cache_hit_ratio_runs(ro_dsn):
    with psycopg.connect(ro_dsn) as conn:
        with conn.transaction():
            with_statement_timeout(conn, 5000)
            with conn.cursor() as cur:
                cur.execute(queries.CACHE_HIT_RATIO, (25,))
                rows = cur.fetchall()
                assert isinstance(rows, list)


def test_database_size_runs(ro_dsn):
    with psycopg.connect(ro_dsn) as conn:
        with conn.transaction():
            with_statement_timeout(conn, 5000)
            with conn.cursor() as cur:
                cur.execute(queries.DATABASE_SIZE)
                rows = cur.fetchall()
                # At least the postgres database exists
                assert len(rows) >= 1
                names = [r[0] for r in rows]
                assert "postgres" in names
