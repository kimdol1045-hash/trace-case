CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
  IF current_setting('server_version_num')::integer < 160000 THEN
    RAISE EXCEPTION 'the failover rehearsal requires PostgreSQL 16 or newer';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
    RAISE EXCEPTION 'the LightRAG rehearsal database requires pgvector';
  END IF;
END
$$;
