#!/usr/bin/env bash
# Cloudflare 共用函式（給 quick_deploy_cloudflare 與 quick_deploy_cloudflare_class 用）
# 使用前先 cd 到專案根目錄再 source 這個檔案。

export WRANGLER_SEND_METRICS=false
export PYTHONIOENCODING=utf-8
CF_DIR=quick_deploy_cloudflare
CF_BUILD=build/cloudflare
COMPAT_DATE=2026-10-01

step() { printf '\n\033[1;36m████ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✔ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m  ✘ %s\033[0m\n' "$*" >&2; exit 1; }

# 剛裝好 Node.js / wrangler、還沒重開視窗時 PATH 可能還沒更新，先補上預設安裝位置
cf_path() {
  local d
  for d in "/c/Program Files/nodejs" ${APPDATA:+"$(cygpath -u "$APPDATA")/npm"}; do
    [ -d "$d" ] && PATH="$d:$PATH"
  done
  export PATH
}

# 檢查 Node.js / wrangler，並確認已登入 Cloudflare
#   有 quick_deploy_cloudflare/cloudflare.env（CLOUDFLARE_API_TOKEN、CLOUDFLARE_ACCOUNT_ID）就用 API Token，
#   否則用 wrangler login 開瀏覽器登入（只有第一次需要）
cf_login() {
  cf_path
  command -v node >/dev/null || die "找不到 Node.js，請先安裝：https://nodejs.org/（或執行 winget install OpenJS.NodeJS.LTS）"
  command -v wrangler >/dev/null || die "找不到 Cloudflare CLI，請先執行：npm install -g wrangler"
  ok "wrangler $(wrangler --version 2>/dev/null | tail -1)"
  if [ -f "$CF_DIR/cloudflare.env" ]; then
    set -a; source <(tr -d '\r' < "$CF_DIR/cloudflare.env"); set +a
    ok "使用 $CF_DIR/cloudflare.env 的 API Token"
  fi
  if wrangler whoami 2>&1 | grep -qi "not authenticated"; then
    echo "  尚未登入 Cloudflare，即將開啟瀏覽器，請按「Allow」授權 wrangler"
    wrangler login || die "Cloudflare 登入失敗"
  fi
  if wrangler whoami 2>&1 | grep -qi "not authenticated"; then die "Cloudflare 尚未登入"; fi
  ok "已登入 Cloudflare"
}

# 從 JSON 陣列中找 key=value 的項目，印出另一個欄位：cf_pick 'JSON' name hoyo-hub uuid
cf_pick() {
  node -e '
    const [s, k, v, f] = process.argv.slice(1);
    let list = []; try { list = JSON.parse(s.slice(s.indexOf("["))); } catch {}
    const hit = list.find((x) => x[k] === v);
    if (hit) console.log(hit[f]);' "$1" "$2" "$3" "$4"
}

# 部署靜態網站（Workers Static Assets，不需要設定檔），印出網址
#   cf_static 資料夾 Worker名稱
cf_static() {
  local out
  out=$(wrangler deploy --name "$2" --assets "$1" --compatibility-date "$COMPAT_DATE" 2>&1) || { echo "$out" >&2; die "上傳 $2 失敗"; }
  echo "$out" | grep -o 'https://[a-z0-9.-]*\.workers\.dev' | head -1
}

# 讀取 KEY=VALUE 檔案中的某個值
get() { [ -f "$1" ] && grep "^$2=" "$1" | cut -d= -f2- | tr -d '\r' || true; }
