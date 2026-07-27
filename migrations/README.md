# Database migrations

Apply migrations before starting the API in deployed environments:

```bash
uv run alembic upgrade head
```

The Docker image performs this command before Uvicorn starts. `create_all()` remains as a local-development compatibility fallback, but schema evolution must be expressed as Alembic revisions.
