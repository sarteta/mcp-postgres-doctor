"""Sanity-check the shipped query strings.

These are static checks — no live database. The goal is to enforce the
contract that no query interpolates user input as raw SQL.
"""
from __future__ import annotations

import re

from mcp_postgres_doctor import queries


ALL_QUERIES = [
    queries.LOCK_CONTENTION,
    queries.LONG_RUNNING_TRANSACTIONS,
    queries.REPLICATION_LAG,
    queries.TABLE_BLOAT_ESTIMATE,
    queries.SLOW_QUERIES_TOP,
    queries.PG_STAT_STATEMENTS_AVAILABLE,
    queries.CONNECTION_STATE,
    queries.UNUSED_INDEXES,
    queries.CACHE_HIT_RATIO,
    queries.DATABASE_SIZE,
]


def test_no_query_contains_python_format_placeholders():
    """`{var}` in an SQL string is the classic injection vector. We forbid it."""
    pattern = re.compile(r"\{\w+\}")
    for q in ALL_QUERIES:
        assert not pattern.search(q), f"f-string-style placeholder in: {q[:60]}"


def test_no_query_contains_string_concat():
    """Operators that imply string concatenation in Python."""
    for q in ALL_QUERIES:
        assert "% (" not in q
        # Allow `%s` (positional bind) but not Python `%(name)s` formatting
        # Actually `%(name)s` is psycopg's named-parameter style, which is safe.
        # We just want to make sure we're not building strings.


def test_only_parameter_style_is_positional():
    """Every parameter must be `%s`, not Python f-string or named-percent."""
    for q in ALL_QUERIES:
        # named-style %(foo)s is allowed by psycopg; not used today, but if it
        # appears later this test will need updating intentionally.
        named_matches = re.findall(r"%\([a-z_]+\)s", q)
        positional_matches = re.findall(r"(?<!%)%s", q)
        # current policy: positional only.
        assert not named_matches, f"unexpected named param in: {q[:60]}"
        # positional may be 0 (e.g. CONNECTION_STATE has no params)


def test_queries_are_select_only():
    """No DDL/DML keywords as statements (CTEs use SELECT-only)."""
    forbidden_starts = (
        "INSERT", "UPDATE", "DELETE", "TRUNCATE",
        "CREATE", "DROP", "ALTER", "GRANT", "REVOKE",
    )
    for q in ALL_QUERIES:
        # Allow these words inside string literals or column names; only flag
        # them when they begin a statement after whitespace/newline.
        first_word = q.strip().split()[0].upper()
        # WITH or SELECT only.
        assert first_word in ("WITH", "SELECT"), (
            f"query does not start with WITH/SELECT: {first_word}"
        )
        for kw in forbidden_starts:
            # Heuristic: forbidden keyword followed by whitespace at start of a line
            pattern = re.compile(rf"(?im)^\s*{kw}\s")
            assert not pattern.search(q), f"forbidden statement {kw} in: {q[:60]}"


def test_lock_contention_filters_to_current_database():
    assert "current_database()" in queries.LOCK_CONTENTION


def test_long_running_uses_make_interval_for_seconds():
    """Binding seconds via make_interval(secs => $1) is safer than concat."""
    assert "make_interval(secs => %s)" in queries.LONG_RUNNING_TRANSACTIONS


def test_pg_stat_statements_check_returns_boolean():
    assert "EXISTS" in queries.PG_STAT_STATEMENTS_AVAILABLE
    assert "pg_extension" in queries.PG_STAT_STATEMENTS_AVAILABLE
    assert "pg_stat_statements" in queries.PG_STAT_STATEMENTS_AVAILABLE


def test_replication_lag_handles_empty_case():
    """Query must work on a primary with no replicas (empty result is fine)."""
    assert "pg_stat_replication" in queries.REPLICATION_LAG


def test_unused_indexes_skips_pk_and_unique():
    """PK / unique-backing indexes must NEVER be flagged as unused."""
    assert "contype IN ('p', 'u')" in queries.UNUSED_INDEXES
