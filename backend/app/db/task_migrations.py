"""Idempotent upgrades for existing installations, before any task is served."""
from sqlalchemy import inspect, text


def migrate_task_schema(engine):
    columns = {
        "video_tasks": {
            "user_id": "INTEGER", "status": "VARCHAR(32)", "error": "VARCHAR(512)",
            "failed_at": "TIMESTAMP", "updated_at": "TIMESTAMP", "deleted_at": "TIMESTAMP",
            "attempt_id": "VARCHAR(36)", "request_json": "TEXT",
        },
        "users": {"auth_version": "INTEGER NOT NULL DEFAULT 0", **{name: "INTEGER" for name in (
            "max_concurrent_tasks", "daily_task_limit", "storage_quota_mb",
        )}},
    }
    with engine.begin() as conn:
        for table, fields in columns.items():
            present = {c["name"] for c in inspect(conn).get_columns(table)}
            for name, sql_type in fields.items():
                if name not in present:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {sql_type}'))
        # Backfill once per task, including deleted tasks, so upgrades preserve quotas.
        conn.execute(text("""
            INSERT INTO task_attempts (id, task_id, user_id, created_at)
            SELECT task_id, task_id, user_id, COALESCE(created_at, CURRENT_TIMESTAMP)
            FROM video_tasks v WHERE NOT EXISTS
              (SELECT 1 FROM task_attempts a WHERE a.task_id = v.task_id)
        """))
