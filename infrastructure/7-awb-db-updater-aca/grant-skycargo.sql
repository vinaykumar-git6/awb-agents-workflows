-- Grant the awb-db-updater managed identity least-privilege access to the
-- skycargo schema. Run this as a PostgreSQL Entra admin from inside the VNet
-- (the server is private-endpoint only).
--
-- The role name MUST match the app's managed identity name and the PGUSER env
-- var on the Container App (default: 'awb-db-updater').

-- 1. Register the managed identity as a PostgreSQL role (idempotent).
--    `false, false` => not an admin, is a service principal (managed identity).
SELECT * FROM pgaadauth_create_principal('awb-db-updater', false, false);

-- 2. Allow it to use the schema and read/write the two tables.
GRANT USAGE ON SCHEMA skycargo TO "awb-db-updater";

GRANT SELECT, INSERT, UPDATE
    ON skycargo.awb_processing, skycargo.awb_analytics
    TO "awb-db-updater";

-- 3. Allow it to use the identity sequences behind the BIGINT IDENTITY columns.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA skycargo TO "awb-db-updater";

-- 4. Apply the same defaults to any future objects created in the schema.
ALTER DEFAULT PRIVILEGES IN SCHEMA skycargo
    GRANT SELECT, INSERT, UPDATE ON TABLES TO "awb-db-updater";
ALTER DEFAULT PRIVILEGES IN SCHEMA skycargo
    GRANT USAGE, SELECT ON SEQUENCES TO "awb-db-updater";
