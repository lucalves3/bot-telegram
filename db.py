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
            CREATE TABLE IF NOT EXISTS watches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                max_unit REAL,
                max_total REAL
            );
            CREATE TABLE IF NOT EXISTS seen (
                watch_id INTEGER NOT NULL,
                asin TEXT NOT NULL,
                last_unit REAL NOT NULL,
                PRIMARY KEY (watch_id, asin)
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        watch_cols = {r["name"]: r for r in c.execute("PRAGMA table_info(watches)")}
        if watch_cols["max_unit"]["notnull"] or "max_total" not in watch_cols:
            # banco antigo: max_unit era obrigatório e não havia max_total
            c.executescript(
                """
                ALTER TABLE watches RENAME TO watches_old;
                CREATE TABLE watches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    max_unit REAL,
                    max_total REAL
                );
                INSERT INTO watches (id, name, url, max_unit)
                    SELECT id, name, url, max_unit FROM watches_old;
                DROP TABLE watches_old;
                """
            )
        cols = {r["name"] for r in c.execute("PRAGMA table_info(items)")}
        if "units" not in cols:
            c.execute("ALTER TABLE items ADD COLUMN units INTEGER")


def add_item(name, url, target=None, selector=None, units=None):
    with conn() as c:
        c.execute(
            "INSERT INTO items (name, url, target, selector, units) VALUES (?, ?, ?, ?, ?)",
            (name, url, target, selector, units),
        )


def set_units(item_id, units):
    with conn() as c:
        cur = c.execute("UPDATE items SET units = ? WHERE id = ?", (units, item_id))
        return cur.rowcount > 0


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


def add_watch(name, url, max_unit=None, max_total=None):
    with conn() as c:
        c.execute(
            "INSERT INTO watches (name, url, max_unit, max_total) VALUES (?, ?, ?, ?)",
            (name, url, max_unit, max_total),
        )
        return c.execute("SELECT * FROM watches WHERE url = ?", (url,)).fetchone()


def list_watches():
    with conn() as c:
        return c.execute("SELECT * FROM watches ORDER BY id").fetchall()


def remove_watch(watch_id):
    with conn() as c:
        cur = c.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
        c.execute("DELETE FROM seen WHERE watch_id = ?", (watch_id,))
        return cur.rowcount > 0


def get_seen(watch_id):
    with conn() as c:
        rows = c.execute("SELECT asin, last_unit FROM seen WHERE watch_id = ?", (watch_id,)).fetchall()
        return {r["asin"]: r["last_unit"] for r in rows}


def set_seen(watch_id, asin, unit):
    with conn() as c:
        c.execute(
            "INSERT INTO seen (watch_id, asin, last_unit) VALUES (?, ?, ?) "
            "ON CONFLICT(watch_id, asin) DO UPDATE SET last_unit = excluded.last_unit",
            (watch_id, asin, unit),
        )


def forget_seen(watch_id, asins):
    with conn() as c:
        c.executemany("DELETE FROM seen WHERE watch_id = ? AND asin = ?", [(watch_id, a) for a in asins])
