CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_extension
    WHERE extname = 'vector'
  ) THEN
    RAISE EXCEPTION 'pgvector extension was not installed';
  END IF;
END
$$;
