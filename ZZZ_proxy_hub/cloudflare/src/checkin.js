/* HoYoLAB 每日簽到（對應 server/checkin.py；三款遊戲共用同一組 Cookie）
 * 設定：PROFILE#{pid} / HOYOLAB；紀錄：PROFILE#{pid} / CHECKIN#{game}#{date}；名單：CHECKIN_SET#{game} / {pid}
 * 排程：Cron Trigger 每小時一次，00:10（UTC+8）後把還沒簽到的人丟進佇列，一人一則訊息
 *       （免費方案每次執行最多 50 個對外請求，一次簽完所有人會超過）。
 */
import { decrypt, httpJson, nowTs, postDiscordWebhook, signingKey } from "./common.js";
import { GAMES } from "./games.js";

const headers = (game, cookie) => ({
  Cookie: cookie, Origin: "https://act.hoyolab.com", Referer: "https://act.hoyolab.com/",
  "x-rpc-app_version": "2.34.1", "x-rpc-client_type": "4", ...game.checkin.headers,
});

/** 只保留簽到需要的三個欄位；cookie_token_v2 等權限較大的欄位一律丟掉 */
export function normalizeCookie(raw) {
  const pairs = {};
  for (const m of (raw || "").matchAll(/([A-Za-z0-9_]+)=([^;\s]+)/g)) pairs[m[1]] = m[2];
  const picked = ["ltoken_v2", "ltuid_v2", "ltmid_v2"].filter((k) => pairs[k]).map((k) => [k, pairs[k]]);
  const p = Object.fromEntries(picked);
  if (!p.ltoken_v2 || !p.ltuid_v2) return [null, null];
  return [picked.map(([k, v]) => `${k}=${v}`).join("; "), p.ltuid_v2];
}

async function doCheckin(game, cookie) {
  const c = game.checkin;
  const h = headers(game, cookie);
  const q = `?lang=zh-tw&act_id=${c.act_id}`;
  const info = await httpJson(`${c.base}/info${q}`, { headers: h });
  if (info.retcode !== 0) return { status: "error", message: `Cookie 無效或過期（${info.retcode}: ${info.message}）` };

  let status, days;
  if (info.data.is_sign) {
    status = "already";
    days = info.data.total_sign_day || 0;
  } else {
    const sign = await httpJson(`${c.base}/sign?lang=zh-tw`, { method: "POST", headers: h, body: { act_id: c.act_id } });
    const gt = (sign.data && sign.data.gt_result) || {};
    if (sign.retcode === -5003) status = "already";
    else if (sign.retcode !== 0) return { status: "error", message: `簽到失敗（${sign.retcode}: ${sign.message}）` };
    else if (gt.is_risk || (gt.risk_code !== undefined && gt.risk_code !== null && gt.risk_code !== 0))
      return { status: "captcha", message: "HoYoLAB 要求人機驗證，請手動到網頁簽到一次" };
    else status = "signed";
    days = (info.data.total_sign_day || 0) + (status === "signed" ? 1 : 0);
  }

  let reward = null;
  const home = await httpJson(`${c.base}/home${q}`, { headers: h });
  const awards = (home.data && home.data.awards) || [];
  if (days > 0 && days <= awards.length) reward = `${awards[days - 1].name} x${awards[days - 1].cnt}`;
  return { status, message: { signed: "簽到成功", already: "今天已經簽到過了" }[status], total_days: days, reward };
}

/** 台灣時間的日期 YYYY-MM-DD */
export const today = () => new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10);

export async function runForProfile(env, store, game, pid, notify = true) {
  const cfg = await store.get(`PROFILE#${pid}`, "HOYOLAB");
  if (!cfg) return { status: "error", message: "尚未設定 HoYoLAB Cookie" };
  let result;
  try {
    result = await doCheckin(game, await decrypt(signingKey(env), cfg.cookie));
  } catch (e) {
    result = { status: "error", message: `連線失敗：${e.message}` };
  }
  Object.assign(result, { date: today(), at: nowTs(), game: game.id });
  const key = `CHECKIN#${game.id}#${result.date}`;
  const prev = (await store.get(`PROFILE#${pid}`, key)) || {};
  await store.put(`PROFILE#${pid}`, key, { ...result, attempts: Number(prev.attempts || 0) + 1 }, nowTs() + 90 * 86400);
  const last = { ...(cfg.last || {}), [game.id]: result };
  await store.update(`PROFILE#${pid}`, "HOYOLAB", { last });
  if (notify && cfg.webhook) {
    const icon = { signed: "✅", already: "☑️" }[result.status] || "⚠️";
    let text = `${icon} ${game.name} HoYoLAB 簽到｜${result.message}`;
    if (result.reward) text += `\n🎁 今日獎勵：${result.reward}（本月第 ${result.total_days} 天）`;
    await postDiscordWebhook(cfg.webhook, text);
  }
  return result;
}

/** 列出今天還需要簽到的 [game, pid]（每遊戲每天最多重試 3 次） */
export async function dueList(store) {
  const out = [];
  for (const gid of Object.keys(GAMES)) {
    for (const m of await store.query(`CHECKIN_SET#${gid}`)) {
      const done = (await store.get(`PROFILE#${m.SK}`, `CHECKIN#${gid}#${today()}`)) || {};
      if (["signed", "already"].includes(done.status) || Number(done.attempts || 0) >= 3) continue;
      out.push([gid, m.SK]);
    }
  }
  return out;
}
