#!/usr/bin/env bash
# 一鍵部署主站（絕區零 / 原神 / 星穹鐵道）到 Cloudflare，不需要 AWS。
#   Workers（API + 網頁）、D1（資料庫）、KV（作品檔案）、Queues（背景工作）、
#   Cron Triggers（每小時簽到）、Workers AI（OCR）；2.5D 深度估計在使用者的瀏覽器計算。
# 程式碼：ZZZ_proxy_hub/cloudflare/（後端）、ZZZ_proxy_hub/frontend/（網頁，與本機版、AWS 版共用）
# 用法：雙擊 quick_deploy_cloudflare\deploy.bat，或在 Git Bash 執行  bash quick_deploy_cloudflare/deploy_all.sh
# 可重複執行：已存在的資源會沿用，資料不會被清掉。
set -euo pipefail
if grep -qi microsoft /proc/version 2>/dev/null; then
  echo "這是 WSL 的 bash，請改用 Git Bash 執行，或直接雙擊 quick_deploy_cloudflare\\deploy.bat" >&2
  exit 1
fi
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."
ROOT=$(cygpath -m "$(pwd)")   # Windows 格式（C:/...），給 node / wrangler 用
source quick_deploy_cloudflare/cf_lib.sh
APP=hoyo-hub
QUEUE=hoyo-hub-jobs
SRC=ZZZ_proxy_hub/cloudflare
START=$(date +%s)

# ---------------------------------------------------------------- 1. 工具與登入
step "1/5 檢查工具、登入 Cloudflare"
cf_login
PY=""
for c in "ZZZ_proxy_hub/.venv/Scripts/python" "py -3" python3 python; do
  if $c -c 'import sys; assert sys.version_info >= (3, 9)' >/dev/null 2>&1; then PY=$c; break; fi
done
[ -n "$PY" ] || die "找不到 Python 3，請先安裝：https://www.python.org/downloads/（只用來匯出遊戲資料）"
ok "Python：$($PY --version 2>&1)"

# ---------------------------------------------------------------- 2. 雲端資源
step "2/5 建立 D1 資料庫、KV、Queue（已存在就沿用）"
D1_ID=$(cf_pick "$(wrangler d1 list --json 2>/dev/null)" name "$APP" uuid)
if [ -z "$D1_ID" ]; then
  wrangler d1 create "$APP" >/dev/null || die "無法建立 D1 資料庫"
  D1_ID=$(cf_pick "$(wrangler d1 list --json 2>/dev/null)" name "$APP" uuid)
fi
[ -n "$D1_ID" ] || die "找不到 D1 資料庫 $APP"
ok "D1  $APP（$D1_ID）"

KV_ID=$(cf_pick "$(wrangler kv namespace list 2>/dev/null)" title "$APP-files" id)
if [ -z "$KV_ID" ]; then
  wrangler kv namespace create "$APP-files" >/dev/null || die "無法建立 KV"
  KV_ID=$(cf_pick "$(wrangler kv namespace list 2>/dev/null)" title "$APP-files" id)
fi
[ -n "$KV_ID" ] || die "找不到 KV $APP-files"
ok "KV  $APP-files（$KV_ID）"

if ! wrangler queues info "$QUEUE" >/dev/null 2>&1; then
  wrangler queues create "$QUEUE" >/dev/null || die "無法建立 Queue"
fi
ok "Queue $QUEUE"

SCHEMA=$(node -e 'import("file:///" + process.argv[1]).then((m) => console.log(m.SCHEMA.replace(/\s+/g, " ")))' "$ROOT/$SRC/src/store.js")
wrangler d1 execute "$APP" --remote --command "$SCHEMA" >/dev/null || die "無法建立資料表"
ok "資料表 items"

# ---------------------------------------------------------------- 3. 打包
step "3/5 打包網頁與遊戲資料"
$PY "$SRC/export_games.py" "$SRC/src/games.json"
ok "遊戲資料 → $SRC/src/games.json"
rm -rf "$CF_BUILD/site"; mkdir -p "$CF_BUILD/site"
cp -r ZZZ_proxy_hub/frontend/. "$CF_BUILD/site"/
echo 'window.APP_CONFIG = { API_BASE: "" };' > "$CF_BUILD/site/config.js"   # 網頁和 API 在同一個網域
# CSP：允許瀏覽器端 2.5D 從 jsDelivr 載入 transformers.js（只限 depth_client.js 指定的版本的 dist/）、
# 從 Hugging Face 下載模型（AWS 版的 CSP 不變）
node -e '
  const fs = require("fs"), [f, dc] = process.argv.slice(1);
  const m = fs.readFileSync(dc, "utf8").match(/TF_URL = "(https:\/\/cdn\.jsdelivr\.net\/npm\/@huggingface\/transformers@[\d.]+\/dist\/)/);
  if (!m) throw new Error("depth_client.js 找不到 TF_URL");
  const tf = m[1], q = (s) => "\x27" + s + "\x27";
  const csp = [
    `default-src ${q("self")}`,
    `script-src ${q("self")} https://cdnjs.cloudflare.com ${tf} ${q("wasm-unsafe-eval")}`,
    `style-src ${q("self")} ${q("unsafe-inline")} https://fonts.googleapis.com`,
    "font-src https://fonts.gstatic.com",
    `img-src ${q("self")} data: blob:`,
    `media-src ${q("self")} blob:`,
    `connect-src ${q("self")} ${tf} https://huggingface.co https://*.huggingface.co https://*.hf.co blob: data:`,
    `worker-src ${q("self")} blob:`,
    `object-src ${q("none")}`, `base-uri ${q("self")}`, `form-action ${q("self")}`,
  ].join("; ");
  const html = fs.readFileSync(f, "utf8");
  // 不送 Referer：Hugging Face 對 Referer 是 *.workers.dev 的請求回 404（沒有 CORS 標頭），模型會下載失敗
  const out = html.replace(/(http-equiv="Content-Security-Policy" content=")[^"]*"/, (_, a) => a + csp + "\"")
    .replace(/<meta name="referrer" content="[^"]*">/, "<meta name=\"referrer\" content=\"no-referrer\">");
  if (!out.includes(csp) || !out.includes("no-referrer")) throw new Error("找不到 CSP 或 referrer 設定");
  fs.writeFileSync(f, out);' "$CF_BUILD/site/index.html" "$ROOT/ZZZ_proxy_hub/frontend/depth_client.js"

# 靜態檔案的安全標頭（meta CSP 不能設 frame-ancestors，要用回應標頭）。
# 只允許自己帳號的 workers.dev 網站嵌入（入口網站的「預覽主站」用 iframe）
write_headers() {  # 子網域（可以是空的：第一次部署還不知道子網域）
  local fa="'self'"
  [ -n "$1" ] && fa="'self' https://*.$1.workers.dev"
  cat > "$CF_BUILD/site/_headers" <<EOF
/*
  Content-Security-Policy: frame-ancestors $fa
  X-Content-Type-Options: nosniff
  Referrer-Policy: no-referrer
  Strict-Transport-Security: max-age=31536000; includeSubDomains
  Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=()
EOF
}
subdomain_of() { echo "$1" | sed -n 's#^https://[^.]*\.\(.*\)\.workers\.dev$#\1#p'; }
OLD_SUB=$(subdomain_of "$(get "$CF_BUILD/outputs.env" SITE_URL)")
write_headers "$OLD_SUB"
ok "網頁 → $CF_BUILD/site"
sed -e "s/__D1_ID__/$D1_ID/" -e "s/__KV_ID__/$KV_ID/" "$SRC/wrangler.template.toml" > "$SRC/wrangler.toml"

# ---------------------------------------------------------------- 4. 部署
step "4/5 部署 Worker（API + 網頁 + 佇列 + 排程）"
OUT=$( cd "$SRC" && wrangler deploy 2>&1 ) || { echo "$OUT"; die "部署失敗"; }
echo "$OUT" | grep -E "Uploaded|Deployed|workers.dev|schedule|Producer|Consumer" | sed 's/^/  /' || true
SITE_URL=$(echo "$OUT" | grep -o 'https://[a-z0-9.-]*\.workers\.dev' | head -1)
[ -n "$SITE_URL" ] || die "找不到網址（第一次使用 Workers 請先到 Cloudflare 後台 Workers & Pages 頁面開通 workers.dev 子網域）"
NEW_SUB=$(subdomain_of "$SITE_URL")
if [ "$NEW_SUB" != "$OLD_SUB" ]; then  # 第一次部署才知道子網域：補上 frame-ancestors 再部署一次
  write_headers "$NEW_SUB"
  ( cd "$SRC" && wrangler deploy >/dev/null 2>&1 ) || die "部署失敗"
  ok "已更新安全標頭（*.$NEW_SUB.workers.dev）"
fi

# ---------------------------------------------------------------- 5. 金鑰
step "5/5 Bot API Key 與上傳簽章金鑰（第一次產生，之後沿用）"
SECRETS="$CF_DIR/.secrets"
if [ ! -f "$SECRETS" ]; then
  node -e 'const c = require("crypto"); console.log("BOT_API_KEY=" + c.randomBytes(32).toString("base64url")); console.log("SIGNING_KEY=" + c.randomBytes(32).toString("base64url"))' > "$SECRETS"
fi
trap 'rm -f "$CF_BUILD/secrets.json"' EXIT   # 中途失敗也不留下明文金鑰檔
node -e '
  const fs = require("fs");
  const kv = Object.fromEntries(fs.readFileSync(process.argv[1], "utf8").split(/\r?\n/).filter(Boolean).map((l) => [l.split("=")[0], l.slice(l.indexOf("=") + 1)]));
  fs.writeFileSync(process.argv[2], JSON.stringify(kv));' "$SECRETS" "$CF_BUILD/secrets.json"
( cd "$SRC" && wrangler secret bulk "$ROOT/$CF_BUILD/secrets.json" >/dev/null ) || die "無法設定金鑰"
rm -f "$CF_BUILD/secrets.json"
ok "已設定 BOT_API_KEY、SIGNING_KEY（存在 $SECRETS）"

# ---------------------------------------------------------------- 結果
cat > "$CF_BUILD/outputs.env" <<EOF
SITE_URL=$SITE_URL
API_URL=$SITE_URL
EOF
ELAPSED=$(( $(date +%s) - START ))
cat > "$CF_DIR/urls.txt" <<EOF
# Cloudflare 部署結果（$(date '+%Y-%m-%d %H:%M')）
主站（三款遊戲）  $SITE_URL/
主站 API          $SITE_URL（與網頁同網域）
Discord Bot Key   $SECRETS
EOF

printf '\n\033[1;32m══════════ 部署完成（%d 分 %d 秒）══════════\033[0m\n' $((ELAPSED / 60)) $((ELAPSED % 60))
sed '1d' "$CF_DIR/urls.txt" | sed 's/^/  /'
echo
echo "  網址也存在 quick_deploy_cloudflare/urls.txt。剛建立的 workers.dev 網址可能要 1～2 分鐘才會生效。"
