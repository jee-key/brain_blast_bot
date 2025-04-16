import sqlite3
import os

# Use a persistent volume path if available, otherwise default to local
PERSISTENT_DIR = os.getenv("PERSISTENT_DIR", "/data")
os.makedirs(PERSISTENT_DIR, exist_ok=True)
DB_FILE = os.path.join(PERSISTENT_DIR, "data.db")

def init_db():
    """
    Initializes the database by creating the scores table if it doesn't exist.
    """
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
    """
    Increments the score for a user when they answer correctly.
    Creates a new user record if the user doesn't exist yet.
    
    Args:
        user_id: Telegram user ID
        user_name: User's display name
    """
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO scores (user_id, user_name, score) VALUES (?, ?, 0)", (user_id, user_name))
        c.execute("UPDATE scores SET score = score + 1 WHERE user_id = ?", (user_id,))
        conn.commit()

def get_top_users(limit=5):
    """
    Retrieves the top users ranked by score.
    
    Args:
        limit: Maximum number of users to return (default: 5)
    
    Returns:
        List of tuples containing (user_name, score) sorted by score
    """
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT user_name, score FROM scores ORDER BY score DESC LIMIT ?", (limit,))
        return c.fetchall()
