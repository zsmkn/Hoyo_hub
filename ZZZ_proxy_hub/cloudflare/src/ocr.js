/* OCR（對應 server/ocr.py）：抽卡紀錄截圖、裝備截圖。
 * 本機版用 RapidOCR 取得每段文字的座標；這裡改用 Workers AI 的視覺模型逐列轉錄，
 * 再把「第 i 列第 j 格」換成相對座標，後面的解析規則（模糊比對、欄位判斷）與 Python 版完全相同。
 */
import { agentByName, COMMON_ALIASES } from "./games.js";

export const OCR_MODEL = "@cf/meta/llama-4-scout-17b-16e-instruct";
// 實測（合成截圖）：Llama 4 Scout 約 8 秒、錯字最少；沒特別說明時它會把繁體轉成簡體，所以提示詞要明講
const PROMPT = `Transcribe ALL text in this game screenshot exactly as shown. If the text is Chinese it is usually Traditional Chinese (繁體中文): keep every character exactly as written, do NOT convert to Simplified Chinese, do not translate. English stays English.
Output one line per visual row, top to bottom. Within a row, separate different columns / text blocks with " | ".
Keep numbers, %, +, dates and times exactly. Output only the transcription, no explanation, no markdown.`;

function toBase64(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

/** 回傳 [{text, top, left, h}]（0~1 的相對座標），格式同 Python 版 detect_lines */
export async function detectLines(env, bytes, contentType) {
  const out = await env.AI.run(OCR_MODEL, {
    messages: [{ role: "user", content: [
      { type: "text", text: PROMPT },
      { type: "image_url", image_url: { url: `data:${contentType};base64,${toBase64(bytes)}` } },
    ] }],
    max_tokens: 2048, temperature: 0,
  });
  // 舊模型回 {response}，新模型回 OpenAI 格式 {choices: [{message: {content}}]}
  const msg = out.choices && out.choices[0] && out.choices[0].message;
  const text = String((typeof out.response === "string" ? out.response : msg && msg.content) || "");
  const rows = text.replace(/```[a-z]*\n?/g, "").split("\n").map((r) => r.trim()).filter(Boolean);
  const n = rows.length || 1;
  const lines = [];
  rows.forEach((r, i) => {
    const cells = r.split(/\s*\|\s*/).map((c) => c.trim()).filter(Boolean);
    cells.forEach((c, j) => lines.push({ text: c, top: i / n, left: j / Math.max(cells.length, 1), h: 0.5 / n }));
  });
  return lines;
}

/** 依 Y 座標把同一列的文字合併 */
export function groupRows(lines) {
  const rows = [];
  for (const ln of [...lines].sort((a, b) => a.top - b.top)) {
    const center = ln.top + ln.h / 2;
    const row = rows.find((r) => Math.abs(r.center - center) < Math.max(ln.h, r.h) * 0.6);
    if (row) row.cells.push(ln);
    else rows.push({ center, h: ln.h, cells: [ln] });
  }
  for (const r of rows) r.cells.sort((a, b) => a.left - b.left);
  return rows;
}

// ---------------- 抽卡紀錄 ----------------
const TIME_RE = /(\d{4})[-/.](\d{2})[-/.](\d{2})\s*(\d{2}):(\d{2}):(\d{2})/;
const TIME_RE_G = new RegExp(TIME_RE.source, "g");
const HEADER_WORDS = new Set([
  "item name", "item type", "time", "obtained", "time obtained", "time received", "name", "type",
  "signal search", "wish history", "warp history", "history", "previous", "next", "page",
  "物品名稱", "物品名称", "物品類型", "物品类型", "獲取時間", "获取时间", "調頻類型", "调频类型",
  "調頻紀錄", "调频记录", "祈願類型", "祈愿类型", "躍遷類型", "跃迁类型", "名稱", "名称", "類型", "类型", "時間", "时间",
  "上一頁", "下一頁", "上一页", "下一页",
]);
const STAR = { 5: "S", 4: "A", 3: "B", 五: "S", 四: "A", 三: "B" };
const RANK_RES = [
  [/\(?\b([SAB])[- ]?Rank\b\)?/, (m) => m[1]],
  [/\[([SAB])\]/, (m) => m[1]],
  [/([SAB])\s*[級级]/, (m) => m[1]],
  [/[(（]\s*([345五四三])\s*[-\s]?(?:star|星)\s*[)）]/i, (m) => STAR[m[1]]],
];
const CJK = /[぀-ヿ一-鿿]/;

/** 逐字比對開頭：OCR 認錯繁體字通常是「替換一個字」，看同位置相同字的比例 */
function cjkScore(text, alias) {
  const seg = [...text].slice(0, [...alias].length), a = [...alias];
  return seg.reduce((s, ch, i) => s + (ch === a[i] ? 1 : 0), 0) / a.length;
}

/** OCR 結果與關鍵字是否「差不多」 */
function fuzzy(tok, word, threshold) {
  if (tok.includes(word)) return true;
  if (!(CJK.test(tok) && CJK.test(word)) || [...tok].length > [...word].length + 1) return false;
  return cjkScore(tok, word) >= threshold;
}

export function parseGacha(game, lines) {
  const ocr = game.ocr;
  const channelLabels = Object.values(game.gacha.channels);
  const itemTypes = ocr.item_types;
  const isNoise = (s) => {
    const low = s.toLowerCase().trim();
    return ocr.channel_words.some((w) => low.includes(w)) || HEADER_WORDS.has(low) || channelLabels.some((lab) => fuzzy(s, lab, 0.6));
  };
  const out = [];
  for (const row of groupRows(lines)) {
    const text = row.cells.map((c) => c.text).join(" ");
    const m = text.match(TIME_RE);
    if (!m) continue;
    const t = `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}`;
    let itemType = "", rank = null;
    const nameParts = [];
    for (const c of row.cells) {
      let cell = c.text.replace(TIME_RE_G, " ").trim();
      for (const [rx, conv] of RANK_RES) {
        const rm = cell.match(rx);
        if (rm) {
          rank = conv(rm);
          cell = cell.replace(new RegExp(rx.source, rx.flags + "g"), " ").trim();
        }
      }
      const low = cell.toLowerCase();
      if (!cell || isNoise(cell)) continue;
      if (Object.hasOwn(itemTypes, low)) { itemType = itemTypes[low]; continue; }
      // OCR 可能把相鄰格子合併（例：「光 輪契」），逐詞判斷
      for (const tok of cell.split(/\s+/)) {
        const fz = Object.entries(itemTypes).find(([k]) => fuzzy(tok, k, 0.5));
        if (Object.hasOwn(itemTypes, tok.toLowerCase()) || (fz && [...tok].length <= 3)) itemType = itemTypes[tok.toLowerCase()] || fz[1];
        else if (!isNoise(tok)) nameParts.push(tok);
      }
    }
    let name = nameParts.join(" ").trim().replace(/^[ \-|]+|[ \-|]+$/g, "");
    if (!name) continue;
    const agent = agentByName(game, name);
    if (agent) {
      name = agent.name;
      rank = rank || agent.rank;
      itemType = itemType || game.labels.agent;
    }
    out.push({ name, item_type: itemType, rank: rank || "B", time: t });
  }
  return out; // 由上到下 = 新到舊
}

/** 幫 OCR 資料產生穩定 id：時間戳 + 同一時間內的序號（越舊序號越小）。時間以 UTC 解讀（同 Lambda）。 */
export function rowsToPulls(rows) {
  const pulls = [], seen = {};
  for (const r of [...rows].reverse()) {
    const seq = seen[r.time] || 0;
    seen[r.time] = seq + 1;
    const [d, tm] = r.time.split(" ");
    const [Y, M, D] = d.split("-").map(Number), [h, mi, s] = tm.split(":").map(Number);
    const ts = Math.floor(Date.UTC(Y, M - 1, D, h, mi, s) / 1000);
    pulls.push({ ...r, id: String(ts).padStart(10, "0") + String(seq).padStart(9, "0") });
  }
  return pulls;
}

// ---------------- 裝備 ----------------
const NUM_RE = /(\d+(?:\.\d+)?)\s*(%)?/g;
const DMG_BONUS_WORDS = ["dmg bonus", "傷害加成", "伤害加成"];
const BULLETS = "·・•●◆◇-–— ";
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function matchStat(game, text) {
  const aliases = [...game.ocr.stat_aliases, ...COMMON_ALIASES];
  let i = 0;
  while (i < text.length && BULLETS.includes(text[i])) i++;
  text = text.slice(i);
  const isCjk = CJK.test(text);
  const low = isCjk ? text.toLowerCase().replace(/ /g, "") : text.toLowerCase();
  for (let w of DMG_BONUS_WORDS) {
    w = isCjk ? w.replace(/ /g, "") : w;
    if (low.includes(w) || (isCjk && low.includes("加成") && !low.includes("治"))) {
      const idx = low.includes(w) ? low.indexOf(w) : low.indexOf("加成");
      return ["dmg_bonus", low.slice(idx)];
    }
  }
  if (isCjk) {
    let best = null, bestScore = 0;
    for (const [alias, key] of aliases) {
      if (!CJK.test(alias)) continue;
      const s = cjkScore(low, alias);
      if (s > bestScore || (s === bestScore && best && alias.length > best[0].length)) { best = [alias, key]; bestScore = s; }
    }
    if (best && bestScore >= 0.6) return [best[1], low.slice(best[0].length)];
    return [null, null];
  }
  for (const [alias, key] of aliases) {
    if (!CJK.test(alias) && new RegExp(`^${escapeRe(alias)}\\b`).test(low)) return [key, low.slice(alias.length)];
  }
  return [null, null];
}

export function parseDisc(game, lines) {
  const slotWords = game.ocr.slot_words;
  let main = null, slot = null, inSubs = false, pending = null;
  const subs = [];
  const add = (key, val, pct) => {
    if (pct && ["atk", "hp", "def"].includes(key)) key += "_pct";
    const entry = { stat: key, value: Number(val) };
    if (main === null && !inSubs) main = entry;
    else if (subs.length < 4) subs.push(entry);
  };
  for (const row of groupRows(lines)) {
    const text = row.cells.map((c) => c.text).join(" ").trim();
    const low = text.toLowerCase();
    if (!slot) {
      const sm = low.match(/\[\s*(\d)\s*\]|【(\d)】|slot\s*(\d)/);
      if (sm) slot = Number(sm[1] || sm[2] || sm[3]);
      else {
        const compact = low.replace(/ /g, "");
        const hit = Object.entries(slotWords).find(([k]) =>
          compact.includes(k) || low.includes(k) || ([...compact].length <= [...k].length + 1 && fuzzy(compact, k, 0.6)));
        slot = hit ? hit[1] : null;
      }
    }
    // 標題列：英文，或「主／副」開頭的短字串（OCR 常漏字）
    const shortCjk = [...low].length <= 4 && !/\d/.test(low);
    if (["sub-stat", "sub stat", "substat"].some((w) => low.includes(w)) || (shortCjk && low.startsWith("副"))) { inSubs = true; pending = null; continue; }
    if (low.includes("main stat") || (shortCjk && low.startsWith("主"))) { inSubs = false; pending = null; continue; }
    // 主詞條名稱和數值常常分在上下兩行
    const bare = text.replace(/ /g, "").match(/^\+?\s*(\d+(?:\.\d+)?)\s*(%)?$/);
    if (pending && bare) { add(pending, bare[1], bare[2]); pending = null; continue; }
    // 先去掉絕區零的強化次數標記「+2 14.4%」
    const [key, rest] = matchStat(game, text.replace(/\+\d(?=\s+\d)/g, " "));
    if (!key) continue;
    const nums = [...rest.matchAll(NUM_RE)];
    if (!nums.length) { pending = main === null ? key : null; continue; }
    pending = null;
    const last = nums[nums.length - 1];
    add(key, last[1], last[2]);
  }
  return { slot, main, subs };
}
