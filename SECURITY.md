# Security model

This document describes what `mcp-postgres-doctor` will and won't do, and the
operator setup the server expects.

## What this server does

It exposes 9 read-only diagnostic tools backed by `SELECT` statements against
`pg_stat_*`, `pg_locks`, and related Postgres catalogs. It is intended to run
inside an MCP client (Claude Desktop, an agent framework, an internal LLM tool)
so an operator or LLM can ask Postgres health questions without `psql` access.

## What this server will not do

- It will not execute DDL (CREATE, ALTER, DROP, TRUNCATE).
- It will not execute DML (INSERT, UPDATE, DELETE).
- It will not execute arbitrary SQL on behalf of the LLM. Every tool runs a
  literal SQL string shipped in [`queries.py`](./src/mcp_postgres_doctor/queries.py);
  parameters that vary (limits, intervals) are bound via positional placeholders.
- It will not start if the connecting role is a `SUPERUSER`, has `CREATEDB`,
  has `CREATEROLE`, or has any `INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES`
  grants on user-namespace tables. The server raises and exits in those cases.
  See [`safety.py`](./src/mcp_postgres_doctor/safety.py).

## What you need to set up

### 1. A dedicated read-only role

**Do not point this server at a role your application uses.** Create a role
that exists only for diagnostics:

```sql
CREATE ROLE postgres_doctor_ro
    LOGIN PASSWORD 'STRONG_RANDOM_PASSWORD'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
    CONNECTION LIMIT 10;
```

The full setup, including default privileges so future tables are auto-granted,
is in [`examples/grant-readonly-role.sql`](./examples/grant-readonly-role.sql).

### 2. `pg_hba.conf` restrictions

Restrict the network reach of this role. At minimum require SSL and limit by
source CIDR:

```
hostssl all postgres_doctor_ro 10.0.0.0/8 scram-sha-256
```

If you run the doctor on the same host as Postgres, prefer `local` peer auth
or a unix socket entirely.

### 3. Connection limits

The role's `CONNECTION LIMIT` (default 10 in the example) caps how many
concurrent diagnostic sessions can exist at once. This protects you if an
agent loops a tool by mistake.

### 4. Statement timeout

Every tool wraps its query in a transaction with `SET LOCAL statement_timeout
= 5000`. A query that runs longer than 5 seconds is killed. You can adjust the
default in `safety.py` if your environment needs a different cap; do not raise
it casually.

## Threat model and what this server does not protect against

| Concern | Mitigation in this server | What you still own |
|---|---|---|
| LLM tries to run DDL/DML | Tools only call shipped SELECTs; role lacks the grants anyway | Don't grant write privileges to the role |
| LLM tries to read application data | The role only has SELECT on what you GRANT | Be deliberate about which schemas you GRANT USAGE on |
| LLM-induced runaway query | Per-call statement_timeout (5s) | Set sane statement_timeout at the role level too |
| Stolen connection string | Role is non-super, network-limited via pg_hba.conf | Rotate password; restrict source IPs |
| MCP client itself is malicious | Server still only runs literal SQL | Trust your MCP client; don't expose this on the public internet |
| Information disclosure via diagnostics | Tools surface query text from pg_stat_activity (may include sensitive params) | If your application binds PII into queries, scrub or omit `lock_contention` and `long_running_transactions` from your client config |

The information-disclosure row is the one most teams underweight. `pg_stat_activity.query`
can include the parameter values an application bound at execution time. If
your app routinely puts emails, tokens, or PII into queries (rather than as
bind params), an LLM with access to these tools can see them. Mitigations:

1. Make sure your application uses parameterized queries (you should be doing
   this anyway).
2. If you must allow the server but want to hide query text, run with the
   `pg_read_all_stats` role membership but **without** `pg_read_server_files`
   or superuser, and consider patching the relevant tools out at startup.

## Verifying your setup

After you create `postgres_doctor_ro` and configure `DATABASE_URL`, run the
server once and confirm it reports a safe posture in the log lines:

```
$ mcp-postgres-doctor
[posture] role=postgres_doctor_ro is_superuser=False can_create_db=False ...
```

If the server refuses to start, the error message lists exactly which check
failed. Fix the grant before continuing.

## Reporting a vulnerability

If you find a security issue, please email santiagoarteta@gmail.com instead
of opening a public issue. Expect a response within a few days.
