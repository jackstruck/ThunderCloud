-- Invoke with: psql -v app_user='developer@example.com' -f scripts/db_grants.sql
-- The caller must be an administrative database user. app_user is quoted as an identifier.
\if :{?app_user}
\else
\echo 'required psql variable app_user is missing'
\quit 3
\endif

SELECT format('GRANT CONNECT ON DATABASE face_index TO %I', :'app_user') \gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'app_user') \gexec
SELECT format(
    'GRANT SELECT, INSERT, UPDATE, DELETE ON identity, subject, source_asset, processing_job, face_track TO %I',
    :'app_user'
) \gexec
