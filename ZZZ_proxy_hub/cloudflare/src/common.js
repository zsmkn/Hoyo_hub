/* 共用工具：錯誤、ID、外部 HTTP 請求（對應 server/common.py） */

export const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36";

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export const nowTs = () => Math.floor(Date.now() / 1000);
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
/** Python 的 round(x, 1) */
export const round1 = (x) => Math.round(x * 10) / 10;

const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
const b64url = (bytes) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

export async function sha256(s) {
  return hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s)));
}

export async function hmac(key, msg) {
  const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return hex(await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(msg)));
}

/* ---------- 加密（HoYoLAB Cookie 加密後才存進 D1；資料庫外洩也拿不到可用的 Cookie） ---------- */
const ENC_PREFIX = "enc1:";

async function aesKey(secret) {
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), "HKDF", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "HKDF", hash: "SHA-256", salt: new TextEncoder().encode("hoyo-hub"), info: new TextEncoder().encode("hoyolab-cookie") },
    base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}

export async function encrypt(secret, text) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await aesKey(secret), new TextEncoder().encode(text)));
  const all = new Uint8Array(iv.length + ct.length);
  all.set(iv);
  all.set(ct, iv.length);
  return ENC_PREFIX + btoa(String.fromCharCode(...all));
}

export async function decrypt(secret, value) {
  if (!String(value || "").startsWith(ENC_PREFIX)) return value;  // 舊資料（未加密）
  const all = Uint8Array.from(atob(value.slice(ENC_PREFIX.length)), (c) => c.charCodeAt(0));
  const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: all.slice(0, 12) }, await aesKey(secret), all.slice(12));
  return new TextDecoder().decode(pt);
}

/** Worker 的秘密金鑰（上傳簽章、Cookie 加密）；沒有設定時直接拒絕，不用預設值 */
export function signingKey(env) {
  if (!env.SIGNING_KEY || env.SIGNING_KEY.length < 32) throw new ApiError(503, "伺服器尚未設定金鑰，請重新執行部署腳本");
  return env.SIGNING_KEY;
}

/** 長度 n 的十六進位 ID（同 secrets.token_hex(n // 2)） */
export const newId = (n = 10) => hex(crypto.getRandomValues(new Uint8Array(Math.floor(n / 2))));
export const newToken = (nbytes = 32) => b64url(crypto.getRandomValues(new Uint8Array(nbytes)));

/** 固定時間比較，避免從回應時間猜出金鑰 */
export function safeEqual(a, b) {
  a = String(a || "");
  b = String(b || "");
  let diff = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i++) diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return diff === 0 && a.length > 0;
}

export async function httpJson(url, { method = "GET", headers = {}, body } = {}) {
  const h = { "User-Agent": USER_AGENT, Accept: "application/json", ...headers };
  if (body !== undefined) h["Content-Type"] = h["Content-Type"] || "application/json";
  const r = await fetch(url, { method, headers: h, body: body !== undefined ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(15000) });
  const raw = await r.text();
  try {
    return raw ? JSON.parse(raw) : {};
  } catch {
    throw new ApiError(502, `外部服務錯誤 HTTP ${r.status}`);
  }
}

/** 發送 Discord Webhook 通知（失敗不影響主流程） */
export async function postDiscordWebhook(webhookUrl, content) {
  if (!webhookUrl) return;
  try {
    await fetch(webhookUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", "User-Agent": USER_AGENT },
      // allowed_mentions：訊息內容來自外部 API，不讓它觸發 @everyone / @here
      body: JSON.stringify({ content: content.slice(0, 1900), allowed_mentions: { parse: [] } }),
      signal: AbortSignal.timeout(10000),
    });
  } catch (e) {
    console.log("webhook failed:", e.message);
  }
}
