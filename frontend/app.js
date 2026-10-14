/* ZZZ Proxy Hub 前端 */
const API = (window.APP_CONFIG && window.APP_CONFIG.API_BASE || "").replace(/\/$/, "");
const SITE = location.origin + location.pathname.replace(/[^/]*$/, "");
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = { token: null, pid: null, agents: [], statLabels: {}, specialties: {}, stats: null, channel: "2", chart: null };
const ELEMENT_ZH = { ice: "冰", fire: "火", electric: "電", physical: "物理", ether: "以太" };

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
    throw new Error("無法連線到 API，請檢查網路後重試");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.error || `HTTP ${r.status}`), { status: r.status });
  return data;
}

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
  const { job_id, upload_url } = await api("/api/upload-url", { method: "POST", body: { kind, content_type: ctype, ...extra } });
  const r = await fetch(upload_url, { method: "PUT", headers: { "Content-Type": ctype }, body: file });
  if (!r.ok) throw new Error("上傳到 S3 失敗");
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

/* ---------------- Tabs ---------------- */
function showTab(name) {
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((s) => s.classList.toggle("active", s.id === "tab-" + name));
  store("zzz_tab", name);
  if (name === "checkin") loadCheckin();
  if (name === "teams") loadTeams();
  if (name === "depth" && !depth.loaded) loadGallery();
  clearInterval(m3d.statusTimer);
  if (name === "model3d") {
    if (!m3d.loaded) loadGallery3d();
    refreshWorkerStatus();
    m3d.statusTimer = setInterval(refreshWorkerStatus, 15000);
  }
}

/* ---------------- 抽卡 ---------------- */
async function importUrl(btn) {
  const url = $("#gacha-url").value.trim();
  if (!url) return toast("請先貼上網址", true);
  await withBtn(btn, async () => {
    try {
      setStatus("#import-status", "建立匯入工作…", "run");
      const { job_id } = await api("/api/import/url", { method: "POST", body: { url } });
      const job = await pollJob(job_id, (j) => setStatus("#import-status", j.message, j.status === "error" ? "err" : "run"));
      if (job.status === "done") { setStatus("#import-status", job.message, "ok"); $("#gacha-url").value = ""; await refreshStats(); }
    } catch (e) { setStatus("#import-status", e.message, "err"); }
  });
}

async function ocrGacha(file) {
  const gt = $("#ocr-gacha-type").value;
  $("#ocr-gacha-result").innerHTML = "";
  try {
    setStatus("#ocr-gacha-status", "上傳到 S3…", "run");
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
          <td><select data-f="rank">${["S", "A", "B"].map((x) => `<option ${x === r.rank ? "selected" : ""}>${x}</option>`).join("")}</select></td>
          <td><input data-f="time" value="${esc(r.time)}" style="width:170px"></td>
          <td><button class="btn danger sm" data-del>✕</button></td>
        </tr>`).join("")}</tbody>
    </table></div>
    <button class="btn primary" id="btn-confirm-ocr" style="margin-top:10px">確認匯入 ${rows.length} 筆</button>`;
  box.querySelectorAll("[data-del]").forEach((b) => (b.onclick = () => b.closest("tr").remove()));
  $("#btn-confirm-ocr").onclick = (ev) => withBtn(ev.target, async () => {
    const data = $$("tbody tr", box).map((tr) => Object.fromEntries($$("[data-f]", tr).map((i) => [i.dataset.f, i.value.trim()])));
    try {
      const r = await api("/api/import/rows", { method: "POST", body: { gacha_type: gt, rows: data } });
      toast(`已匯入 ${r.imported} 筆`);
      box.innerHTML = "";
      setStatus("#ocr-gacha-status", "");
      await refreshStats();
    } catch (e) { toast(e.message, true); }
  });
}

async function refreshStats() {
  try {
    const [stats, glob] = await Promise.all([api("/api/stats"), api("/api/global", { auth: false })]);
    state.stats = stats;
    renderStats(stats);
    renderGlobal(glob);
  } catch (e) { toast(e.message, true); }
}

function renderStats(stats) {
  const ch = stats.channels;
  const limited = ch["2"] || {};
  $("#kpis").innerHTML = [
    ["總抽數", stats.total_pulls.toLocaleString()],
    ["約花費菲林", stats.polychrome_spent.toLocaleString()],
    ["S 級總數", Object.values(ch).reduce((a, c) => a + c.s_count, 0)],
    ["獨家頻段 已墊", `${limited.current_pity ?? 0}<small class="muted">/90</small>`],
    ["獨家 歐非評價", limited.luck_label || "—"],
  ].map(([l, v]) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");

  $("#channel-tabs").innerHTML = Object.values(ch).map((c) =>
    `<button data-ch="${c.gacha_type}" class="${c.gacha_type === state.channel ? "active" : ""}">${c.label}<span class="n">${c.total}</span></button>`).join("");
  $$("#channel-tabs button").forEach((b) => (b.onclick = () => { state.channel = b.dataset.ch; renderStats(stats); }));
  renderChannel(ch[state.channel]);
}

function renderChannel(c) {
  const box = $("#channel-detail");
  if (!c || !c.total) {
    box.innerHTML = `<div class="empty">這個頻段還沒有資料，先用上面的方法匯入吧！</div>`;
    return;
  }
  const pct = Math.min(100, (c.current_pity / c.pity_max) * 100);
  const resultBadge = { won: '<span class="badge won">小保底贏</span>', lost: '<span class="badge lost">歪</span>', guaranteed: '<span class="badge guaranteed">大保底</span>' };
  box.innerHTML = `
    <div class="pity">
      <div class="pity-text"><span>距離保底：已墊 <b>${c.current_pity}</b> / ${c.pity_max} 抽 ${c.guaranteed ? '<span class="badge guaranteed">下一隻必定 UP</span>' : ""}</span><span class="muted">A 級已墊 ${c.a_pity}/10</span></div>
      <div class="pity-bar"><div class="pity-fill" style="width:${pct}%"></div></div>
    </div>
    <div class="kpis">
      <div class="kpi"><div class="v">${c.avg_s_pulls ?? "—"}</div><div class="l">平均出金抽數（期望 ${c.expected_s_pulls}）</div></div>
      <div class="kpi"><div class="v">${c.s_count}<small class="muted"> / ${c.a_count}</small></div><div class="l">S 級 / A 級 數量</div></div>
      ${c.lose_rate !== null ? `<div class="kpi"><div class="v">${c.lose_rate}%</div><div class="l">小保底歪率（${c.lost_5050} 歪 / ${c.won_5050 + c.lost_5050} 次）</div></div>` : ""}
      <div class="kpi"><div class="v">${c.luck_index ?? "—"}</div><div class="l">歐非指數 <span class="badge luck">${c.luck_label}</span></div></div>
    </div>
    <div class="detail-grid">
      <div class="chart-box"><canvas id="s-chart"></canvas></div>
      <ul class="s-list">${c.s_history.map((s) => `
        <li><span>${esc(s.name)} ${resultBadge[s.result] || ""}<br><span class="muted small">${esc(s.time)}</span></span>
        <span class="p ${s.pulls >= 75 ? "hi" : s.pulls <= 40 ? "lo" : ""}">${s.pulls}</span></li>`).join("") || '<li class="muted">還沒有 S 級</li>'}</ul>
    </div>`;
  drawChart(c);
}

function drawChart(c) {
  if (state.chart) state.chart.destroy();
  if (!window.Chart) return;
  const hist = [...c.s_history].reverse();
  const color = { won: "#c6f432", lost: "#ff4d5e", guaranteed: "#4cc3ff" };
  state.chart = new Chart($("#s-chart"), {
    type: "bar",
    data: {
      labels: hist.map((s) => s.name),
      datasets: [{
        label: "出金抽數", data: hist.map((s) => s.pulls), borderRadius: 6,
        backgroundColor: hist.map((s) => color[s.result] || "#ffb627"),
      }],
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
  $("#global-stats").innerHTML = Object.values(g.channels).map((c) => `
    <div class="g-item">
      <div class="muted small">${c.label}</div>
      <b>${c.avg_s_pulls ?? "—"}</b> <span class="muted small">抽出金</span>
      <div class="small muted">共 ${c.total_pulls.toLocaleString()} 抽 · ${c.s_count} 個 S${c.lose_rate !== null ? ` · 歪率 ${c.lose_rate}%` : ""}</div>
    </div>`).join("");
}

/* ---------------- 驅動盤 ---------------- */
const MAIN_STATS = ["hp", "atk", "def", "hp_pct", "atk_pct", "def_pct", "crit_rate", "crit_dmg", "ap", "pen_ratio", "dmg_bonus", "am", "impact", "er"];
const SUB_STATS = ["crit_rate", "crit_dmg", "atk_pct", "atk", "hp_pct", "hp", "def_pct", "def", "pen", "ap"];

function initDiscForm() {
  const opt = (k) => `<option value="${k}">${esc(state.statLabels[k] || k)}</option>`;
  $("#disc-char").innerHTML = state.agents.map((a) => `<option value="${a.id}">${esc(a.name)}（${a.specialty_zh}）</option>`).join("");
  $("#disc-main").innerHTML = MAIN_STATS.map(opt).join("");
  $("#disc-subs").innerHTML = [0, 1, 2, 3].map(() => `
    <div class="sub-row"><select class="sub-stat"><option value="">—</option>${SUB_STATS.map(opt).join("")}</select>
    <input class="sub-val" type="number" step="0.1" min="0" placeholder="數值"></div>`).join("");
  $("#disc-slot").value = "4";
  $("#disc-main").value = "crit_rate";
}

function fillDisc(d) {
  if (d.slot) $("#disc-slot").value = d.slot;
  if (d.main) $("#disc-main").value = d.main.stat;
  const rows = $$("#disc-subs .sub-row");
  rows.forEach((r, i) => {
    const s = d.subs[i];
    $(".sub-stat", r).value = s ? s.stat : "";
    $(".sub-val", r).value = s ? s.value : "";
  });
}

async function ocrDisc(file) {
  try {
    setStatus("#ocr-disc-status", "上傳到 S3…", "run");
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
      const r = await api("/api/disc/score", {
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
      a.download = `${depth.current?.title || "zzz-2.5d"}.webm`;
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

async function generateDepth(file) {
  try {
    setStatus("#depth-status", "上傳到 S3…", "run");
    const jobId = await uploadImage(file, "depth", {
      title: $("#depth-title").value.trim(), public: $("#depth-public").checked,
    }, 10);
    const job = await pollJob(jobId, (j) => setStatus("#depth-status",
      j.status === "pending" ? "等待 Lambda 啟動（第一次需要載入 AI 模型，約 10~20 秒）…" : j.message,
      j.status === "error" ? "err" : "run"));
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

/* ---------------- 3D 模型 ---------------- */
const m3d = { current: null, gal: "mine", loaded: false, statusTimer: null };
const M3D_STEPS = ["排隊", "喚醒", "去背", "AI 推測", "建網格", "完成"];

function m3dStep(msg, status) {
  let n = 1;
  if (/喚醒|啟動|關機/.test(msg)) n = 2;
  if (/去背/.test(msg)) n = 3;
  if (/推測/.test(msg)) n = 4;
  if (/網格|上色/.test(msg)) n = 5;
  if (status === "done") n = 6;
  $("#m3d-steps").innerHTML = M3D_STEPS.map((s, i) => `<span class="${i < n ? "on" : ""}" title="${s}"></span>`).join("");
}

async function refreshWorkerStatus() {
  try {
    const s = await api("/api/model3d/status", { auth: false });
    const box = $("#worker-status");
    let cls = "", text;
    if (s.online) {
      cls = s.worker_status === "busy" ? "busy" : "on";
      text = s.worker_status === "busy" ? `運算主機忙碌中・佇列 ${s.queue} 個任務` : "運算主機在線，可以立即生成";
    } else if (s.instance === "pending" || s.instance === "running") {
      cls = "wake"; text = "運算主機啟動中，正在載入 AI 模型…";
    } else if (s.instance === "stopping") {
      cls = "wake"; text = "運算主機關機中";
    } else {
      text = "運算主機休眠中（送出任務時會自動喚醒）";
    }
    box.className = "worker-status " + cls;
    $("#worker-text").textContent = text;
  } catch (e) { $("#worker-text").textContent = "無法取得狀態"; }
}

function init3d() {
  $("#m3d-file").onchange = (e) => { if (e.target.files[0]) generate3d(e.target.files[0]); e.target.value = ""; };
  $$("[data-gal3d]").forEach((b) => (b.onclick = () => {
    m3d.gal = b.dataset.gal3d;
    $$("[data-gal3d]").forEach((x) => x.classList.toggle("active", x === b));
    loadGallery3d();
  }));
  $("#btn-m3d-share").onclick = () => {
    if (!m3d.current) return;
    navigator.clipboard.writeText(`${SITE}?model=${m3d.current.id}`).then(() => toast("已複製分享連結"));
  };
}

function openModel(w) {
  m3d.current = w;
  $("#m3d-viewer").src = w.model;
  $("#m3d-viewer").poster = w.thumb;
  $("#m3d-empty").hidden = true;
  $("#m3d-viewer-title").textContent = w.title;
  $("#btn-m3d-share").disabled = false;
  const dl = $("#btn-m3d-download");
  dl.href = w.model;
  dl.download = `${w.title || "model"}.glb`;
  dl.setAttribute("aria-disabled", "false");
  $("#m3d-meta").textContent = `${(w.faces || 0).toLocaleString()} 個面 · ${((w.size || 0) / 1048576).toFixed(1)} MB · 生成耗時 ${w.elapsed} 秒`;
  history.replaceState(null, "", `?model=${w.id}`);
}

async function generate3d(file) {
  try {
    m3dStep("", "");
    setStatus("#m3d-status", "上傳到 S3…", "run");
    const jobId = await uploadImage(file, "model3d", {
      title: $("#m3d-title").value.trim(), public: $("#m3d-public").checked,
    }, 10);
    const job = await pollJob(jobId, (j) => {
      setStatus("#m3d-status", j.message, j.status === "error" ? "err" : "run");
      m3dStep(j.message, j.status);
    }, 900);
    refreshWorkerStatus();
    if (job.status !== "done") return;
    setStatus("#m3d-status", job.message, "ok");
    $("#m3d-title").value = "";
    openModel(job.result);
    if (m3d.gal === "mine") loadGallery3d();
  } catch (e) { setStatus("#m3d-status", e.message, "err"); }
}

async function loadGallery3d() {
  const box = $("#gallery3d");
  try {
    const mine = m3d.gal === "mine";
    const { works } = mine ? await api("/api/model3d/mine") : await api("/api/model3d/gallery", { auth: false });
    box.innerHTML = works.map((w) => `
      <div class="work" data-id="${esc(w.id)}">
        <img src="${esc(w.thumb)}" alt="${esc(w.title)}" loading="lazy">
        <div class="cap">${esc(w.title)}</div>
        ${mine && w.public ? '<span class="badge won pub">公開</span>' : ""}
        ${mine ? '<button class="btn danger sm del" title="刪除">✕</button>' : ""}
      </div>`).join("") || `<div class="empty">${mine ? "還沒有模型，上傳一張圖片試試看！" : "畫廊還是空的"}</div>`;
    works.forEach((w) => {
      const el = box.querySelector(`[data-id="${w.id}"]`);
      el.onclick = () => openModel(w);
      const del = el.querySelector(".del");
      if (del) del.onclick = async (ev) => {
        ev.stopPropagation();
        if (!confirm(`刪除「${w.title}」？`)) return;
        try { await api(`/api/model3d/${w.id}`, { method: "DELETE" }); toast("已刪除"); loadGallery3d(); }
        catch (e) { toast(e.message, true); }
      };
    });
    m3d.loaded = true;
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

/* ---------------- 角色與配隊 ---------------- */
function initAgents() {
  const els = [...new Set(state.agents.map((a) => a.element))];
  $("#filter-element").innerHTML += els.map((e) => `<option value="${e}">${ELEMENT_ZH[e]}</option>`).join("");
  $("#filter-specialty").innerHTML += Object.entries(state.specialties).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
  ["#filter-element", "#filter-specialty", "#filter-rank"].forEach((s) => ($(s).onchange = renderAgents));
  $$(".team-member").forEach((sel) => (sel.innerHTML = state.agents.map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join("")));
  renderAgents();
}

function renderAgents() {
  const el = $("#filter-element").value, sp = $("#filter-specialty").value, rk = $("#filter-rank").value;
  const list = state.agents.filter((a) => (!el || a.element === el) && (!sp || a.specialty === sp) && (!rk || a.rank === rk));
  $("#agent-grid").innerHTML = list.map((a) => `
    <div class="agent el-${a.element}">
      <span class="rk rank-${a.rank}">${a.rank}</span>
      <div class="nm">${esc(a.name)}</div><div class="en">${esc(a.name_en)}</div>
      <div class="meta"><span>${a.element_zh}</span><span>${a.specialty_zh}</span>${a.standard && a.rank === "S" ? "<span>常駐</span>" : ""}</div>
      <div class="en" style="margin-top:4px">${esc(a.faction)}</div>
    </div>`).join("") || '<div class="empty">沒有符合的代理人</div>';
}

async function loadTeams() {
  try {
    const { teams } = await api("/api/teams", { auth: false });
    $("#team-list").innerHTML = teams.map((t, i) => `
      <div class="team">
        <div class="vote"><span class="n">${t.votes}</span><button class="btn ghost sm" data-vote="${t.id}">👍</button></div>
        <div class="info">
          <b>#${i + 1} ${esc(t.name)}</b> <span class="muted small">by ${esc(t.author)}</span>
          <div class="members">${t.members.map((m) => `<span class="chip el-${m.element}">${esc(m.name)}</span>`).join("")}</div>
          <div class="note">${esc(t.note)}</div>
        </div>
      </div>`).join("") || '<div class="empty">還沒有配隊</div>';
    $$("[data-vote]").forEach((b) => (b.onclick = () => withBtn(b, async () => {
      try { await api(`/api/teams/${b.dataset.vote}/vote`, { method: "POST" }); toast("已投票！"); loadTeams(); }
      catch (e) { toast(e.message, true); }
    })));
  } catch (e) { toast(e.message, true); }
}

async function createTeam(btn) {
  await withBtn(btn, async () => {
    try {
      await api("/api/teams", {
        method: "POST",
        body: { name: $("#team-name").value, note: $("#team-note").value, members: $$(".team-member").map((s) => s.value) },
      });
      setStatus("#team-status", "已送出！", "ok");
      $("#team-name").value = ""; $("#team-note").value = "";
      loadTeams();
    } catch (e) { setStatus("#team-status", e.message, "err"); }
  });
}

/* ---------------- HoYoLAB ---------------- */
const CHECKIN_ICON = { signed: "✅", already: "☑️", captcha: "🧩", error: "⚠️" };

async function loadCheckin() {
  try {
    const s = await api("/api/hoyolab");
    if (!s.configured) { $("#hl-info").innerHTML = '<p class="muted">尚未設定。</p>'; return; }
    $("#hl-enabled").checked = s.enabled;
    $("#hl-cookie").placeholder = `已儲存（ltuid ${s.ltuid}），留空表示不變更`;
    $("#hl-webhook").placeholder = s.has_webhook ? "已設定 Webhook，留空表示不變更" : "https://discord.com/api/webhooks/...";
    const lr = s.last_result;
    $("#hl-info").innerHTML = `
      <div class="kpis">
        <div class="kpi"><div class="v">${s.enabled ? "ON" : "OFF"}</div><div class="l">自動簽到</div></div>
        <div class="kpi"><div class="v">${lr && lr.total_days != null ? lr.total_days : "—"}</div><div class="l">本月已簽到天數</div></div>
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
      await api("/api/hoyolab", { method: "PUT", body });
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
      const r = await api("/api/hoyolab/checkin", { method: "POST" });
      setStatus("#hl-status", `${CHECKIN_ICON[r.status] || ""} ${r.message}${r.reward ? `｜${r.reward}` : ""}`, r.status === "error" || r.status === "captcha" ? "err" : "ok");
      loadCheckin();
    } catch (e) { setStatus("#hl-status", e.message, "err"); }
  });
}

/* ---------------- 初始化 ---------------- */
function bind() {
  $$("#tabs button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
  $$("[data-copy]").forEach((b) => (b.onclick = () => {
    const el = $("#" + b.dataset.copy);
    navigator.clipboard.writeText(el.value || el.textContent).then(() => toast("已複製"));
  }));
  $("#btn-import-url").onclick = (e) => importUrl(e.target);
  $("#ocr-gacha-file").onchange = (e) => { if (e.target.files[0]) ocrGacha(e.target.files[0]); e.target.value = ""; };
  $("#ocr-disc-file").onchange = (e) => { if (e.target.files[0]) ocrDisc(e.target.files[0]); e.target.value = ""; };
  $("#btn-refresh-stats").onclick = refreshStats;
  $("#btn-clear-pulls").onclick = async () => {
    if (!confirm("確定要刪除你所有的抽卡紀錄嗎？")) return;
    try { const r = await api("/api/pulls", { method: "DELETE" }); toast(`已刪除 ${r.deleted} 筆`); refreshStats(); } catch (e) { toast(e.message, true); }
  };
  $("#btn-score").onclick = (e) => scoreDisc(e.target);
  $("#btn-create-team").onclick = (e) => createTeam(e.target);
  $("#btn-hl-save").onclick = (e) => saveCheckin(e.target);
  $("#btn-hl-run").onclick = (e) => runCheckin(e.target);
  $("#btn-hl-delete").onclick = async () => {
    if (!confirm("確定刪除 HoYoLAB 設定？")) return;
    try { await api("/api/hoyolab", { method: "DELETE" }); toast("已刪除"); $("#hl-cookie").placeholder = ""; loadCheckin(); } catch (e) { toast(e.message, true); }
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
  $("#api-base").textContent = API;
  $("#ps-cmd").textContent = `powershell -ExecutionPolicy Bypass -c "iex (irm '${SITE}get_gacha_url.ps1')"`;
  initDepth();  // 不依賴 API，先初始化，避免 API 失敗時按鈕沒反應
  init3d();

  // 初始化失敗時自動重試，冷啟動或網路不穩時比較不會卡住
  for (let attempt = 1; ; attempt++) {
    try {
      const [me, ag] = await Promise.all([ensureProfile(), api("/api/agents", { auth: false })]);
      state.agents = ag.agents; state.statLabels = ag.stat_labels; state.specialties = ag.specialties;
      renderMe(me);
      initDiscForm();
      initAgents();
      break;
    } catch (e) {
      if (attempt >= 3) { toast(e.message, true); break; }
      await new Promise((r) => setTimeout(r, 1000 * attempt));
    }
  }

  refreshStats();
  const params = new URLSearchParams(location.search);
  const viewId = params.get("view"), modelId = params.get("model");
  if (modelId) {
    showTab("model3d");
    api(`/api/model3d/${encodeURIComponent(modelId)}`, { auth: false }).then(openModel).catch((e) => toast(e.message, true));
  } else if (viewId) {
    showTab("depth");
    api(`/api/depth/${encodeURIComponent(viewId)}`, { auth: false }).then(openWork).catch((e) => toast(e.message, true));
  } else {
    const t = params.get("tab") || load("zzz_tab");
    showTab(t && document.getElementById("tab-" + t) ? t : "gacha");
  }
}
main();
