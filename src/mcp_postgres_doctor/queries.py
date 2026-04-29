"""Diagnostic queries.

Every query here is a literal SQL string with no user input interpolation.
Parameters that vary (limits, age thresholds) are bound via $1, $2, ...
"""
from __future__ import annotations


# ============================================================================
# Lock contention — what is blocking what right now
# ----------------------------------------------------------------------------
# Joins pg_locks against itself to find blockers. The resulting rows describe
# a chain "blocked PID is waiting on blocker PID, who is running query Q".
# Limited to current database to avoid surprising cross-DB results.
LOCK_CONTENTION = """
SELECT
    blocked.pid           AS blocked_pid,
    blocked.usename       AS blocked_user,
    blocked.query         AS blocked_query,
    blocked.wait_event_type,
    blocked.wait_event,
    blocking.pid          AS blocking_pid,
    blocking.usename      AS blocking_user,
    blocking.query        AS blocking_query,
    blocking.state        AS blocking_state,
    age(now(), blocked.xact_start)  AS blocked_xact_age
FROM pg_stat_activity blocked
JOIN pg_stat_activity blocking
  ON blocking.pid = ANY(pg_blocking_pids(blocked.pid))
WHERE blocked.datname = current_database()
ORDER BY blocked_xact_age DESC NULLS LAST
LIMIT %s
"""


# ============================================================================
# Long-running transactions — txns older than a threshold
# ----------------------------------------------------------------------------
# Catches the classic incident: an idle-in-transaction connection holding
# locks on a row, blocking everything downstream.
LONG_RUNNING_TRANSACTIONS = """
SELECT
    pid,
    usename,
    application_name,
    state,
    age(now(), xact_start)  AS xact_age,
    age(now(), query_start) AS query_age,
    wait_event_type,
    wait_event,
    query
FROM pg_stat_activity
WHERE xact_start IS NOT NULL
  AND age(now(), xact_start) > make_interval(secs => %s)
  AND datname = current_database()
ORDER BY xact_start ASC
LIMIT %s
"""


# ============================================================================
# Replication lag — physical and logical
# ----------------------------------------------------------------------------
# pg_stat_replication is empty on a replica or on a primary with no replicas;
# returning an empty array is the right answer in those cases.
REPLICATION_LAG = """
SELECT
    application_name,
    client_addr,
    state,
    sync_state,
    write_lag,
    flush_lag,
    replay_lag,
    pg_wal_lsn_diff(pg_current_wal_lsn(), sent_lsn)   AS sent_lag_bytes,
    pg_wal_lsn_diff(pg_current_wal_lsn(), write_lsn)  AS write_lag_bytes,
    pg_wal_lsn_diff(pg_current_wal_lsn(), flush_lsn)  AS flush_lag_bytes,
    pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS replay_lag_bytes
FROM pg_stat_replication
ORDER BY replay_lsn
"""


# ============================================================================
# Bloat estimate — table-level
# ----------------------------------------------------------------------------
# Estimate is approximate (uses pgstattuple or basic heuristic). We use the
# basic heuristic from the Postgres wiki because it requires no extension.
# For exact numbers operators should run pgstattuple separately.
TABLE_BLOAT_ESTIMATE = """
WITH constants AS (
    SELECT current_setting('block_size')::int AS bs, 23 AS hdr, 8 AS ma
), tbl AS (
    SELECT
        schemaname, tablename,
        n_live_tup, n_dead_tup,
        CASE WHEN n_live_tup + n_dead_tup = 0 THEN 0
             ELSE round(100.0 * n_dead_tup / (n_live_tup + n_dead_tup), 2)
        END AS dead_pct,
        pg_total_relation_size(schemaname || '.' || tablename) AS total_bytes
    FROM pg_stat_user_tables
)
SELECT
    schemaname,
    tablename,
    n_live_tup,
    n_dead_tup,
    dead_pct,
    pg_size_pretty(total_bytes) AS total_size
FROM tbl
WHERE n_live_tup + n_dead_tup > 0
ORDER BY dead_pct DESC, total_bytes DESC
LIMIT %s
"""


# ============================================================================
# Slow queries — top N from pg_stat_statements (if enabled)
# ----------------------------------------------------------------------------
# Requires the pg_stat_statements extension. If absent, the tool returns a
# clear message instead of a query result.
SLOW_QUERIES_TOP = """
SELECT
    rolname AS role,
    datname AS db,
    calls,
    total_exec_time::numeric(20,2) AS total_ms,
    mean_exec_time::numeric(20,2)  AS mean_ms,
    rows,
    100.0 * shared_blks_hit / NULLIF(shared_blks_hit + shared_blks_read, 0) AS hit_pct,
    LEFT(query, 280) AS query
FROM pg_stat_statements s
JOIN pg_roles r ON r.oid = s.userid
JOIN pg_database d ON d.oid = s.dbid
ORDER BY total_exec_time DESC
LIMIT %s
"""

PG_STAT_STATEMENTS_AVAILABLE = """
SELECT EXISTS (
    SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'
)
"""


# ============================================================================
# Connection state breakdown
# ----------------------------------------------------------------------------
CONNECTION_STATE = """
SELECT
    state,
    application_name,
    usename,
    COUNT(*) AS conn_count,
    MAX(age(now(), state_change)) AS oldest_in_state
FROM pg_stat_activity
WHERE pid <> pg_backend_pid()
  AND datname = current_database()
GROUP BY state, application_name, usename
ORDER BY conn_count DESC
"""


# ============================================================================
# Unused indexes — idx_scan = 0 since stats reset
# ----------------------------------------------------------------------------
UNUSED_INDEXES = """
SELECT
    schemaname,
    relname AS table_name,
    indexrelname AS index_name,
    pg_size_pretty(pg_relation_size(indexrelid)) AS index_size,
    idx_scan
FROM pg_stat_user_indexes
WHERE idx_scan = 0
  AND NOT EXISTS (
      SELECT 1 FROM pg_constraint
      WHERE conindid = indexrelid AND contype IN ('p', 'u')
  )
ORDER BY pg_relation_size(indexrelid) DESC
LIMIT %s
"""


# ============================================================================
# Cache hit ratio — buffer cache effectiveness per table
# ----------------------------------------------------------------------------
CACHE_HIT_RATIO = """
SELECT
    schemaname,
    relname AS table_name,
    heap_blks_read,
    heap_blks_hit,
    CASE WHEN heap_blks_read + heap_blks_hit = 0 THEN NULL
         ELSE round(100.0 * heap_blks_hit / (heap_blks_read + heap_blks_hit), 2)
    END AS hit_pct,
    pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_statio_user_tables
WHERE heap_blks_read + heap_blks_hit > 0
ORDER BY heap_blks_read + heap_blks_hit DESC
LIMIT %s
"""


# ============================================================================
# Database size summary
# ----------------------------------------------------------------------------
DATABASE_SIZE = """
SELECT
    datname,
    pg_size_pretty(pg_database_size(datname)) AS size_pretty,
    pg_database_size(datname) AS size_bytes
FROM pg_database
WHERE datistemplate = false
ORDER BY pg_database_size(datname) DESC
"""
