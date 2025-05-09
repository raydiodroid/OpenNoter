import json
import os
import sys

from app.db.sqlite_client import get_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)



def get_builtin_providers_path():
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(__file__)
    return os.path.join(base_path, 'builtin_providers.json')

def seed_default_providers():
    conn = get_connection()
    if conn is None:
        logger.error("Failed to connect to database.")
        return

    cursor = conn.cursor()

    # 检查已有数据
    cursor.execute("SELECT COUNT(*) FROM providers")
    count = cursor.fetchone()[0]
    if count > 0:
        logger.info("Providers already exist, skipping seed.")
        conn.close()
        return

    json_path = get_builtin_providers_path()
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            providers = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read builtin_providers.json: {e}")
        conn.close()
        return

    try:
        for p in providers:
            cursor.execute("""
                INSERT INTO providers (id, name, api_key, base_url, logo, type, enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                p['id'],
                p['name'],
                p['api_key'],
                p['base_url'],
                p['logo'],
                p['type'],
                p.get('enabled', 1)
            ))
        conn.commit()
        logger.info("Default providers seeded successfully.")
    except Exception as e:
        logger.error(f"Failed to seed default providers: {e}")
    finally:
        conn.close()
def init_provider_table():
    conn = get_connection()
    if conn is None:
        logger.error("Failed to connect to the database.")
        return
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS providers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            logo TEXT NOT NULL,
            type TEXT NOT NULL,
            api_key TEXT NOT NULL,
            base_url TEXT NOT NULL,
            enabled INTEGER DEFAULT 1, 
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    try:
        conn.commit()
        conn.close()
        logger.info("provider table created successfully.")
        seed_default_providers()
    except Exception as e:
        logger.error(f"Failed to create provider table: {e}")
def insert_provider(id: str, name: str, api_key: str, base_url: str, logo: str, type_: str,enabled:int=1):
    conn = get_connection()
    if conn is None:
        logger.error("Failed to connect to the database.")
        return
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO providers (id, name, api_key, base_url, logo, type, enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (id, name, api_key, base_url, logo, type_, enabled))
    try:
        conn.commit()
        conn.close()
        logger.info(f"Provider inserted successfully. id: {id}, name: {name}, type: {type_}")
        return id
    except Exception as e:
        logger.error(f"Failed to insert provider: {e}")
        return None

def get_enabled_providers():
    conn = get_connection()
    if conn is None:
        logger.error("Failed to connect to the database.")
        return
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM providers WHERE enabled = 1")
    try:
        rows = cursor.fetchall()
        conn.close()
        if rows is None:
            logger.info("No providers found")
            return None
        logger.info(f"Providers found: {rows}")
        return rows
    except Exception as e:
        logger.error(f"Failed to get enabled providers: {e}")
def get_provider_by_name(name: str):
    conn = get_connection()
    if conn is None:
        logger.error("Failed to connect to the database.")
        return
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM providers WHERE name = ?", (name,))
    try:
        row = cursor.fetchone()
        conn.close()
        if row is None:
            logger.info(f"Provider not found: {name}")
            return None
        logger.info(f"Provider found: {row}")
        return row
    except Exception as e:
        logger.error(f"Failed to get provider by name: {e}")

def get_provider_by_id(id: int):
    conn = get_connection()
    if conn is None:
        logger.error("Failed to connect to the database.")
        return
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM providers WHERE id = ?", (id,))

    try:
        row = cursor.fetchone()
        conn.close()
        if row is None:
            logger.info(f"Provider not found: {id}")
            return None
        logger.info(f"Provider found: {row}")
        return row
    except Exception as e:
        logger.error(f"Failed to get provider by id: {e}")

def get_all_providers():
    conn = get_connection()
    if conn is None:
        logger.error("Failed to connect to the database.")
        return
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM providers")
    try:
        rows = cursor.fetchall()
        conn.close()
        if rows is None:
            logger.info("No providers found")
            return None
        logger.info(f"Providers found: {rows}")
        return rows
    except Exception as e:
        logger.error(f"Failed to get all providers: {e}")

def update_provider(id: str, **kwargs):
    conn = get_connection()
    if conn is None:
        logger.error("Failed to connect to the database.")
        return

    fields = []
    values = []

    for key, value in kwargs.items():
        fields.append(f"{key} = ?")
        values.append(value)

    if not fields:
        logger.warning("No fields provided for update.")
        return

    sql = f"""
        UPDATE providers
        SET {', '.join(fields)}
        WHERE id = ?
    """

    values.append(id)  # id 最后加
    cursor = conn.cursor()

    try:
        cursor.execute(sql, values)
        conn.commit()
        conn.close()
        logger.info(f"Provider updated successfully. id: {id}, updated_fields: {fields}")
    except Exception as e:
        logger.error(f"Failed to update provider: {e}")

def delete_provider(id: int):
    conn = get_connection()
    if conn is None:
        logger.error("Failed to connect to the database.")
        return
    cursor = conn.cursor()
    cursor.execute("DELETE FROM providers WHERE id = ?", (id,))
    try:
        conn.commit()
        conn.close()
        logger.info(f"Provider deleted successfully. id: {id}")
    except Exception as e:
        logger.error(f"Failed to delete provider: {e}")