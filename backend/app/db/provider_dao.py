from app.db.sqlite_client import get_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

def init_provider_table():
    conn = get_connection()
    if conn is None:
        logger.error("Failed to connect to the database.")
        return
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS providers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            logo TEXT NOT NULL,
            type TEXT NOT NULL,                -- ✅ 新增字段
            api_key TEXT NOT NULL,
            base_url TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        conn.commit()
        conn.close()
        logger.info("provider table created successfully.")
    except Exception as e:
        logger.error(f"Failed to create provider table: {e}")
def insert_provider(name: str, api_key: str, base_url: str, logo: str, type_: str):
    conn = get_connection()
    if conn is None:
        logger.error("Failed to connect to the database.")
        return
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO providers (name, api_key, base_url, logo, type)
        VALUES (?, ?, ?, ?, ?)
    """, (name, api_key, base_url, logo, type_))
    try:
        conn.commit()
        cursor_id = cursor.lastrowid
        conn.close()
        logger.info(f"Provider inserted successfully. name: {name}, type: {type_}")
        return cursor_id
    except Exception as e:
        logger.error(f"Failed to insert provider: {e}")
        return None
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

def update_provider(id: int, name: str, api_key: str, base_url: str, logo: str, type_: str):
    conn = get_connection()
    if conn is None:
        logger.error("Failed to connect to the database.")
        return
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE providers 
        SET name = ?, api_key = ?, base_url = ?, logo = ?, type = ?
        WHERE id = ?
    """, (name, api_key, base_url, logo, type_, id))
    try:
        conn.commit()
        conn.close()
        logger.info(f"Provider updated successfully. id: {id}, type: {type_}")
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