/* HoYo Proxy Hub — Cloudflare Workers 版進入點
 *   fetch      /api/*（路由見 routes.js）、/gallery/*（KV 作品檔案）、/get_gacha_url_{game}.ps1、其他交給靜態檔案
 *   queue      背景工作（抽卡網址匯入、OCR、自動簽到）
 *   scheduled  每小時：把今天還沒簽到的人排進佇列、清除過期資料
 */
import { DEFAULT_GAME } from "./games.js";
import { handleApi, renderScript, runAction } from "./routes.js";
import { dueList } from "./checkin.js";
import { Store } from "./store.js";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
  "Access-Control-Allow-Headers": "content-type,authorization,x-bot-key",
  "Access-Control-Max-Age": "3600",
};
const SECURITY = { "X-Content-Type-Options": "nosniff", "Referrer-Policy": "strict-origin-when-cross-origin" };

const json = (status, body, extra = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json; charset=utf-8", ...CORS, ...SECURITY, ...extra } });

/** 限流（取代 API Gateway 的 throttling）：每個 IP 每 10 秒 300 個請求；/api/ping 每 10 秒 20 個 */
async function limited(env, request, path) {
  const ip = request.headers.get("cf-connecting-ip") || "local";
  const limiter = path.endsWith("/ping") ? env.PING_LIMIT : env.API_LIMIT;
  if (!limiter) return false;
  const { success } = await limiter.limit({ key: ip });
  return !success;
}

async function serveFile(env, path) {
  const notFound = () => new Response("Not Found", { status: 404, headers: { ...CORS, ...SECURITY } });
  // 先確認作品還在 D1（KV 刪除要約 60 秒才會全球同步，刪掉的作品要立刻讀不到）
  const m = path.match(/^\/gallery\/([a-f0-9]{16})\/(image|depth|thumb)\.(png|jpg|webp)$/);
  if (!m || !(await new Store(env.DB).get(`DEPTH#${m[1]}`, "META"))) return notFound();
  const obj = await env.FILES.getWithMetadata(path.slice(1), "stream");
  if (!obj.value) return notFound();
  return new Response(obj.value, { headers: {
    "Content-Type": (obj.metadata && obj.metadata.ct) || "application/octet-stream",
    "Cache-Control": "public, max-age=86400", ...CORS, ...SECURITY,
  } });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    if (path.startsWith("/api/")) {
      if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
      if (await limited(env, request, path)) return json(429, { error: "請求太頻繁，請稍後再試" }, { "Retry-After": "10" });
      const [status, body] = await handleApi(request, env, new Store(env.DB), url);
      return json(status, body);
    }
    if (path.startsWith("/gallery/") && (request.method === "GET" || request.method === "HEAD")) return serveFile(env, path);
    const ps = path.match(/^\/get_gacha_url(?:_([a-z]+))?\.ps1$/);
    if (ps) {
      const text = renderScript(ps[1] || DEFAULT_GAME);
      if (text) return new Response(text, { headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-cache", ...SECURITY } });
    }
    return env.ASSETS.fetch(request);
  },

  async queue(batch, env) {
    const store = new Store(env.DB);
    for (const msg of batch.messages) {
      try {
        await runAction(env, store, msg.body);
      } catch (e) {
        // 不記錄整則訊息：網址匯入的訊息裡有 authkey（等同登入憑證）
        console.log("job failed", msg.body && msg.body.action, msg.body && msg.body.job_id, e.stack);
      }
      msg.ack();  // 錯誤已寫進工作狀態，不自動重試（避免重複匯入、重複簽到）
    }
  },

  async scheduled(event, env, ctx) {
    const store = new Store(env.DB);
    const tw = new Date(Date.now() + 8 * 3600 * 1000);
    if (!(tw.getUTCHours() === 0 && tw.getUTCMinutes() < 10)) {
      const due = await dueList(store);
      for (let i = 0; i < due.length; i += 100) {
        await env.JOBS.sendBatch(due.slice(i, i + 100).map(([game, pid]) => ({ body: { action: "checkin", game, pid } })));
      }
      console.log(`[checkin] queued ${due.length}`);
    }
    ctx.waitUntil(store.purgeExpired());
  },
};
