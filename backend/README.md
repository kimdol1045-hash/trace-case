# Backend foundation

This directory owns the Sprint 0 backend foundation: strict startup settings,
PostgreSQL 16/pgvector validation, Alembic migrations, the ontology v1 schema
skeleton, and a `FOR UPDATE SKIP LOCKED` background-job prototype.

## Install and run tests without external services

From the repository root:

```bash
python3 -m venv /tmp/tc-backend-venv
/tmp/tc-backend-venv/bin/pip install -r backend/requirements.lock
/tmp/tc-backend-venv/bin/pip install --no-deps -e backend
/tmp/tc-backend-venv/bin/pytest backend/tests/unit -m 'not postgres'
```

Unit tests only compile PostgreSQL SQL and exercise pure validation/state
logic. They do not connect to PostgreSQL, Slack, object storage, Ollama, or LightRAG.

## Run PostgreSQL migration and repository tests

The repository tests intentionally require a **disposable** database whose
name contains `test`. The migration test downgrades it to Alembic `base` and
then rebuilds it, so never point this command at shared or production data.

One reproducible PostgreSQL 16 + pgvector test database is:

```bash
docker run --rm --name tc-postgres-test \
  -e POSTGRES_USER=tc \
  -e POSTGRES_PASSWORD=tc-test-only \
  -e POSTGRES_DB=tc_test \
  -p 55432:5432 \
  pgvector/pgvector:pg16
```

In a second terminal, after PostgreSQL is ready:

```bash
export TC_TEST_DATABASE_URL='postgresql+psycopg://tc:tc-test-only@127.0.0.1:55432/tc_test'
/tmp/tc-backend-venv/bin/pytest backend/tests/repository -m postgres
```

Apply migrations and run the startup capability check separately with:

```bash
export TC_DATABASE_URL="$TC_TEST_DATABASE_URL"
export TC_ENCRYPTION_MASTER_KEY='local-test-key-with-at-least-32-chars'
/tmp/tc-backend-venv/bin/alembic -c backend/alembic.ini upgrade head
/tmp/tc-backend-venv/bin/tc-backend-check
```

The baseline migration aborts on PostgreSQL older than 16 and fails when the
`vector` extension is unavailable. The startup check also verifies both core
tables and the fixed ontology embedding column type `vector(1024)`.

## Migration recovery

`alembic downgrade base` removes application tables in reverse dependency
order. It deliberately retains the shared `vector` extension; removing an
extension is a database-administration decision and not an application
rollback side effect.
