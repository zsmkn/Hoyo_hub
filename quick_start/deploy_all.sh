#!/usr/bin/env bash
# 一鍵部署到 AWS Academy Learner Lab：3D 建模站 + 主站
#   1. 3D 建模站（deploy/deploy.sh）：舊的絕區零雲端版，提供 3D 建模與配隊推薦
#   2. 主站（ZZZ_proxy_hub/deploy/deploy_aws.sh）：絕區零 / 原神 / 星穹鐵道
# 用法：雙擊 quick_start\deploy.bat，或在 Git Bash 執行  bash quick_start/deploy_all.sh [--hub-only]
#   --hub-only   只部署主站（跳過 3D 建模站；主站的「3D 建模 ↗」連結會沿用上次的網址，沒有就不顯示）
# 可重複執行：已存在的資源會更新而不是重建。
set -euo pipefail
if grep -qi microsoft /proc/version 2>/dev/null; then
  echo "這是 WSL 的 bash，請改用 Git Bash 執行，或直接雙擊 quick_start\\deploy.bat" >&2
  exit 1
fi
export MSYS_NO_PATHCONV=1
export AWS_PROFILE="${AWS_PROFILE:-learnerlab}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
export AWS_PAGER=""
export PYTHONIOENCODING=utf-8
cd "$(dirname "$0")/.."
ROOT=$(pwd)
QS=quick_start
HUB_ONLY=0
for a in "$@"; do
  case "$a" in
    --hub-only) HUB_ONLY=1 ;;
    *) echo "不認得的參數：$a" >&2; exit 1 ;;
  esac
done

step() { printf '\n\033[1;33m████ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✔ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m  ✘ %s\033[0m\n' "$*" >&2; exit 1; }
START=$(date +%s)

# ---------------------------------------------------------------- 1. 檢查工具
step "1/5 檢查工具"
command -v aws >/dev/null || die "找不到 AWS CLI v2，請先安裝：https://aws.amazon.com/cli/"
ok "$(aws --version 2>&1 | cut -d' ' -f1)"

# 找一個可以用的 Python 3.10+（Windows 的 python 可能只是 Microsoft Store 的捷徑）
find_python() {
  local c
  for c in "ZZZ_proxy_hub/.venv/Scripts/python" "py -3.12" "py -3" python3 python; do
    if $c -c 'import sys; assert sys.version_info >= (3, 10)' >/dev/null 2>&1; then echo "$c"; return; fi
  done
}
BASE_PY=$(find_python)
[ -n "$BASE_PY" ] || die "找不到 Python 3.10 以上，請先安裝：https://www.python.org/downloads/（安裝時勾選 Add to PATH）"
ok "Python：$($BASE_PY --version 2>&1)"

# ---------------------------------------------------------------- 2. AWS 憑證
step "2/5 AWS Learner Lab 憑證（profile：$AWS_PROFILE）"
import_creds() {  # 從 Learner Lab「AWS Details → AWS CLI」那段文字匯入
  local text=$1 id secret token
  id=$(printf '%s\n' "$text" | tr -d '\r' | sed -n 's/^[[:space:]]*aws_access_key_id[[:space:]]*=[[:space:]]*//p' | head -1)
  secret=$(printf '%s\n' "$text" | tr -d '\r' | sed -n 's/^[[:space:]]*aws_secret_access_key[[:space:]]*=[[:space:]]*//p' | head -1)
  token=$(printf '%s\n' "$text" | tr -d '\r' | sed -n 's/^[[:space:]]*aws_session_token[[:space:]]*=[[:space:]]*//p' | head -1)
  [ -n "$id" ] && [ -n "$secret" ] && [ -n "$token" ] || return 1
  aws configure set aws_access_key_id "$id"
  aws configure set aws_secret_access_key "$secret"
  aws configure set aws_session_token "$token"
  aws configure set region "$AWS_DEFAULT_REGION"
}
creds_ok() { aws sts get-caller-identity --query Account --output text 2>/dev/null; }

if [ -f "$QS/credentials.txt" ]; then
  if import_creds "$(cat "$QS/credentials.txt")"; then
    rm -f "$QS/credentials.txt"   # 匯入後刪掉，避免金鑰留在專案資料夾
    ok "已從 quick_start/credentials.txt 匯入（檔案已刪除）"
  else
    die "quick_start/credentials.txt 格式不對，請貼上 Learner Lab「AWS Details → AWS CLI」的整段文字"
  fi
fi
ACCOUNT=$(creds_ok || true)
if [ -z "$ACCOUNT" ] && [ -t 0 ]; then
  echo "  目前的憑證無效或已過期（Learner Lab 每次 Start Lab 都會換）。"
  echo "  請到 Learner Lab → AWS Details → AWS CLI 的「Show」，複製整段貼在這裡，貼完按 Enter 兩次："
  PASTED=""
  while IFS= read -r line; do
    line=${line%$'\r'}
    [ -z "$line" ] && [ -n "$PASTED" ] && break
    PASTED+="$line"$'\n'
  done
  import_creds "$PASTED" || die "沒有讀到 aws_access_key_id / aws_secret_access_key / aws_session_token"
  ACCOUNT=$(creds_ok || true)
fi
[ -n "$ACCOUNT" ] || die "憑證無效。請確認 Lab 已經 Start（左上角綠燈），再把新的憑證放進 quick_start/credentials.txt 重跑"
aws iam get-role --role-name LabRole >/dev/null 2>&1 || die "帳號 $ACCOUNT 沒有 LabRole，這看起來不是 Learner Lab 帳號"
ok "AWS 帳號 $ACCOUNT（$AWS_DEFAULT_REGION）"

# ---------------------------------------------------------------- 3. Python 環境
step "3/5 Python 虛擬環境（ZZZ_proxy_hub/.venv）"
VENV=ZZZ_proxy_hub/.venv
if [ ! -x "$VENV/Scripts/python" ] && [ ! -x "$VENV/Scripts/python.exe" ]; then
  echo "  第一次執行：建立虛擬環境並安裝套件（約 3～5 分鐘）"
  $BASE_PY -m venv "$VENV"
  "$VENV/Scripts/python" -m pip install --quiet -U pip wheel
  "$VENV/Scripts/python" -m pip install --quiet -r ZZZ_proxy_hub/requirements.txt
fi
# 兩支部署腳本裡的 python 都用這個虛擬環境
export PATH="$ROOT/$VENV/Scripts:$PATH"
ok "$(python --version)（$VENV）"

# ---------------------------------------------------------------- 4. 3D 建模站
if [ "$HUB_ONLY" = 1 ]; then
  step "4/5 3D 建模站：略過（--hub-only）"
else
  step "4/5 3D 建模站（DynamoDB、S3、SQS、EC2 r5.large、Lambda、API Gateway）"
  echo "  第一次部署約 10 分鐘（要下載 AI 模型並建立 EC2）；之後重跑約 2～3 分鐘"
  bash deploy/deploy.sh
  ok "3D 建模站完成"
fi

# ---------------------------------------------------------------- 5. 主站
step "5/5 主站：絕區零 / 原神 / 星穹鐵道（Lambda、API Gateway、DynamoDB、EventBridge）"
( cd ZZZ_proxy_hub && bash deploy/deploy_aws.sh )
ok "主站完成"

# ---------------------------------------------------------------- 結果
get() { [ -f "$1" ] && grep "^$2=" "$1" | cut -d= -f2- | tr -d '\r' || true; }
HUB_URL=$(get ZZZ_proxy_hub/deploy/outputs.env SITE_URL)
HUB_API=$(get ZZZ_proxy_hub/deploy/outputs.env API_URL)
Z_URL=$(get deploy/outputs.env SITE_URL)
Z_API=$(get deploy/outputs.env API_URL)
ELAPSED=$(( $(date +%s) - START ))
cat > "$QS/urls.txt" <<EOF
# 部署結果（$(date '+%Y-%m-%d %H:%M')，AWS 帳號 $ACCOUNT）
主站（三款遊戲）  $HUB_URL
主站 API          $HUB_API
3D 建模站         ${Z_URL:-（未部署）}${Z_URL:+/?tab=model3d}
3D 建模站 API     ${Z_API:-（未部署）}
Discord Bot Key   主站：ZZZ_proxy_hub/deploy/.secrets　3D 站：deploy/.secrets
EOF

printf '\n\033[1;32m══════════ 部署完成（%d 分 %d 秒）══════════\033[0m\n' $((ELAPSED / 60)) $((ELAPSED % 60))
sed '1d' "$QS/urls.txt" | sed 's/^/  /'
echo
echo "  網址也存在 quick_start/urls.txt"
