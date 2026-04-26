import sqlite3

conn = sqlite3.connect("bot.db", check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
user_id INTEGER PRIMARY KEY,
approved INTEGER
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS trades (
id INTEGER PRIMARY KEY AUTOINCREMENT,
entry REAL,
tp REAL,
sl REAL,
direction TEXT,
status TEXT,
pnl REAL
)
""")

conn.commit()