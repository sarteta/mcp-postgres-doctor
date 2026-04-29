-- Demo init: creates postgres_doctor_ro on first Postgres startup.
-- Mounted by compose.yml at /docker-entrypoint-initdb.d/00-init.sql.

CREATE ROLE postgres_doctor_ro
    LOGIN PASSWORD 'demo_ro_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
    CONNECTION LIMIT 10;

GRANT CONNECT ON DATABASE demo TO postgres_doctor_ro;
GRANT USAGE ON SCHEMA public TO postgres_doctor_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO postgres_doctor_ro;
GRANT pg_read_all_stats     TO postgres_doctor_ro;
GRANT pg_read_all_settings  TO postgres_doctor_ro;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO postgres_doctor_ro;
