/* D1 版的 PK/SK 鍵值儲存：資料表與 server/store.py（SQLite）完全相同，只是改成非同步。
 *   items(pk, sk, data JSON, ttl)
 */
import { nowTs } from "./common.js";

export const SCHEMA = `CREATE TABLE IF NOT EXISTS items (
  pk TEXT NOT NULL, sk TEXT NOT NULL, data TEXT NOT NULL, ttl INTEGER,
  PRIMARY KEY (pk, sk)) WITHOUT ROWID`;
const ALIVE = "(ttl IS NULL OR ttl > ?)";
const MAX_CHAR = "￿";

const strip = (data) => {
  const { PK, SK, ...rest } = data || {};
  return JSON.stringify(rest);
};
const row = (pk, sk, data) => ({ ...JSON.parse(data), PK: pk, SK: sk });

export class Store {
  constructor(db) {
    this.db = db;
  }

  async get(pk, sk) {
    const r = await this.db.prepare(`SELECT data FROM items WHERE pk=? AND sk=? AND ${ALIVE}`).bind(pk, sk, nowTs()).first();
    return r ? row(pk, sk, r.data) : null;
  }

  async put(pk, sk, data, ttl = null) {
    await this.db.prepare("INSERT OR REPLACE INTO items VALUES (?,?,?,?)").bind(pk, sk, strip(data), ttl).run();
  }

  /** rows: [[pk, sk, data], ...]，分批交易寫入 */
  async putMany(rows) {
    const stmt = this.db.prepare("INSERT OR REPLACE INTO items VALUES (?,?,?,NULL)");
    for (let i = 0; i < rows.length; i += 100) {
      await this.db.batch(rows.slice(i, i + 100).map(([pk, sk, d]) => stmt.bind(pk, sk, strip(d))));
    }
  }

  /** 合併欄位；不存在就建立（保留原本的 ttl） */
  async update(pk, sk, fields) {
    const r = await this.db.prepare("SELECT data, ttl FROM items WHERE pk=? AND sk=?").bind(pk, sk).first();
    const data = { ...(r ? JSON.parse(r.data) : {}), ...fields };
    await this.db.prepare("INSERT OR REPLACE INTO items VALUES (?,?,?,?)").bind(pk, sk, strip(data), r ? r.ttl : null).run();
    return data;
  }

  /** 原子遞增計數器（配額用），回傳遞增後的值 */
  async incr(pk, sk, ttl) {
    const r = await this.db.prepare(`INSERT INTO items VALUES (?, ?, '{"n":1}', ?)
      ON CONFLICT(pk, sk) DO UPDATE SET data = json_set(data, '$.n', json_extract(data, '$.n') + 1)
      RETURNING json_extract(data, '$.n') AS n`).bind(pk, sk, ttl).first();
    return Number(r.n);
  }

  /** 只有欄位 field 目前等於 expect 時才合併 fields（原子操作）；回傳是否成功 */
  async updateIf(pk, sk, field, expect, fields) {
    const r = await this.db.prepare(`UPDATE items SET data = json_patch(data, ?)
      WHERE pk=? AND sk=? AND json_extract(data, ?) = ?`).bind(JSON.stringify(fields), pk, sk, `$.${field}`, expect).run();
    return r.meta.changes === 1;
  }

  async delete(pk, sk) {
    await this.db.prepare("DELETE FROM items WHERE pk=? AND sk=?").bind(pk, sk).run();
  }

  async query(pk, prefix = "", { desc = false, limit = null } = {}) {
    let sql = `SELECT sk, data FROM items WHERE pk=? AND sk >= ? AND sk < ? AND ${ALIVE} ORDER BY sk ${desc ? "DESC" : "ASC"}`;
    const args = [pk, prefix, prefix + MAX_CHAR, nowTs()];
    if (limit) {
      sql += " LIMIT ?";
      args.push(limit);
    }
    const { results } = await this.db.prepare(sql).bind(...args).all();
    return results.map((r) => row(pk, r.sk, r.data));
  }

  async deletePrefix(pk, prefix) {
    const r = await this.db.prepare("DELETE FROM items WHERE pk=? AND sk >= ? AND sk < ?").bind(pk, prefix, prefix + MAX_CHAR).run();
    return r.meta.changes;
  }

  async purgeExpired() {
    const r = await this.db.prepare("DELETE FROM items WHERE ttl IS NOT NULL AND ttl <= ?").bind(nowTs()).run();
    return r.meta.changes;
  }
}
