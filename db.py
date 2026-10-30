import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.getenv("DB_PATH") or Path(__file__).parent / "pricehunter.db")


def conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                target REAL,
                selector TEXT,
                last_price REAL,
                last_alert_price REAL
            );
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                price REAL NOT NULL,
                ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )


def add_item(name, url, target=None, selector=None):
    with conn() as c:
        c.execute(
            "INSERT INTO items (name, url, target, selector) VALUES (?, ?, ?, ?)",
            (name, url, target, selector),
        )


def list_items():
    with conn() as c:
        return c.execute("SELECT * FROM items ORDER BY id").fetchall()


def remove_item(item_id):
    with conn() as c:
        cur = c.execute("DELETE FROM items WHERE id = ?", (item_id,))
        c.execute("DELETE FROM history WHERE item_id = ?", (item_id,))
        return cur.rowcount > 0


def record_price(item_id, price):
    with conn() as c:
        c.execute("INSERT INTO history (item_id, price) VALUES (?, ?)", (item_id, price))
        c.execute("UPDATE items SET last_price = ? WHERE id = ?", (price, item_id))


def set_alert_price(item_id, price):
    with conn() as c:
        c.execute("UPDATE items SET last_alert_price = ? WHERE id = ?", (price, item_id))


def min_price(item_id):
    with conn() as c:
        row = c.execute("SELECT MIN(price) AS m FROM history WHERE item_id = ?", (item_id,)).fetchone()
        return row["m"]


def get_setting(key, default=None):
    with conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with conn() as c:
        c.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
