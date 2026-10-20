/* 抽卡紀錄（對應 server/gacha.py）：從官方紀錄網址抓資料、存取、統計分析。
 * 資料鍵：PROFILE#{pid} / PULL#{game}#{頻段}#{id}；全站統計：GLOBAL#{game} / P#{pid}
 *
 * 和 Python 版的差別：Workers 免費方案每次執行最多 50 個對外請求，所以匯入改成「分段」：
 * 每段最多抓 PAGES_PER_STEP 頁，狀態存在佇列訊息裡，下一段接著抓。
 * 每抓完一種卡池才更新 APIMAX#{game}#{ft}（增量更新的停止點），中途失敗下次會從頭補齊，不會留下缺口。
 */
import { ApiError, httpJson, nowTs, round1, sleep } from "./common.js";
import { isStandard } from "./games.js";

export const PAGES_PER_STEP = 40;
const MAX_PAGES = 1500;  // 一次匯入最多 1,500 頁（3 萬筆），避免異常回應讓佇列無限接力
const pk = (pid) => `PROFILE#${pid}`;

// ---------------- 抓取 ----------------
export function parseGachaUrl(game, url) {
  const g = game.gacha;
  let u = null;
  try { u = new URL((url || "").trim()); } catch { /* 不是網址 */ }
  let q = u ? Object.fromEntries(u.searchParams) : {};
  if (u && !q.authkey && u.hash.includes("?")) q = Object.fromEntries(new URLSearchParams(u.hash.split("?").slice(1).join("?")));
  if (!q.authkey) throw new ApiError(400, `網址裡找不到 authkey，請確認貼上的是完整的${game.labels.record_name}網址`);
  const isCn = (q.game_biz || "").endsWith(g.cn_biz_suffix) || u.host.includes(g.cn_domain);
  return [g.hosts[isCn ? "cn" : "global"], q];
}

const channelOf = (game, ft) => game.gacha.channel_of[ft] || ft;

export async function savePulls(store, game, pid, channel, items, src, fetchedType = null) {
  const rankMap = game.gacha.rank_map;
  const rows = items.map((it) => [pk(pid), `PULL#${game.id}#${channel}#${it.id}`, {
    id: String(it.id), gacha_type: channel, ft: fetchedType || channel,
    name: it.name || "", item_type: it.item_type || "",
    rank: rankMap[String(it.rank_type)] || it.rank || "B",
    time: it.time || "", game_uid: String(it.uid || ""), src,
  }]);
  await store.putMany(rows);
  return rows.length;
}

/** 新的匯入工作的初始狀態 */
export const newImportState = () => ({ ft: 0, end_id: "0", page: 1, stop: undefined, newest: null, summary: {}, uid: null });

/**
 * 執行一段匯入。回傳 true = 全部完成；false = 還沒完成（state 已更新，交給下一段）。
 */
export async function importStep(store, game, pid, url, state, progress) {
  const g = game.gacha;
  const [host, base] = parseGachaUrl(game, url);
  for (let budget = PAGES_PER_STEP; budget > 0; budget--) {
    if (state.ft >= g.fetch_types.length) break;
    state.pages = (state.pages || 0) + 1;
    if (state.pages > MAX_PAGES) throw new ApiError(400, "紀錄頁數超過上限，匯入已停止");
    const ft = g.fetch_types[state.ft];
    const ch = channelOf(game, ft);
    if (state.stop === undefined) {
      const mark = await store.get(pk(pid), `APIMAX#${game.id}#${ft}`);
      state.stop = mark ? mark.id : null;
    }
    const params = new URLSearchParams({
      ...base, [g.type_param]: ft, size: "20", end_id: state.end_id, page: String(state.page),
      lang: "zh-tw", authkey_ver: base.authkey_ver || "1", sign_type: base.sign_type || "2",
    });
    const data = await httpJson(`${host}${g.path}?${params}`);
    if (data.retcode === -101) throw new ApiError(400, `authkey 已過期，請重新開啟遊戲內的${game.labels.record_name}再取得網址`);
    if (data.retcode !== 0) throw new ApiError(400, `官方 API 錯誤 ${data.retcode}: ${data.message}`);
    const page = (data.data && data.data.list) || [];
    const fresh = [];
    let reachedOld = false;
    for (const item of page) {
      if (state.stop && BigInt(item.id) <= BigInt(state.stop)) { reachedOld = true; break; }
      fresh.push(item);
    }
    if (fresh.length) {
      state.newest = state.newest || fresh[0].id;
      state.uid = state.uid || fresh[0].uid;
      state.summary[ch] = (state.summary[ch] || 0) + (await savePulls(store, game, pid, ch, fresh, "api", ft));
    }
    const label = g.channels[ch];
    await progress(`正在讀取 ${label}…已取得 ${state.summary[ch] || 0} 筆`);
    if (reachedOld || page.length < 20) {
      // 這種卡池抓完了：記錄停止點，換下一種
      if (state.newest) await store.put(pk(pid), `APIMAX#${game.id}#${ft}`, { id: String(state.newest) });
      Object.assign(state, { ft: state.ft + 1, end_id: "0", page: 1, stop: undefined, newest: null });
    } else {
      state.end_id = page[page.length - 1].id;
      state.page += 1;
      await sleep(300);
    }
  }
  if (state.ft < g.fetch_types.length) return false;
  if (state.uid) await store.update(pk(pid), "META", { [`game_uid_${game.id}`]: String(state.uid) });
  return true;
}

// ---------------- 統計 ----------------
export async function loadPulls(store, game, pid, channel = null) {
  const items = await store.query(pk(pid), channel ? `PULL#${game.id}#${channel}#` : `PULL#${game.id}#`);
  items.sort((a, b) => (a.time || "").localeCompare(b.time || "") || (BigInt(a.id) < BigInt(b.id) ? -1 : BigInt(a.id) > BigInt(b.id) ? 1 : 0));
  return items;
}

export async function deletePulls(store, game, pid) {
  const n = await store.deletePrefix(pk(pid), `PULL#${game.id}#`);
  await store.deletePrefix(pk(pid), `APIMAX#${game.id}#`);
  await store.delete(`GLOBAL#${game.id}`, `P#${pid}`);
  return n;
}

function luckLabel(idx) {
  if (idx === null) return "資料不足";
  if (idx >= 25) return "超級歐皇";
  if (idx >= 10) return "歐洲人";
  if (idx > -10) return "普通人";
  if (idx > -25) return "非洲人";
  return "非酋本酋";
}

function channelStats(game, channel, pulls) {
  const g = game.gacha;
  const has5050 = Object.hasOwn(g.standard, channel);
  let counter = 0, aCounter = 0, guaranteed = false;
  let won = 0, lost = 0, aCount = 0, bCount = 0;
  const sHist = [];
  for (const p of pulls) {
    counter++;
    aCounter++;
    if (p.rank === "A") { aCount++; aCounter = 0; }
    else if (p.rank === "B") bCount++;
    if (p.rank !== "S") continue;
    let result = null;
    if (has5050) {
      if (guaranteed) { result = "guaranteed"; guaranteed = false; }
      else if (isStandard(game, channel, p.name)) { result = "lost"; guaranteed = true; lost++; }
      else { result = "won"; won++; }
    }
    sHist.push({ name: p.name, time: p.time, pulls: counter, result });
    counter = 0;
  }
  const sCount = sHist.length;
  const avg = sCount ? round1(sHist.reduce((s, x) => s + x.pulls, 0) / sCount) : null;
  const exp = g.expected[channel];
  const luck = avg ? Math.round(((exp - avg) / exp) * 100) : null;
  const attempts = won + lost;
  return {
    gacha_type: channel, label: g.channels[channel], total: pulls.length,
    s_count: sCount, a_count: aCount, b_count: bCount,
    current_pity: counter, pity_max: g.pity[channel], a_pity: aCounter, guaranteed,
    avg_s_pulls: avg, expected_s_pulls: exp, won_5050: won, lost_5050: lost,
    lose_rate: attempts ? round1((lost / attempts) * 100) : null,
    luck_index: luck, luck_label: luckLabel(luck), s_history: sHist.reverse(),
  };
}

export async function profileStats(store, game, pid) {
  const order = game.gacha.channel_order;
  const byCh = Object.fromEntries(order.map((ch) => [ch, []]));
  for (const p of await loadPulls(store, game, pid)) if (byCh[p.gacha_type]) byCh[p.gacha_type].push(p);
  const stats = Object.fromEntries(order.map((ch) => [ch, channelStats(game, ch, byCh[ch])]));
  const total = Object.values(stats).reduce((s, c) => s + c.total, 0);
  return { total_pulls: total, currency: game.labels.currency, polychrome_spent: total * 160, channels: stats };
}

/** 把個人統計的匿名摘要寫進 GLOBAL#{game}，供全站統計彙整 */
export async function updateGlobal(store, game, pid, stats) {
  const entry = Object.fromEntries(Object.entries(stats.channels).map(([ch, c]) => [ch, {
    s_count: c.s_count, s_pulls_sum: c.s_history.reduce((s, x) => s + x.pulls, 0),
    won: c.won_5050, lost: c.lost_5050, total: c.total,
  }]));
  await store.put(`GLOBAL#${game.id}`, `P#${pid}`, { channels: entry, updated: nowTs() });
}

export async function globalStats(store, game) {
  const rows = await store.query(`GLOBAL#${game.id}`, "P#");
  const out = {};
  for (const ch of game.gacha.channel_order) {
    const totalOf = (key) => rows.reduce((s, r) => s + Number((r.channels[ch] || {})[key] || 0), 0);
    const s = totalOf("s_count"), ps = totalOf("s_pulls_sum"), won = totalOf("won"), lost = totalOf("lost");
    out[ch] = {
      label: game.gacha.channels[ch], total_pulls: totalOf("total"), s_count: s,
      avg_s_pulls: s ? round1(ps / s) : null,
      lose_rate: won + lost ? round1((lost / (won + lost)) * 100) : null,
    };
  }
  return { players: rows.length, channels: out };
}
