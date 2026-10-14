"""SQLite 版的 PK/SK 鍵值儲存（取代雲端版的 DynamoDB，資料模型完全相同）。"""
import json
import sqlite3
import threading
import time

from .config import CLOUD, DB_PATH, TABLE_NAME


class Store:
    def __init__(self, path=DB_PATH):
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS items (
            pk TEXT NOT NULL, sk TEXT NOT NULL, data TEXT NOT NULL, ttl INTEGER,
            PRIMARY KEY (pk, sk))""")
        self._lock = threading.Lock()

    def _row(self, pk, sk, data):
        d = json.loads(data)
        d["PK"], d["SK"] = pk, sk
        return d

    def _alive(self):
        return "(ttl IS NULL OR ttl > ?)"

    def get(self, pk, sk):
        with self._lock:
            r = self._db.execute(f"SELECT data FROM items WHERE pk=? AND sk=? AND {self._alive()}",
                                 (pk, sk, int(time.time()))).fetchone()
        return self._row(pk, sk, r[0]) if r else None

    def put(self, pk, sk, data, ttl=None):
        data = {k: v for k, v in data.items() if k not in ("PK", "SK")}
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO items VALUES (?,?,?,?)",
                             (pk, sk, json.dumps(data, ensure_ascii=False), ttl))

    def put_many(self, rows):
        """rows: [(pk, sk, data), ...]，一次交易寫入。"""
        with self._lock:
            self._db.execute("BEGIN")
            self._db.executemany("INSERT OR REPLACE INTO items VALUES (?,?,?,NULL)",
                                 [(pk, sk, json.dumps(d, ensure_ascii=False)) for pk, sk, d in rows])
            self._db.execute("COMMIT")

    def put_if_absent(self, pk, sk, data):
        with self._lock:
            cur = self._db.execute("INSERT OR IGNORE INTO items VALUES (?,?,?,NULL)",
                                   (pk, sk, json.dumps(data, ensure_ascii=False)))
            return cur.rowcount == 1

    def update(self, pk, sk, **fields):
        """合併欄位；不存在就建立。"""
        with self._lock:
            r = self._db.execute("SELECT data, ttl FROM items WHERE pk=? AND sk=?", (pk, sk)).fetchone()
            data, ttl = (json.loads(r[0]), r[1]) if r else ({}, None)
            data.update(fields)
            self._db.execute("INSERT OR REPLACE INTO items VALUES (?,?,?,?)",
                             (pk, sk, json.dumps(data, ensure_ascii=False), ttl))
        return data

    def incr(self, pk, sk, field, n=1):
        with self._lock:
            r = self._db.execute("SELECT data, ttl FROM items WHERE pk=? AND sk=?", (pk, sk)).fetchone()
            data, ttl = (json.loads(r[0]), r[1]) if r else ({}, None)
            data[field] = int(data.get(field, 0)) + n
            self._db.execute("INSERT OR REPLACE INTO items VALUES (?,?,?,?)",
                             (pk, sk, json.dumps(data, ensure_ascii=False), ttl))
        return data[field]

    def delete(self, pk, sk):
        with self._lock:
            self._db.execute("DELETE FROM items WHERE pk=? AND sk=?", (pk, sk))

    def query(self, pk, prefix="", desc=False, limit=None):
        sql = (f"SELECT sk, data FROM items WHERE pk=? AND sk >= ? AND sk < ? AND {self._alive()} "
               f"ORDER BY sk {'DESC' if desc else 'ASC'}")
        args = [pk, prefix, prefix + "￿", int(time.time())]
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [self._row(pk, sk, d) for sk, d in rows]

    def delete_prefix(self, pk, prefix):
        with self._lock:
            return self._db.execute("DELETE FROM items WHERE pk=? AND sk >= ? AND sk < ?",
                                    (pk, prefix, prefix + "￿")).rowcount

    def purge_expired(self):
        with self._lock:
            return self._db.execute("DELETE FROM items WHERE ttl IS NOT NULL AND ttl <= ?",
                                    (int(time.time()),)).rowcount



if CLOUD:
    from .store_dynamo import DynamoStore
    store = DynamoStore(TABLE_NAME)
else:
    store = Store()
