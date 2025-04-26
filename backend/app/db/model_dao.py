from app.db.sqlite_client import get_connection

def init_model_table():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER NOT NULL,
            model_name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

# 插入模型
def insert_model(provider_id: int, model_name: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO models (provider_id, model_name)
        VALUES (?, ?)
    """, (provider_id, model_name))
    conn.commit()
    conn.close()

# 根据provider查模型
def get_models_by_provider(provider_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, model_name FROM models
        WHERE provider_id = ?
    """, (provider_id,))
    rows = cursor.fetchall()
    conn.close()
    return [{"id": row[0], "model_name": row[1]} for row in rows]

# 删除某个模型
def delete_model(model_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM models WHERE id = ?
    """, (model_id,))
    conn.commit()
    conn.close()

def get_all_models():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, provider_id, model_name FROM models
    """)
    rows = cursor.fetchall()
    conn.close()
    return [{"id": row[0], "provider_id": row[1], "model_name": row[2]} for row in rows]