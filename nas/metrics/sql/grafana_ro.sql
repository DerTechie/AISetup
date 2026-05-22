-- One-time: create a read-only role for Grafana to query LiteLLM_SpendLogs.
-- A dashboard can then never write to the gateway's database.
-- Run against the RUNNING gateway Postgres (see README "Deploy" step 2).
-- Replace 'change-me' with the same value as GRAFANA_DB_RO_PASSWORD in the metrics .env.

CREATE ROLE grafana_ro WITH LOGIN PASSWORD 'change-me';
GRANT CONNECT ON DATABASE litellm TO grafana_ro;
GRANT USAGE ON SCHEMA public TO grafana_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_ro;
-- Future tables (e.g. after a LiteLLM upgrade adds one) are readable too:
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grafana_ro;
