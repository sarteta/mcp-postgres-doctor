"""Tests for the safety layer.

Uses fakes for psycopg connections — no live database required.
The point is to verify the SAFETY POLICY: a connection that returns
unsafe role attributes must trigger SafetyError.
"""
from __future__ import annotations

import pytest

from mcp_postgres_doctor.safety import (
    DEFAULT_STATEMENT_TIMEOUT_MS,
    RolePosture,
    SafetyError,
    assert_read_only,
    inspect_role,
    with_statement_timeout,
)


class FakeCursor:
    def __init__(self, results):
        self._results = list(results)
        self._next = 0
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        r = self._results[self._next]
        self._next += 1
        return r

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _conn_for(*, super=False, createdb=False, createrole=False, write_grants=0):
    cur = FakeCursor([
        ("postgres_doctor_ro", super, createdb, createrole),
        (write_grants,),
    ])
    return FakeConn(cur), cur


def test_inspect_role_safe():
    conn, _ = _conn_for()
    posture = inspect_role(conn)
    assert posture.is_safe is True
    assert posture.role == "postgres_doctor_ro"


def test_inspect_role_unsafe_when_superuser():
    conn, _ = _conn_for(super=True)
    posture = inspect_role(conn)
    assert posture.is_safe is False
    assert posture.is_superuser is True


def test_inspect_role_unsafe_with_write_grants():
    conn, _ = _conn_for(write_grants=3)
    posture = inspect_role(conn)
    assert posture.is_safe is False
    assert posture.write_grants_count == 3


def test_assert_read_only_passes_for_safe():
    conn, _ = _conn_for()
    posture = assert_read_only(conn)
    assert posture.is_safe is True


def test_assert_read_only_raises_for_super():
    conn, _ = _conn_for(super=True)
    with pytest.raises(SafetyError, match="SUPERUSER"):
        assert_read_only(conn)


def test_assert_read_only_raises_for_create_role():
    conn, _ = _conn_for(createrole=True)
    with pytest.raises(SafetyError, match="CREATEROLE"):
        assert_read_only(conn)


def test_assert_read_only_lists_all_reasons():
    conn, _ = _conn_for(super=True, createdb=True, createrole=True, write_grants=2)
    with pytest.raises(SafetyError) as exc:
        assert_read_only(conn)
    msg = str(exc.value)
    assert "SUPERUSER" in msg
    assert "CREATEDB" in msg
    assert "CREATEROLE" in msg
    assert "2" in msg
    assert "SECURITY.md" in msg


def test_with_statement_timeout_executes_set_local():
    cur = FakeCursor([])
    conn = FakeConn(cur)
    with_statement_timeout(conn, 1500)
    assert len(cur.executed) == 1
    sql, params = cur.executed[0]
    # SET commands can't use bind params; we inline a type-validated int.
    assert sql == "SET LOCAL statement_timeout = 1500"


def test_with_statement_timeout_default():
    cur = FakeCursor([])
    conn = FakeConn(cur)
    with_statement_timeout(conn)
    sql, _ = cur.executed[0]
    assert sql == f"SET LOCAL statement_timeout = {DEFAULT_STATEMENT_TIMEOUT_MS}"


def test_with_statement_timeout_rejects_non_int():
    """String input must be rejected — guards the inline-int path."""
    cur = FakeCursor([])
    conn = FakeConn(cur)
    with pytest.raises(TypeError):
        with_statement_timeout(conn, "5000; DROP TABLE x")  # type: ignore[arg-type]


def test_with_statement_timeout_rejects_bool():
    """bool is a subclass of int in Python; we reject it explicitly."""
    cur = FakeCursor([])
    conn = FakeConn(cur)
    with pytest.raises(TypeError):
        with_statement_timeout(conn, True)  # type: ignore[arg-type]


def test_with_statement_timeout_rejects_zero():
    cur = FakeCursor([])
    conn = FakeConn(cur)
    with pytest.raises(ValueError):
        with_statement_timeout(conn, 0)


def test_with_statement_timeout_rejects_negative():
    cur = FakeCursor([])
    conn = FakeConn(cur)
    with pytest.raises(ValueError):
        with_statement_timeout(conn, -100)


def test_role_posture_dataclass_is_frozen():
    p = RolePosture(
        role="x", is_superuser=False, can_create_db=False,
        can_create_role=False, write_grants_count=0,
    )
    with pytest.raises(Exception):
        p.role = "hacked"  # type: ignore[misc]
