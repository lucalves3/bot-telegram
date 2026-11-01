import os
import sqlite3
import time
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
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                watch_id INTEGER NOT NULL,
                asin TEXT NOT NULL,
                prio REAL NOT NULL,
                payload TEXT NOT NULL,
                tries INTEGER NOT NULL DEFAULT 0,
                UNIQUE (watch_id, asin)
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS themes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                max_total REAL,
                min_discount INTEGER NOT NULL DEFAULT 10,
                interval_min INTEGER NOT NULL DEFAULT 25,
                hour_start INTEGER NOT NULL DEFAULT 8,
                hour_end INTEGER NOT NULL DEFAULT 22,
                tag TEXT,
                excludes TEXT NOT NULL DEFAULT '',
                next_post_at REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS theme_terms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                theme_id INTEGER NOT NULL,
                term TEXT NOT NULL COLLATE NOCASE,
                last_checked REAL NOT NULL DEFAULT 0,
                UNIQUE (theme_id, term)
            );
            CREATE TABLE IF NOT EXISTS theme_groups (
                channel TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                theme_id INTEGER NOT NULL,
                PRIMARY KEY (channel, chat_id)
            );
            CREATE TABLE IF NOT EXISTS theme_seen (
                theme_id INTEGER NOT NULL,
                asin TEXT NOT NULL,
                price REAL NOT NULL,
                PRIMARY KEY (theme_id, asin)
            );
            CREATE TABLE IF NOT EXISTS theme_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                theme_id INTEGER NOT NULL,
                asin TEXT NOT NULL,
                prio REAL NOT NULL,
                payload TEXT NOT NULL,
                ts REAL NOT NULL,
                tries INTEGER NOT NULL DEFAULT 0,
                UNIQUE (theme_id, asin)
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
        c.execute("DELETE FROM queue WHERE watch_id = ?", (watch_id,))
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


def enqueue(watch_id, asin, prio, payload):
    with conn() as c:
        c.execute(
            "INSERT INTO queue (watch_id, asin, prio, payload) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(watch_id, asin) DO UPDATE SET prio = excluded.prio, payload = excluded.payload",
            (watch_id, asin, prio, payload),
        )


def queue_counts():
    """{watch_id: quantidade na fila}"""
    with conn() as c:
        rows = c.execute("SELECT watch_id, COUNT(*) AS n FROM queue GROUP BY watch_id").fetchall()
        return {r["watch_id"]: r["n"] for r in rows}


def queue_next():
    """Próximo da fila: alterna entre as categorias e, dentro de cada uma, vai do mais barato."""
    with conn() as c:
        ids = [r["watch_id"] for r in c.execute("SELECT DISTINCT watch_id FROM queue ORDER BY watch_id")]
        if not ids:
            return None
        last = int(get_setting("last_sent_watch", 0))
        pick = next((i for i in ids if i > last), ids[0])
        return c.execute(
            "SELECT * FROM queue WHERE watch_id = ? ORDER BY prio, id LIMIT 1", (pick,)
        ).fetchone()


def queue_done(row):
    with conn() as c:
        c.execute("DELETE FROM queue WHERE id = ?", (row["id"],))
    set_setting("last_sent_watch", row["watch_id"])


def queue_fail(row, max_tries=3):
    """Falha no envio: tenta de novo depois; desiste do item após max_tries."""
    with conn() as c:
        c.execute("UPDATE queue SET tries = tries + 1 WHERE id = ?", (row["id"],))
        c.execute("DELETE FROM queue WHERE id = ? AND tries >= ?", (row["id"], max_tries))
    set_setting("last_sent_watch", row["watch_id"])  # passa a vez, não trava a categoria


# ---------- temas ----------
THEME_FIELDS = {"max_total", "min_discount", "interval_min", "hour_start", "hour_end", "tag", "excludes", "next_post_at"}


def get_theme(name):
    with conn() as c:
        return c.execute("SELECT * FROM themes WHERE name = ?", (name,)).fetchone()


def theme_by_id(theme_id):
    with conn() as c:
        return c.execute("SELECT * FROM themes WHERE id = ?", (theme_id,)).fetchone()


def add_theme(name):
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO themes (name) VALUES (?)", (name,))
    return get_theme(name)


def list_themes():
    with conn() as c:
        return c.execute("SELECT * FROM themes ORDER BY id").fetchall()


def update_theme(theme_id, **fields):
    bad = set(fields) - THEME_FIELDS
    if bad:
        raise ValueError(f"campos inválidos: {bad}")
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE themes SET {sets} WHERE id = ?", (*fields.values(), theme_id))


def remove_theme(theme_id):
    with conn() as c:
        cur = c.execute("DELETE FROM themes WHERE id = ?", (theme_id,))
        for t in ("theme_terms", "theme_groups", "theme_seen", "theme_queue"):
            c.execute(f"DELETE FROM {t} WHERE theme_id = ?", (theme_id,))
        return cur.rowcount > 0


def add_terms(theme_id, terms):
    with conn() as c:
        c.executemany(
            "INSERT OR IGNORE INTO theme_terms (theme_id, term) VALUES (?, ?)", [(theme_id, t) for t in terms]
        )


def remove_term(theme_id, term):
    with conn() as c:
        return c.execute("DELETE FROM theme_terms WHERE theme_id = ? AND term = ?", (theme_id, term)).rowcount > 0


def list_terms(theme_id):
    with conn() as c:
        return [r["term"] for r in c.execute("SELECT term FROM theme_terms WHERE theme_id = ? ORDER BY id", (theme_id,))]


def due_term(min_age_sec):
    """O termo checado há mais tempo, se já passou min_age_sec desde a última checagem."""
    with conn() as c:
        return c.execute(
            "SELECT * FROM theme_terms WHERE last_checked <= ? ORDER BY last_checked, id LIMIT 1",
            (time.time() - min_age_sec,),
        ).fetchone()


def touch_term(term_id):
    with conn() as c:
        c.execute("UPDATE theme_terms SET last_checked = ? WHERE id = ?", (time.time(), term_id))


def link_group(theme_id, channel, chat_id):
    with conn() as c:
        c.execute(
            "INSERT INTO theme_groups (channel, chat_id, theme_id) VALUES (?, ?, ?) "
            "ON CONFLICT(channel, chat_id) DO UPDATE SET theme_id = excluded.theme_id",
            (channel, str(chat_id), theme_id),
        )


def unlink_group(channel, chat_id):
    with conn() as c:
        return c.execute(
            "DELETE FROM theme_groups WHERE channel = ? AND chat_id = ?", (channel, str(chat_id))
        ).rowcount > 0


def theme_groups(theme_id):
    with conn() as c:
        return c.execute("SELECT * FROM theme_groups WHERE theme_id = ?", (theme_id,)).fetchall()


def theme_of_chat(channel, chat_id):
    with conn() as c:
        row = c.execute(
            "SELECT theme_id FROM theme_groups WHERE channel = ? AND chat_id = ?", (channel, str(chat_id))
        ).fetchone()
        return row["theme_id"] if row else None


def theme_seen(theme_id):
    with conn() as c:
        rows = c.execute("SELECT asin, price FROM theme_seen WHERE theme_id = ?", (theme_id,)).fetchall()
        return {r["asin"]: r["price"] for r in rows}


def set_theme_seen(theme_id, asin, price):
    with conn() as c:
        c.execute(
            "INSERT INTO theme_seen (theme_id, asin, price) VALUES (?, ?, ?) "
            "ON CONFLICT(theme_id, asin) DO UPDATE SET price = excluded.price",
            (theme_id, asin, price),
        )


def tq_add(theme_id, asin, prio, payload):
    with conn() as c:
        c.execute(
            "INSERT INTO theme_queue (theme_id, asin, prio, payload, ts) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(theme_id, asin) DO UPDATE SET prio = excluded.prio, payload = excluded.payload, ts = excluded.ts",
            (theme_id, asin, prio, payload, time.time()),
        )


def tq_next(theme_id, max_age_sec):
    """Melhor oferta da fila do tema; descarta as antigas (o preço já pode ter mudado)."""
    with conn() as c:
        c.execute("DELETE FROM theme_queue WHERE ts < ?", (time.time() - max_age_sec,))
        return c.execute(
            "SELECT * FROM theme_queue WHERE theme_id = ? ORDER BY prio, id LIMIT 1", (theme_id,)
        ).fetchone()


def tq_done(row):
    with conn() as c:
        c.execute("DELETE FROM theme_queue WHERE id = ?", (row["id"],))


def tq_fail(row, max_tries=3):
    with conn() as c:
        c.execute("UPDATE theme_queue SET tries = tries + 1 WHERE id = ?", (row["id"],))
        c.execute("DELETE FROM theme_queue WHERE id = ? AND tries >= ?", (row["id"], max_tries))


def tq_counts():
    with conn() as c:
        rows = c.execute("SELECT theme_id, COUNT(*) AS n FROM theme_queue GROUP BY theme_id").fetchall()
        return {r["theme_id"]: r["n"] for r in rows}
