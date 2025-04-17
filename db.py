import sqlite3
import os

PERSISTENT_DIR = os.getenv("PERSISTENT_DIR", "/data")
os.makedirs(PERSISTENT_DIR, exist_ok=True)
DB_FILE = os.path.join(PERSISTENT_DIR, "data.db")

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS scores (
                user_id INTEGER PRIMARY KEY,
                user_name TEXT,
                score INTEGER DEFAULT 0
            )
        """)
        conn.commit()

def increment_score(user_id: int, user_name: str):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO scores (user_id, user_name, score) VALUES (?, ?, 0)", (user_id, user_name))
        c.execute("UPDATE scores SET score = score + 1 WHERE user_id = ?", (user_id,))
        conn.commit()

def get_top_users(limit=5):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT user_name, score FROM scores ORDER BY score DESC LIMIT ?", (limit,))
        return c.fetchall()
