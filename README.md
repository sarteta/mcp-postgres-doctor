# mcp-postgres-doctor

[![tests](https://github.com/sarteta/mcp-postgres-doctor/actions/workflows/tests.yml/badge.svg)](https://github.com/sarteta/mcp-postgres-doctor/actions/workflows/tests.yml)
[![docker](https://github.com/sarteta/mcp-postgres-doctor/actions/workflows/docker.yml/badge.svg)](https://github.com/sarteta/mcp-postgres-doctor/actions/workflows/docker.yml)
[![ghcr](https://img.shields.io/badge/ghcr-mcp--postgres--doctor-blue?logo=docker)](https://github.com/sarteta/mcp-postgres-doctor/pkgs/container/mcp-postgres-doctor)
[![python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org)
[![license](https://img.shields.io/badge/license-MIT-green)](./LICENSE)

Read-only Postgres operational diagnostics over MCP. Lock contention,
long-running transactions, replication lag, bloat, slow queries, cache
hit ratios. Surfaced to Claude (or any MCP client) without giving it
write access.

```mermaid
flowchart LR
    O[on-call engineer] -->|"what is happening with the orders DB?"| C[Claude / agent]
    C -->|MCP tool calls| D[mcp-postgres-doctor]
    D -->|SELECT only, 5s timeout| P[(Postgres)]
    D --> C
    C -->|narrative + queries to run| O
```

Most "AI + database" projects let an agent run arbitrary SQL. That's a
production accident waiting to happen. This server takes the other side:
nine fixed read-only tools, a hard-enforced read-only role, a
per-statement 5-second timeout. The agent gets a Postgres health report.
Production gets to keep its day.

It pairs well with [`postgres-production-playbook`](https://github.com/sarteta/postgres-production-playbook).
The playbook teaches a human what to look for; the doctor lets a
machine surface it on demand.

## What you get

Nine MCP tools, all read-only:

| Tool | Question it answers |
|---|---|
| `lock_contention` | What query is blocking what other query right now? |
| `long_running_transactions` | Any transaction older than N seconds? |
| `replication_lag` | How far behind are my replicas (bytes + time)? |
| `table_bloat_estimate` | Which tables have the most dead tuples? |
| `slow_queries_top` | Top queries by total execution time (pg_stat_statements). |
| `connection_state` | How many connections per state / app / user? |
| `unused_indexes` | Indexes that nothing has used since the last stats reset. |
| `cache_hit_ratio` | Which tables are missing the buffer cache? |
| `database_size` | Sizes of every non-template database on the server. |

Every tool is a single literal `SELECT` against `pg_stat_*` /
`pg_locks` / `pg_database`. No user input is ever interpolated as SQL.

## Security model -- read this first

The server **refuses to start** unless the connecting role passes four
checks:

1. Not a superuser.
2. No `CREATEDB`.
3. No `CREATEROLE`.
4. Zero `INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES` grants on
   user-namespace tables.

The recommended setup creates a dedicated `postgres_doctor_ro` role,
grants `SELECT` only, and uses `ALTER DEFAULT PRIVILEGES` so future
tables stay covered. Full SQL is in
[`examples/grant-readonly-role.sql`](./examples/grant-readonly-role.sql).
The threat model and trade-offs are in [`SECURITY.md`](./SECURITY.md).

> If you grant this server write access, you're holding it wrong. The
> point is that you _can't_ accidentally let an LLM `DROP TABLE` even
> if you wanted to.

## Install and run

### Docker (recommended)

```bash
# 30-second demo: spin up Postgres seeded with the read-only role,
# then build the doctor image and verify the safety check + tool
# registration.
git clone https://github.com/sarteta/mcp-postgres-doctor
cd mcp-postgres-doctor
docker compose up --build
# expected: "OK -- server built. Registered tools:" plus 9 tool names
```

A pre-built image is pushed to GHCR on every main commit:

```bash
docker pull ghcr.io/sarteta/mcp-postgres-doctor:latest

docker run --rm \
  -e DATABASE_URL='postgresql://postgres_doctor_ro:STRONG_PW@host.docker.internal:5432/db' \
  ghcr.io/sarteta/mcp-postgres-doctor:latest
```

The image runs as non-root (uid 10001), no shell, no build tools.

### Python

```bash
pip install mcp-postgres-doctor      # not yet on PyPI; use the source clone
# or
uvx mcp-postgres-doctor

export DATABASE_URL='postgresql://postgres_doctor_ro:STRONG_PW@host:5432/db'
mcp-postgres-doctor
```

## Wire it to Claude Desktop

Drop this into `~/.config/Claude/claude_desktop_config.json`
(or `%APPDATA%/Claude/claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "postgres-doctor": {
      "command": "uvx",
      "args": ["mcp-postgres-doctor"],
      "env": {
        "DATABASE_URL": "postgresql://postgres_doctor_ro:STRONG_PW@host:5432/db"
      }
    }
  }
}
```

Restart Claude Desktop. Ask: _"Are there any locks blocking queries on
the orders database right now?"_ Claude calls `lock_contention`, you get
the answer.

## Example session

```
You: Replicas look slow today. What's the lag?

Claude: Calling replication_lag()...
        Three replicas reporting:
        - reporting-replica-01: replay_lag 12s, write_lag_bytes ~14 MB
        - reporting-replica-02: replay_lag 11s, similar
        - analytics-replica:    replay_lag 4m 02s, write_lag_bytes ~1.2 GB
        The analytics replica is the outlier. Want me to check
        long-running transactions on the primary that might be
        blocking WAL replay?

You: yes

Claude: Calling long_running_transactions(min_age_seconds=120)...
        One match: pid 4821, idle in transaction, xact_age 6m 18s,
        from app analytics-etl. That's the cause. The replica can't
        advance until that transaction commits or aborts.
```

## Tests

```bash
pytest tests/
```

The shipped tests cover:

- Safety layer: rejects superuser, rejects roles with write grants,
  enforces statement_timeout, frozen `RolePosture` dataclass.
- Query strings: no f-string-style placeholders, no string concat, no
  DDL/DML keywords, every query starts with `WITH` or `SELECT`,
  positional binds only.

Tests use fakes; no live database required to run them.

## Roadmap

- `mcp-postgres-doctor inspect` CLI subcommand to dry-run all tools
  against a DSN and print a Markdown report (no MCP client needed).
- pgstattuple-backed bloat tool when the extension is installed.
- Wait-event aggregation tool (5-minute windows over `pg_stat_activity`).
- Optional schema-level scoping so you can restrict which user schemas
  the doctor reports on, even when the role has wider SELECT rights.

## Related projects

Same MCP-server pattern, different domain:

- [`mcp-aws-cost-doctor`](https://github.com/sarteta/mcp-aws-cost-doctor) — read-only AWS cost diagnostic tools (idle EC2, orphaned EBS, unused EIPs, oversized RDS) over MCP.
- [`mcp-supabase-latam`](https://github.com/sarteta/mcp-supabase-latam) — Supabase admin tools for LATAM SMBs over MCP.

If you want both Postgres + AWS visibility in a single Claude session, mount this server alongside `mcp-aws-cost-doctor` — the agent can correlate database load with infrastructure waste in one conversation.

## License

MIT (c) 2026 Santiago Arteta
