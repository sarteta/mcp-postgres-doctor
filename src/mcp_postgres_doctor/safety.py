"""Safety layer: enforces read-only posture before any tool runs.

The promise this module makes to operators:

1. The connecting role MUST NOT be a superuser. We refuse to start otherwise.
2. The connecting role MUST NOT have INSERT/UPDATE/DELETE/TRUNCATE/DDL grants
   on user-namespace tables. We refuse to start if it does.
3. Every query is wrapped with `SET LOCAL statement_timeout` (default 5000 ms)
   so a runaway query cannot hold a connection open.
4. No user input is ever interpolated into SQL. Tools accept typed parameters
   (ints, intervals) and the only SQL strings are the literal queries shipped
   in this package.

If any of these checks fail at startup the server raises and exits.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg


DEFAULT_STATEMENT_TIMEOUT_MS = 5000


class SafetyError(RuntimeError):
    """Raised when the connecting role fails read-only posture checks."""


@dataclass(frozen=True)
class RolePosture:
    role: str
    is_superuser: bool
    can_create_db: bool
    can_create_role: bool
    write_grants_count: int

    @property
    def is_safe(self) -> bool:
        return (
            not self.is_superuser
            and not self.can_create_db
            and not self.can_create_role
            and self.write_grants_count == 0
        )


def inspect_role(conn: psycopg.Connection) -> RolePosture:
    """Inspect the connected role's privileges.

    Returns a RolePosture. Caller decides what to do with an unsafe one.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                current_user,
                rolsuper,
                rolcreatedb,
                rolcreaterole
            FROM pg_roles
            WHERE rolname = current_user
        """)
        row = cur.fetchone()
        if row is None:
            raise SafetyError("could not introspect current_user")
        role, is_super, create_db, create_role = row

        # Count any privilege that allows write to user-namespace tables.
        # Excludes pg_catalog and information_schema; those are read-only.
        cur.execute("""
            SELECT COUNT(*)
            FROM information_schema.role_table_grants
            WHERE grantee = current_user
              AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES')
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
        """)
        write_count = cur.fetchone()[0]

    return RolePosture(
        role=role,
        is_superuser=bool(is_super),
        can_create_db=bool(create_db),
        can_create_role=bool(create_role),
        write_grants_count=int(write_count),
    )


def assert_read_only(conn: psycopg.Connection) -> RolePosture:
    """Inspect role and raise SafetyError if it is not read-only."""
    posture = inspect_role(conn)
    if not posture.is_safe:
        reasons = []
        if posture.is_superuser:
            reasons.append("role is SUPERUSER")
        if posture.can_create_db:
            reasons.append("role has CREATEDB")
        if posture.can_create_role:
            reasons.append("role has CREATEROLE")
        if posture.write_grants_count > 0:
            reasons.append(
                f"role has {posture.write_grants_count} INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES grants"
            )
        raise SafetyError(
            f"refusing to run as '{posture.role}': "
            + "; ".join(reasons)
            + ". See SECURITY.md for the recommended GRANT setup."
        )
    return posture


def with_statement_timeout(
    conn: psycopg.Connection, timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS
) -> None:
    """Set a per-transaction statement_timeout. Caller is in a transaction."""
    if timeout_ms <= 0:
        raise ValueError(f"timeout_ms must be > 0, got {timeout_ms}")
    with conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = %s", (str(timeout_ms),))
