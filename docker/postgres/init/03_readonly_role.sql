CREATE ROLE datalyst_ro LOGIN PASSWORD 'datalyst_ro';
GRANT CONNECT ON DATABASE shop TO datalyst_ro;
GRANT USAGE ON SCHEMA public TO datalyst_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO datalyst_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO datalyst_ro;
ALTER ROLE datalyst_ro SET default_transaction_read_only = on;
