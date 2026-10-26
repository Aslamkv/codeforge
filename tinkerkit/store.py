import contextlib
from collections import namedtuple
from pathlib import Path
lazy import json
lazy import os
lazy import sqlite3
lazy import time

LogEntry = namedtuple("LogEntry", "id entry")


class Store:
    SCHEMA = (
        "CREATE TABLE IF NOT EXISTS docs (collection TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
        "PRIMARY KEY (collection, key))",
        "CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
        "time TEXT NOT NULL, entry TEXT NOT NULL)",
    )
    LOCK_WAIT_SECONDS = 30

    def __init__(self, path):
        self.path, self.made = Path(path), False

    def get(self, collection, key, default=None):
        with self.transaction() as db:
            return self.read(db, collection, key, default)

    def put(self, collection, key, value):
        with self.transaction(write=True) as db:
            self.write(db, collection, key, value)

    def update(self, collection, key, change, default=None):
        with self.transaction(write=True) as db:
            value = change(self.read(db, collection, key, default))
            self.write(db, collection, key, value)
        return value

    def delete(self, collection, key=None):
        with self.transaction(write=True) as db:
            if key is None:
                db.execute("DELETE FROM docs WHERE collection = ?", (collection,))
            else:
                db.execute("DELETE FROM docs WHERE collection = ? AND key = ?", (collection, key))

    def items(self, collection):
        with self.transaction() as db:
            rows = db.execute(
                "SELECT key, value FROM docs WHERE collection = ? ORDER BY rowid", (collection,)
            ).fetchall()
        return {key: json.loads(value) for key, value in rows}

    def count(self, collection):
        with self.transaction() as db:
            return db.execute("SELECT COUNT(*) FROM docs WHERE collection = ?", (collection,)).fetchone()[0]

    def append(self, log, entry):
        with self.transaction(write=True) as db:
            return db.execute(
                "INSERT INTO logs (name, time, entry) VALUES (?, ?, ?)",
                (log, time.strftime("%Y-%m-%d %H:%M:%S"), json.dumps(entry, ensure_ascii=False)),
            ).lastrowid

    def entries(self, log, after=0):
        with self.transaction() as db:
            rows = db.execute(
                "SELECT id, time, entry FROM logs WHERE name = ? AND id > ? ORDER BY id", (log, after)
            ).fetchall()
        return [LogEntry(row_id, {"time": stamp, **json.loads(entry)}) for row_id, stamp, entry in rows]

    def drop_log(self, log):
        with self.transaction(write=True) as db:
            db.execute("DELETE FROM logs WHERE name = ?", (log,))

    @contextlib.contextmanager
    def transaction(self, write=False):
        db = self.opened()
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.execute("COMMIT")
        except sqlite3.DatabaseError as exc:
            raise self.unreadable(exc) from None
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def opened(self):
        try:
            return self.connect()
        except sqlite3.Error as exc:
            raise self.unreadable(exc) from None

    def unreadable(self, exc):
        return SystemExit(f"{self.path} cannot be read ({exc})")

    def connect(self):
        if not self.path.exists():
            self.create_private_file()
        elif not self.made:
            self.path.chmod(0o600)
        db = sqlite3.connect(self.path, timeout=self.LOCK_WAIT_SECONDS, isolation_level=None)
        if not self.made:
            self.create_tables(db)
        return db

    def create_private_file(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
        self.made = False

    def create_tables(self, db):
        for statement in self.SCHEMA:
            db.execute(statement)
        self.made = True

    @staticmethod
    def read(db, collection, key, default):
        row = db.execute(
            "SELECT value FROM docs WHERE collection = ? AND key = ?", (collection, key)
        ).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def write(db, collection, key, value):
        db.execute(
            "INSERT INTO docs (collection, key, value) VALUES (?, ?, ?) ON CONFLICT (collection, key) "
            "DO UPDATE SET value = excluded.value",
            (collection, key, json.dumps(value, ensure_ascii=False)),
        )


class Log:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def add(self, event):
        if self.name:
            self.store.append(self.name, event)
