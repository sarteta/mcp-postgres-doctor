-- Recommended role setup for mcp-postgres-doctor.
-- Run this as a superuser, ONCE per database where the doctor will run.
--
-- The role 'postgres_doctor_ro' will have:
--   - SELECT on all current and future tables in all current and future schemas
--   - Permission to read pg_stat_*, pg_locks, pg_replication, pg_extension
--   - NO INSERT/UPDATE/DELETE/TRUNCATE/DDL
--   - NO CREATEDB / CREATEROLE / SUPERUSER
--
-- After applying, your DATABASE_URL should look like:
--   postgresql://postgres_doctor_ro:STRONG_PASSWORD@host:5432/dbname

-- 1. Create the role with a strong password.
CREATE ROLE postgres_doctor_ro
    LOGIN
    PASSWORD 'CHANGE_ME_STRONG_RANDOM_64_CHARS'
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOREPLICATION
    INHERIT
    CONNECTION LIMIT 10;

-- 2. Grant CONNECT on the database.
GRANT CONNECT ON DATABASE current_database() TO postgres_doctor_ro;

-- 3. Grant USAGE on every existing schema, then default privileges for any
--    schema created later. Run this for each schema you want diagnostics on.
GRANT USAGE ON SCHEMA public TO postgres_doctor_ro;

-- Add other schemas as needed:
-- GRANT USAGE ON SCHEMA app TO postgres_doctor_ro;
-- GRANT USAGE ON SCHEMA analytics TO postgres_doctor_ro;

-- 4. Grant SELECT on existing tables in each granted schema.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO postgres_doctor_ro;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO postgres_doctor_ro;

-- 5. Default privileges so future tables/sequences are auto-granted.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO postgres_doctor_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON SEQUENCES TO postgres_doctor_ro;

-- 6. (Optional) If you use pg_stat_statements, the role already has read access
--    via the pg_read_all_stats role membership below. Otherwise grant explicitly:
GRANT pg_read_all_stats TO postgres_doctor_ro;
GRANT pg_read_all_settings TO postgres_doctor_ro;

-- 7. (Optional, recommended) Restrict where this role can connect from.
--    Edit pg_hba.conf to require SSL and limit source IPs:
--      hostssl all postgres_doctor_ro 10.0.0.0/8 scram-sha-256

-- ----------------------------------------------------------------------------
-- Verification: after applying the above, run this as postgres_doctor_ro to
-- confirm the read-only posture matches what mcp-postgres-doctor expects.

-- Should return false:
--   SELECT rolsuper FROM pg_roles WHERE rolname = current_user;

-- Should return 0:
--   SELECT COUNT(*) FROM information_schema.role_table_grants
--   WHERE grantee = current_user
--     AND privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES')
--     AND table_schema NOT IN ('pg_catalog', 'information_schema');

-- Should error with permission denied:
--   CREATE TABLE _doctor_should_fail (id int);
