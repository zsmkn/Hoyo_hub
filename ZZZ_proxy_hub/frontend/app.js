/* HoYo Proxy Hub 前端：絕區零 / 原神 / 崩壞：星穹鐵道 */
const API = (window.APP_CONFIG && window.APP_CONFIG.API_BASE || "").replace(/\/$/, "");
const SITE = location.origin + location.pathname.replace(/[^/]*$/, "");
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = { token: null, pid: null, game: "zzz", games: [], external: [], meta: null, stats: null, channel: null, chart: null, tab: "gacha" };
const L = (k) => (state.meta && state.meta.labels[k]) || "";
/** 依遊戲設定的順序排列卡池（物件的數字 key 會被 JS 自動排序） */
const ordered = (obj) => (state.meta ? state.meta.channel_order : Object.keys(obj)).filter((k) => obj[k]).map((k) => obj[k]);
const rankName = (r) => (r === "S" ? L("rank_S") : r === "A" ? L("rank_A") : r === "B" ? "3★" : r);

function store(k, v) { try { v === undefined ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) { /* 無痕模式 */ } }
function load(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = "toast"), 2600);
}
function setStatus(id, msg, cls = "") { const el = $(id); el.textContent = msg; el.className = "status " + cls; }

async function api(path, { method = "GET", body, auth = true } = {}) {
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  if (auth && state.token) headers.Authorization = "Bearer " + state.token;
  let r;
  try {
    r = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  } catch (e) {
    throw new Error("無法連線到伺服器，請確認 start.bat 正在執行");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.error || `HTTP ${r.status}`), { status: r.status });
  return data;
}
/** 目前遊戲的 API：/api/xxx → /api/{game}/xxx */
const gapi = (path, opts) => api(`/api/${state.game}/${path.replace(/^\/api\//, "")}`, opts);

async function withBtn(btn, fn) {
  btn.disabled = true;
  try { return await fn(); } finally { btn.disabled = false; }
}

async function pollJob(jobId, onUpdate, maxSeconds = 300) {
  for (let i = 0; i < maxSeconds / 1.5; i++) {
    const job = await api(`/api/jobs/${jobId}`);
    onUpdate(job);
    if (job.status === "done" || job.status === "error") return job;
    await new Promise((r) => setTimeout(r, 1500));
  }
  throw new Error("等待逾時");
}

async function uploadImage(file, kind, extra = {}, maxMB = 5) {
  if (file.size > maxMB * 1024 * 1024) throw new Error(`圖片請小於 ${maxMB}MB`);
  const ctype = ["image/jpeg", "image/webp"].includes(file.type) ? file.type : "image/png";
  const { job_id, upload_url } = await gapi("/api/upload-url", { method: "POST", body: { kind, content_type: ctype, ...extra } });
  const r = await fetch(upload_url, { method: "PUT", headers: { "Content-Type": ctype }, body: file });
  if (!r.ok) throw new Error("圖片上傳失敗");
  return job_id;
}

/* ---------------- 身分 ---------------- */
async function ensureProfile() {
  state.token = load("zzz_token");
  if (state.token) {
    try {
      const me = await api("/api/profile");
      state.pid = me.profile_id;
      return me;
    } catch (e) {
      if (e.status !== 401) throw e;  // 只有 token 真的無效才重建，連線失敗不能弄丟身分
      state.token = null;
    }
  }
  const p = await api("/api/profile", { method: "POST", auth: false });
  state.token = p.token; state.pid = p.profile_id;
  store("zzz_token", p.token);
  return api("/api/profile");
}

function renderMe(me) {
  $("#me-pid").value = state.pid;
  $("#me-token").value = state.token;
  $("#discord-status").textContent = me.discord_id ? `已綁定 Discord：${me.discord_id}` : "尚未綁定 Discord";
}

/* ---------------- 遊戲切換 ---------------- */
function renderGameMenu() {
  // 本站提供的遊戲 + 連到其他網站的遊戲（例如雲端版的絕區零在另一個站台）
  const ext = (state.external || []).map((g) => ({ ...g, external: true }));
  $("#game-menu").innerHTML = [...state.games, ...ext].map((g) => `
    <button role="menuitem" data-game="${g.id}" ${g.external ? `data-url="${esc(g.url)}"` : ""} class="${g.id === state.game ? "active" : ""}" style="--g:${g.accent}">
      <span class="mini-logo">${esc(g.short)}</span><span>${esc(g.name)}</span>${g.external ? '<span class="ext">↗</span>' : ""}
    </button>`).join("");
  $$("#game-menu [data-game]").forEach((b) => (b.onclick = () => {
    toggleMenu(false);
    if (b.dataset.url) location.href = b.dataset.url;
    else switchGame(b.dataset.game);
  }));
}

function applyMode(mode) {
  state.mode = mode;
  if (mode === "cloudflare") {
    $("#mode-label").textContent = "Cloudflare 版";
    $$(".where").forEach((el) => (el.textContent = "雲端"));
    $("#footer-tech").textContent = "Cloudflare 版 · Workers · D1 · KV · Queues · Cron Triggers · Workers AI（OCR）· Depth Anything V2（瀏覽器端）";
    $("#checkin-desc").innerHTML = "每天 <b>00:10（台灣時間）</b>起由 Cloudflare Cron Triggers 自動幫你在 HoYoLAB 簽到，失敗會每小時自動重試。";
    $("#ocr-desc").innerHTML = "使用 Cloudflare Workers AI 視覺模型辨識，<b>支援繁體中文、簡體中文與英文介面</b>，遊戲語言不用切換。辨識結果可以手動修正後再匯入。";
    $("#depth-desc").innerHTML = "上傳一張立繪，<b>你的瀏覽器</b>會用 AI 模型 <b>Depth Anything V2</b> 估計每個像素的深度（第一次使用需下載約 50MB 模型），再由 WebGL 依深度做視差位移，讓平面圖片「立體」起來。三款遊戲共用同一個作品庫。";
    return;
  }
  if (mode !== "cloud") return;
  $("#mode-label").textContent = "雲端版";
  $$(".where").forEach((el) => (el.textContent = "雲端"));
  $("#footer-tech").textContent = "雲端版 · AWS S3 · API Gateway · Lambda · DynamoDB · EventBridge · RapidOCR · Depth Anything V2";
  $("#checkin-desc").innerHTML = "每天 <b>00:10（台灣時間）</b>起由 AWS EventBridge 自動幫你在 HoYoLAB 簽到，失敗會每小時自動重試。";
}

function toggleMenu(open) {
  const m = $("#game-menu");
  open = open ?? m.hidden;
  m.hidden = !open;
  $("#game-btn").setAttribute("aria-expanded", String(open));
}

async function switchGame(id) {
  if (!state.games.some((g) => g.id === id)) id = state.defaultGame || state.games[0].id;
  const meta = await api(`/api/${id}/meta`, { auth: false });
  state.game = id;
  state.meta = meta;
  store("hoyo_game", id);
  applyGame();
  renderGameMenu();
  refreshStats();
  if (state.tab === "checkin") loadCheckin();
}

function applyGame() {
  const m = state.meta;
  const root = document.documentElement.style;
  root.setProperty("--accent", m.accent);
  root.setProperty("--accent-ink", m.accent_ink);
  document.body.dataset.game = m.id;
  document.title = `${m.short} Proxy Hub｜${m.name}玩家工具站`;
  $("#logo").textContent = m.short;
  $("#brand-game").textContent = m.name;
  $("#hl-game").textContent = m.name;
  $("#hl-enabled-game").textContent = `（${m.name}）`;
  $$("[data-l]").forEach((el) => { if (m.labels[el.dataset.l]) el.textContent = m.labels[el.dataset.l]; });
  $("#ps-cmd").textContent = `powershell -ExecutionPolicy Bypass -c "iex (irm '${SITE}get_gacha_url_${m.id}.ps1')"`;
  $("#ocr-gacha-type").innerHTML = m.channel_order.map((k) => `<option value="${k}">${esc(m.channels[k])}</option>`).join("");
  $("#ocr-gacha-result").innerHTML = "";
  ["#import-status", "#ocr-gacha-status", "#ocr-disc-status", "#hl-status"].forEach((s) => setStatus(s, ""));
  state.channel = m.featured_channel;
  initDiscForm();
  initAgents();
  renderLinks();
  $("#disc-result").innerHTML = `<h2>評分結果</h2><p class="muted">輸入詞條後按「計算分數」。<br>評分方式：每條副詞條換算成「強化次數」，再乘上該角色的詞條權重，加總為<b>有效詞條數</b>（滿分 9）。</p>`;
}

/** 某些遊戲在其他站台有額外功能（例如絕區零的 3D 建模），在分頁列加一個外部連結 */
function renderLinks() {
  $$("#tabs .ext-link").forEach((a) => a.remove());
  (state.links || []).filter((l) => l.game === state.game).forEach((l) => {
    const a = document.createElement("a");
    a.className = "ext-link"; a.href = l.url; a.target = "_blank"; a.rel = "noopener";
    a.innerHTML = `${esc(l.label)} <span class="ext">↗</span>`;
    $("#tabs").appendChild(a);
  });
}

/* ---------------- Tabs ---------------- */
function showTab(name) {
  if (!$("#tab-" + name)) name = "gacha";
  state.tab = name;
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((s) => s.classList.toggle("active", s.id === "tab-" + name));
  store("zzz_tab", name);
  if (name === "checkin") loadCheckin();
  if (name === "depth" && !depth.loaded) loadGallery();
}

/* ---------------- 抽卡 ---------------- */
async function importUrl(btn) {
  const url = $("#gacha-url").value.trim();
  if (!url) return toast("請先貼上網址", true);
  await withBtn(btn, async () => {
    try {
      setStatus("#import-status", "建立匯入工作…", "run");
      const { job_id } = await gapi("/api/import/url", { method: "POST", body: { url } });
      const job = await pollJob(job_id, (j) => setStatus("#import-status", j.message, j.status === "error" ? "err" : "run"));
      if (job.status === "done") { setStatus("#import-status", job.message, "ok"); $("#gacha-url").value = ""; await refreshStats(); }
    } catch (e) { setStatus("#import-status", e.message, "err"); }
  });
}

async function ocrGacha(file) {
  const gt = $("#ocr-gacha-type").value;
  $("#ocr-gacha-result").innerHTML = "";
  try {
    setStatus("#ocr-gacha-status", "上傳圖片…", "run");
    const jobId = await uploadImage(file, "gacha", { gacha_type: gt });
    const job = await pollJob(jobId, (j) => setStatus("#ocr-gacha-status", j.message, j.status === "error" ? "err" : "run"));
    if (job.status !== "done") return;
    setStatus("#ocr-gacha-status", job.message, job.result.rows.length ? "ok" : "err");
    renderOcrRows(job.result.rows, gt);
  } catch (e) { setStatus("#ocr-gacha-status", e.message, "err"); }
}

function renderOcrRows(rows, gt) {
  if (!rows.length) return;
  const box = $("#ocr-gacha-result");
  box.innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr><th>名稱</th><th>類型</th><th>稀有度</th><th>時間</th><th></th></tr></thead>
      <tbody>${rows.map((r) => `
        <tr>
          <td><input data-f="name" value="${esc(r.name)}"></td>
          <td><input data-f="item_type" value="${esc(r.item_type)}" style="width:70px"></td>
          <td><select data-f="rank">${["S", "A", "B"].map((x) => `<option value="${x}" ${x === r.rank ? "selected" : ""}>${esc(rankName(x))}</option>`).join("")}</select></td>
          <td><input data-f="time" value="${esc(r.time)}" style="width:170px"></td>
          <td><button class="btn danger sm" data-del>✕</button></td>
        </tr>`).join("")}</tbody>
    </table></div>
    <button class="btn primary" id="btn-confirm-ocr" style="margin-top:10px">確認匯入 ${rows.length} 筆</button>`;
  box.querySelectorAll("[data-del]").forEach((b) => (b.onclick = () => b.closest("tr").remove()));
  $("#btn-confirm-ocr").onclick = (ev) => withBtn(ev.target, async () => {
    const data = $$("tbody tr", box).map((tr) => Object.fromEntries($$("[data-f]", tr).map((i) => [i.dataset.f, i.value.trim()])));
    try {
      const r = await gapi("/api/import/rows", { method: "POST", body: { gacha_type: gt, rows: data } });
      toast(`已匯入 ${r.imported} 筆`);
      box.innerHTML = "";
      setStatus("#ocr-gacha-status", "");
      await refreshStats();
    } catch (e) { toast(e.message, true); }
  });
}

async function refreshStats() {
  if (!state.token || !state.meta) return;
  const game = state.game;
  try {
    const [stats, glob] = await Promise.all([gapi("/api/stats"), gapi("/api/global", { auth: false })]);
    if (game !== state.game) return;  // 載入期間切換了遊戲
    state.stats = stats;
    renderStats(stats);
    renderGlobal(glob);
  } catch (e) { toast(e.message, true); }
}

function renderStats(stats) {
  const ch = stats.channels;
  const feat = ch[state.meta.featured_channel] || {};
  $("#kpis").innerHTML = [
    ["總抽數", stats.total_pulls.toLocaleString()],
    [`約花費${stats.currency}`, stats.polychrome_spent.toLocaleString()],
    [`${L("rank_S")} 總數`, ordered(ch).reduce((a, c) => a + c.s_count, 0)],
    [`${feat.label || ""} 已墊`, `${feat.current_pity ?? 0}<small class="muted">/${feat.pity_max ?? 90}</small>`],
    [`${feat.label || ""} 歐非評價`, feat.luck_label || "—"],
  ].map(([l, v]) => `<div class="kpi"><div class="v">${v}</div><div class="l">${esc(l)}</div></div>`).join("");

  if (!ch[state.channel]) state.channel = state.meta.featured_channel;
  $("#channel-tabs").innerHTML = ordered(ch).map((c) =>
    `<button data-ch="${c.gacha_type}" class="${c.gacha_type === state.channel ? "active" : ""}">${esc(c.label)}<span class="n">${c.total}</span></button>`).join("");
  $$("#channel-tabs button").forEach((b) => (b.onclick = () => { state.channel = b.dataset.ch; renderStats(stats); }));
  renderChannel(ch[state.channel]);
}

function renderChannel(c) {
  const box = $("#channel-detail");
  if (!c || !c.total) {
    box.innerHTML = `<div class="empty">這個卡池還沒有資料，先用上面的方法匯入吧！</div>`;
    if (state.chart) { state.chart.destroy(); state.chart = null; }
    return;
  }
  const S = L("rank_S"), A = L("rank_A");
  const pct = Math.min(100, (c.current_pity / c.pity_max) * 100);
  const resultBadge = { won: '<span class="badge won">小保底贏</span>', lost: '<span class="badge lost">歪</span>', guaranteed: '<span class="badge guaranteed">大保底</span>' };
  box.innerHTML = `
    <div class="pity">
      <div class="pity-text"><span>距離保底：已墊 <b>${c.current_pity}</b> / ${c.pity_max} 抽 ${c.guaranteed ? '<span class="badge guaranteed">下一個必定 UP</span>' : ""}</span><span class="muted">${A} 已墊 ${c.a_pity}/10</span></div>
      <div class="pity-bar"><div class="pity-fill" style="width:${pct}%"></div></div>
    </div>
    <div class="kpis">
      <div class="kpi"><div class="v">${c.avg_s_pulls ?? "—"}</div><div class="l">平均出金抽數（期望 ${c.expected_s_pulls}）</div></div>
      <div class="kpi"><div class="v">${c.s_count}<small class="muted"> / ${c.a_count}</small></div><div class="l">${S} / ${A} 數量</div></div>
      ${c.lose_rate !== null ? `<div class="kpi"><div class="v">${c.lose_rate}%</div><div class="l">小保底歪率（${c.lost_5050} 歪 / ${c.won_5050 + c.lost_5050} 次）</div></div>` : ""}
      <div class="kpi"><div class="v">${c.luck_index ?? "—"}</div><div class="l">歐非指數 <span class="badge luck">${c.luck_label}</span></div></div>
    </div>
    <div class="detail-grid">
      <div class="chart-box"><canvas id="s-chart"></canvas></div>
      <ul class="s-list">${c.s_history.map((s) => `
        <li><span>${esc(s.name)} ${resultBadge[s.result] || ""}<br><span class="muted small">${esc(s.time)}</span></span>
        <span class="p ${s.pulls >= 75 ? "hi" : s.pulls <= 40 ? "lo" : ""}">${s.pulls}</span></li>`).join("") || `<li class="muted">還沒有 ${S}</li>`}</ul>
    </div>`;
  drawChart(c);
}

function drawChart(c) {
  if (state.chart) state.chart.destroy();
  if (!window.Chart) return;
  const hist = [...c.s_history].reverse();
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#c6f432";
  const color = { won: accent, lost: "#ff4d5e", guaranteed: "#4cc3ff" };
  state.chart = new Chart($("#s-chart"), {
    type: "bar",
    data: {
      labels: hist.map((s) => s.name),
      datasets: [{ label: "出金抽數", data: hist.map((s) => s.pulls), borderRadius: 6, backgroundColor: hist.map((s) => color[s.result] || "#ffb627") }],
    },
    options: {
      maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (ctx) => ` ${ctx.raw} 抽` } } },
      scales: {
        y: { beginAtZero: true, max: c.pity_max, grid: { color: "#2a2a31" }, ticks: { color: "#9a9aa6" } },
        x: { grid: { display: false }, ticks: { color: "#ececf1" } },
      },
    },
  });
}

function renderGlobal(g) {
  $("#global-players").textContent = `（${g.players} 位玩家的匿名資料）`;
  $("#global-stats").innerHTML = ordered(g.channels).map((c) => `
    <div class="g-item">
      <div class="muted small">${esc(c.label)}</div>
      <b>${c.avg_s_pulls ?? "—"}</b> <span class="muted small">抽出金</span>
      <div class="small muted">共 ${c.total_pulls.toLocaleString()} 抽 · ${c.s_count} 個 ${L("rank_S")}${c.lose_rate !== null ? ` · 歪率 ${c.lose_rate}%` : ""}</div>
    </div>`).join("");
}

/* ---------------- 裝備評分 ---------------- */
const statOpt = (k) => `<option value="${k}">${esc(state.meta.stat_labels[k] || k)}</option>`;

function initDiscForm() {
  const m = state.meta, d = m.disc;
  $("#disc-char").innerHTML = m.agents.map((a) => `<option value="${a.id}">${esc(a.name)}（${esc(a.specialty_zh)}）</option>`).join("");
  $("#disc-slot").innerHTML = d.slots.map((s) => `<option value="${s.id}">${esc(s.label)}</option>`).join("");
  $("#disc-slot").value = d.default_slot;
  $("#disc-slot").onchange = () => updateMainOptions();
  updateMainOptions();
  $("#disc-subs").innerHTML = [0, 1, 2, 3].map(() => `
    <div class="sub-row"><select class="sub-stat"><option value="">—</option>${d.sub_stats.map(statOpt).join("")}</select>
    <input class="sub-val" type="number" step="0.1" min="0" placeholder="數值"></div>`).join("");
}

function updateMainOptions(selected) {
  const slot = state.meta.disc.slots.find((s) => String(s.id) === $("#disc-slot").value) || state.meta.disc.slots[0];
  $("#disc-main").innerHTML = slot.mains.map(statOpt).join("");
  if (selected && slot.mains.includes(selected)) $("#disc-main").value = selected;
}

function fillDisc(d) {
  if (d.slot) $("#disc-slot").value = d.slot;
  updateMainOptions(d.main && d.main.stat);
  $$("#disc-subs .sub-row").forEach((r, i) => {
    const s = d.subs[i];
    $(".sub-stat", r).value = s ? s.stat : "";
    $(".sub-val", r).value = s ? s.value : "";
  });
}

async function ocrDisc(file) {
  try {
    setStatus("#ocr-disc-status", "上傳圖片…", "run");
    const jobId = await uploadImage(file, "disc");
    const job = await pollJob(jobId, (j) => setStatus("#ocr-disc-status", j.message, j.status === "error" ? "err" : "run"));
    if (job.status !== "done") return;
    setStatus("#ocr-disc-status", job.message, "ok");
    fillDisc(job.result);
  } catch (e) { setStatus("#ocr-disc-status", e.message, "err"); }
}

async function scoreDisc(btn) {
  const subs = $$("#disc-subs .sub-row").map((r) => ({ stat: $(".sub-stat", r).value, value: parseFloat($(".sub-val", r).value) }))
    .filter((s) => s.stat && !isNaN(s.value));
  await withBtn(btn, async () => {
    try {
      const r = await gapi("/api/disc/score", {
        method: "POST", auth: false,
        body: { character: $("#disc-char").value, slot: $("#disc-slot").value, main: { stat: $("#disc-main").value }, subs },
      });
      $("#disc-result").innerHTML = `
        <h2>評分結果 · ${esc(r.character)}</h2>
        <div class="grade-box">
          <div class="grade g-${r.grade}">${r.grade}</div>
          <div><div style="font-size:1.6rem;font-weight:900">${r.score} 分</div>
          <div class="muted">有效詞條 ${r.effective_rolls} / 9</div></div>
        </div>
        <div class="table-wrap"><table>
          <thead><tr><th>副詞條</th><th>數值</th><th>強化次數</th><th>權重</th><th>有效</th></tr></thead>
          <tbody>${r.subs.map((s) => `<tr><td>${esc(s.label)}</td><td>${s.value}</td><td>${s.rolls}</td><td>${s.weight}</td><td><b>${s.effective}</b></td></tr>`).join("")}</tbody>
        </table></div>
        <ul class="advice">${r.advice.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>
        <p class="muted small">權重：${Object.entries(r.weights).map(([k, v]) => `${esc(k)} ${v}`).join("、")}</p>`;
    } catch (e) { toast(e.message, true); }
  });
}

/* ---------------- 角色圖鑑 ---------------- */
function initAgents() {
  const m = state.meta;
  const used = new Set(m.agents.map((a) => a.element));
  $("#filter-element").innerHTML = `<option value="">全部屬性</option>` +
    Object.entries(m.elements).filter(([k]) => used.has(k)).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("#filter-specialty").innerHTML = `<option value="">全部${esc(L("specialty"))}</option>` +
    Object.entries(m.specialties).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("#filter-rank").innerHTML = `<option value="">全部稀有度</option><option value="S">${esc(L("rank_S"))}</option><option value="A">${esc(L("rank_A"))}</option>`;
  $("#filter-name").value = "";
  ["#filter-element", "#filter-specialty", "#filter-rank"].forEach((s) => ($(s).onchange = renderAgents));
  $("#filter-name").oninput = renderAgents;
  renderAgents();
}

function renderAgents() {
  const q = $("#filter-name").value.trim().toLowerCase();
  const el = $("#filter-element").value, sp = $("#filter-specialty").value, rk = $("#filter-rank").value;
  const list = state.meta.agents.filter((a) => (!el || a.element === el) && (!sp || a.specialty === sp) && (!rk || a.rank === rk)
    && (!q || a.name.toLowerCase().includes(q) || a.name_en.toLowerCase().includes(q)));
  $("#agent-count").textContent = `（${list.length} / ${state.meta.agents.length}）`;
  $("#agent-grid").innerHTML = list.map((a) => `
    <div class="agent el-${a.element}">
      <span class="rk rank-${a.rank}">${esc(rankName(a.rank))}</span>
      <div class="nm">${esc(a.name)}</div><div class="en">${esc(a.name_en)}</div>
      <div class="meta"><span>${esc(a.element_zh)}</span><span>${esc(a.specialty_zh)}</span>${a.standard && a.rank === "S" ? "<span>常駐</span>" : ""}</div>
      ${a.faction ? `<div class="en" style="margin-top:4px">${esc(a.faction)}</div>` : ""}
    </div>`).join("") || `<div class="empty">沒有符合的${esc(L("agent"))}</div>`;
}

/* ---------------- 2.5D 立繪 ---------------- */
const depth = { viewer: null, current: null, gal: "mine", loaded: false };

function initDepth() {
  try { depth.viewer = new ParallaxViewer($("#depth-canvas")); }
  catch (e) { setStatus("#depth-status", e.message, "err"); return; }
  const v = depth.viewer;
  $("#ctl-strength").oninput = (e) => (v.strength = parseFloat(e.target.value));
  $("#ctl-focus").oninput = (e) => (v.focus = parseFloat(e.target.value));
  $("#ctl-depth").onchange = (e) => (v.showDepth = e.target.checked);
  $("#ctl-mode").onchange = async (e) => {
    if (e.target.value === "gyro") {
      try { await v.enableGyro(); } catch (err) { toast(err.message, true); e.target.value = "mouse"; }
    }
    v.mode = e.target.value;
    v.pointer = [0, 0];
  };
  $("#depth-file").onchange = (e) => { if (e.target.files[0]) generateDepth(e.target.files[0]); e.target.value = ""; };
  $$("[data-gal]").forEach((b) => (b.onclick = () => {
    depth.gal = b.dataset.gal;
    $$("[data-gal]").forEach((x) => x.classList.toggle("active", x === b));
    loadGallery();
  }));
  $("#btn-share").onclick = () => {
    if (!depth.current) return;
    navigator.clipboard.writeText(`${SITE}?view=${depth.current.id}`).then(() => toast("已複製分享連結"));
  };
  $("#btn-record").onclick = (e) => withBtn(e.target, async () => {
    try {
      toast("錄影中（4 秒）…");
      const blob = await v.record(4);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${depth.current?.title || "hoyo-2.5d"}.webm`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    } catch (err) { toast(err.message, true); }
  });
}

async function openWork(w) {
  if (!depth.viewer) return;
  try {
    await depth.viewer.load(w.image, w.depth);
    depth.current = w;
    $("#viewer-empty").hidden = true;
    $("#viewer-title").textContent = w.title;
    $("#btn-share").disabled = false;
    $("#btn-record").disabled = false;
    history.replaceState(null, "", `?view=${w.id}`);
  } catch (e) { toast(e.message, true); }
}

/** Cloudflare 版：深度在瀏覽器計算（depth_client.js），算好再上傳三個檔案 */
async function generateDepthClient(file) {
  const status = (m) => setStatus("#depth-status", m, "run");
  if (file.size > 10 * 1024 * 1024) throw new Error("圖片請小於 10MB");
  status("載入 AI 模組…");
  const { processImage } = await import("./depth_client.js");
  const r = await processImage(file, status);
  status("上傳作品…");
  const form = new FormData();
  form.append("title", $("#depth-title").value.trim());
  form.append("public", String($("#depth-public").checked));
  for (const k of ["width", "height", "elapsed"]) form.append(k, String(r[k]));
  for (const k of ["image", "depth", "thumb"]) form.append(k, r[k], k);
  const res = await fetch(API + "/api/depth/upload", { method: "POST", headers: { Authorization: "Bearer " + state.token }, body: form });
  const work = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(work.error || `HTTP ${res.status}`);
  return work;
}

async function generateDepth(file) {
  if (state.mode === "cloudflare") {
    try {
      const work = await generateDepthClient(file);
      setStatus("#depth-status", work.message, "ok");
      $("#depth-title").value = "";
      await openWork(work);
      if (depth.gal === "mine") loadGallery();
    } catch (e) { setStatus("#depth-status", e.message, "err"); }
    return;
  }
  try {
    setStatus("#depth-status", "上傳圖片…", "run");
    const jobId = await uploadImage(file, "depth", { title: $("#depth-title").value.trim(), public: $("#depth-public").checked }, 10);
    const job = await pollJob(jobId, (j) => setStatus("#depth-status", j.message, j.status === "error" ? "err" : "run"));
    if (job.status !== "done") return;
    setStatus("#depth-status", job.message, "ok");
    $("#depth-title").value = "";
    await openWork(job.result);
    if (depth.gal === "mine") loadGallery();
  } catch (e) { setStatus("#depth-status", e.message, "err"); }
}

async function loadGallery() {
  const box = $("#gallery");
  try {
    const mine = depth.gal === "mine";
    const { works } = mine ? await api("/api/depth/mine") : await api("/api/depth/gallery", { auth: false });
    box.innerHTML = works.map((w) => `
      <div class="work" data-id="${esc(w.id)}">
        <img src="${esc(w.thumb)}" alt="${esc(w.title)}" loading="lazy">
        <div class="cap">${esc(w.title)}</div>
        ${mine && w.public ? '<span class="badge won pub">公開</span>' : ""}
        ${mine ? '<button class="btn danger sm del" title="刪除">✕</button>' : ""}
      </div>`).join("") || `<div class="empty">${mine ? "還沒有作品，上傳一張立繪試試看！" : "畫廊還是空的"}</div>`;
    works.forEach((w) => {
      const el = box.querySelector(`[data-id="${w.id}"]`);
      el.onclick = () => openWork(w);
      const del = el.querySelector(".del");
      if (del) del.onclick = async (ev) => {
        ev.stopPropagation();
        if (!confirm(`刪除「${w.title}」？`)) return;
        try { await api(`/api/depth/${w.id}`, { method: "DELETE" }); toast("已刪除"); loadGallery(); }
        catch (e) { toast(e.message, true); }
      };
    });
    depth.loaded = true;
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

/* ---------------- HoYoLAB ---------------- */
const CHECKIN_ICON = { signed: "✅", already: "☑️", captcha: "🧩", error: "⚠️" };

async function loadCheckin() {
  const game = state.game;
  try {
    const s = await gapi("/api/hoyolab");
    if (game !== state.game) return;
    if (!s.configured) {
      $("#hl-info").innerHTML = '<p class="muted">尚未設定。</p>';
      $("#hl-cookie").placeholder = "ltoken_v2=...; ltuid_v2=...; ltmid_v2=...";
      $("#hl-enabled").checked = true;
      return;
    }
    $("#hl-enabled").checked = s.enabled;
    $("#hl-cookie").placeholder = `已儲存（ltuid ${s.ltuid}，三款遊戲共用），留空表示不變更`;
    $("#hl-webhook").placeholder = s.has_webhook ? "已設定 Webhook，留空表示不變更" : "https://discord.com/api/webhooks/...";
    const names = Object.fromEntries(state.games.map((g) => [g.id, g.short]));
    const lr = s.last_result;
    $("#hl-info").innerHTML = `
      <div class="kpis">
        <div class="kpi"><div class="v">${s.enabled ? "ON" : "OFF"}</div><div class="l">${esc(state.meta.name)} 自動簽到</div></div>
        <div class="kpi"><div class="v">${lr && lr.total_days != null ? lr.total_days : "—"}</div><div class="l">本月已簽到天數</div></div>
        <div class="kpi"><div class="v" style="font-size:1.2rem">${s.enabled_games.map((g) => names[g] || g).join(" · ") || "—"}</div><div class="l">已啟用自動簽到的遊戲</div></div>
      </div>
      ${lr ? `<p>${CHECKIN_ICON[lr.status] || ""} 最近一次：${esc(lr.date)} ${esc(lr.message)}${lr.reward ? `<br>🎁 ${esc(lr.reward)}` : ""}</p>` : ""}
      <div class="table-wrap"><table><thead><tr><th>日期</th><th>結果</th><th>獎勵</th></tr></thead>
      <tbody>${s.history.map((h) => `<tr><td>${esc(h.date)}</td><td>${CHECKIN_ICON[h.status] || ""} ${esc(h.message)}</td><td>${esc(h.reward || "")}</td></tr>`).join("")}</tbody></table></div>`;
  } catch (e) { toast(e.message, true); }
}

async function saveCheckin(btn) {
  const body = { enabled: $("#hl-enabled").checked };
  if ($("#hl-cookie").value.trim()) body.cookie = $("#hl-cookie").value.trim();
  if ($("#hl-webhook").value.trim()) body.webhook = $("#hl-webhook").value.trim();
  await withBtn(btn, async () => {
    try {
      await gapi("/api/hoyolab", { method: "PUT", body });
      $("#hl-cookie").value = ""; $("#hl-webhook").value = "";
      setStatus("#hl-status", "已儲存", "ok");
      loadCheckin();
    } catch (e) { setStatus("#hl-status", e.message, "err"); }
  });
}

async function runCheckin(btn) {
  await withBtn(btn, async () => {
    try {
      setStatus("#hl-status", "簽到中…", "run");
      const r = await gapi("/api/hoyolab/checkin", { method: "POST" });
      setStatus("#hl-status", `${CHECKIN_ICON[r.status] || ""} ${r.message}${r.reward ? `｜${r.reward}` : ""}`, r.status === "error" || r.status === "captcha" ? "err" : "ok");
      loadCheckin();
    } catch (e) { setStatus("#hl-status", e.message, "err"); }
  });
}

/* ---------------- 初始化 ---------------- */
function bind() {
  $$("#tabs button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
  $("#game-btn").onclick = (e) => { e.stopPropagation(); toggleMenu(); };
  document.addEventListener("click", (e) => { if (!e.target.closest("#game-menu")) toggleMenu(false); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") toggleMenu(false); });
  $$("[data-copy]").forEach((b) => (b.onclick = () => {
    const el = $("#" + b.dataset.copy);
    navigator.clipboard.writeText(el.value || el.textContent).then(() => toast("已複製"));
  }));
  $("#btn-import-url").onclick = (e) => importUrl(e.target);
  $("#ocr-gacha-file").onchange = (e) => { if (e.target.files[0]) ocrGacha(e.target.files[0]); e.target.value = ""; };
  $("#ocr-disc-file").onchange = (e) => { if (e.target.files[0]) ocrDisc(e.target.files[0]); e.target.value = ""; };
  $("#btn-refresh-stats").onclick = refreshStats;
  $("#btn-clear-pulls").onclick = async () => {
    if (!confirm(`確定要刪除你在「${state.meta.name}」的所有抽卡紀錄嗎？`)) return;
    try { const r = await gapi("/api/pulls", { method: "DELETE" }); toast(`已刪除 ${r.deleted} 筆`); refreshStats(); } catch (e) { toast(e.message, true); }
  };
  $("#btn-score").onclick = (e) => scoreDisc(e.target);
  $("#btn-hl-save").onclick = (e) => saveCheckin(e.target);
  $("#btn-hl-run").onclick = (e) => runCheckin(e.target);
  $("#btn-hl-delete").onclick = async () => {
    if (!confirm("確定刪除 HoYoLAB 設定？（三款遊戲的自動簽到都會停止）")) return;
    try { await api("/api/hoyolab", { method: "DELETE" }); toast("已刪除"); loadCheckin(); } catch (e) { toast(e.message, true); }
  };
  $("#btn-linkcode").onclick = (e) => withBtn(e.target, async () => {
    try { const r = await api("/api/profile/linkcode", { method: "POST" }); $("#linkcode").textContent = r.code; } catch (err) { toast(err.message, true); }
  });
  $("#btn-show-token").onclick = () => { const i = $("#me-token"); i.type = i.type === "password" ? "text" : "password"; };
  $("#btn-restore").onclick = async () => {
    const t = $("#restore-token").value.trim();
    if (!t) return;
    const old = state.token;
    state.token = t;
    try { const me = await api("/api/profile"); state.pid = me.profile_id; store("zzz_token", t); renderMe(me); refreshStats(); toast("已還原"); }
    catch (e) { state.token = old; toast("Token 無效", true); }
  };
}

async function main() {
  bind();
  $("#api-base").textContent = API || location.origin;
  initDepth();  // 不依賴 API，先初始化，避免 API 失敗時按鈕沒反應
  const params = new URLSearchParams(location.search);
  for (let attempt = 1; ; attempt++) {
    try {
      const [me, games] = await Promise.all([ensureProfile(), api("/api/games", { auth: false })]);
      state.games = games.games;
      state.external = games.external;
      state.links = games.links || [];
      state.defaultGame = games.default;
      applyMode(games.mode);
      renderMe(me);
      await switchGame(params.get("game") || load("hoyo_game") || games.default);
      break;
    } catch (e) {
      if (attempt >= 3) { toast(e.message, true); break; }
      await new Promise((r) => setTimeout(r, 1000 * attempt));
    }
  }
  const viewId = params.get("view");
  if (viewId) {
    showTab("depth");
    api(`/api/depth/${encodeURIComponent(viewId)}`, { auth: false }).then(openWork).catch((e) => toast(e.message, true));
  } else {
    showTab(load("zzz_tab") || "gacha");
  }
}
main();
