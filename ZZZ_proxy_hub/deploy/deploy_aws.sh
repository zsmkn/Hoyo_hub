#!/usr/bin/env bash
# HoYo Proxy Hub 雲端版（原神 / 崩壞：星穹鐵道）一鍵部署到 AWS Learner Lab
# 用法：在 ZZZ_proxy_hub 目錄執行  bash deploy/deploy_aws.sh
# 可重複執行：已存在的資源會更新而不是重建。程式碼與本機版共用（server/）。
set -euo pipefail
# PowerShell 裡打 bash 可能叫到 WSL 的 bash（裡面沒有 Windows 版的 aws / python），這些腳本要用 Git Bash 執行
if grep -qi microsoft /proc/version 2>/dev/null; then
  echo "這是 WSL 的 bash，請改用 Git Bash 執行，例如在 PowerShell 輸入：" >&2
  echo '  & "C:\Program Files\Git\bin\bash.exe" '"$0" >&2
  echo "或直接雙擊 quick_start\deploy.bat" >&2
  exit 1
fi
command -v aws >/dev/null || { echo "找不到 AWS CLI，請先安裝：https://aws.amazon.com/cli/" >&2; exit 1; }
export MSYS_NO_PATHCONV=1
export AWS_PROFILE="${AWS_PROFILE:-learnerlab}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
export AWS_PAGER=""
cd "$(dirname "$0")/.."

PY=.venv/Scripts/python
REGION="$AWS_DEFAULT_REGION"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
APP=hoyo-hub
TABLE=$APP
WEB_BUCKET="$APP-web-$ACCOUNT"
UP_BUCKET="$APP-uploads-$ACCOUNT"
FN_API="$APP-api"; FN_WORKER="$APP-worker"; FN_CHECKIN="$APP-checkin"
ROLE_ARN=$(aws iam get-role --role-name LabRole --query Role.Arn --output text)
GAMES="${HUB_GAMES:-zzz,gi,hsr}"
MODEL_KEY="models/depth_anything_v2_small.onnx"
BUILD=build; mkdir -p "$BUILD"

# 舊的絕區零雲端站（3D 建模、配隊推薦還在那裡）：絕區零的分頁列多一個「3D 建模 ↗」連過去
ZZZ_SITE=""
[ -f ../deploy/outputs.env ] && ZZZ_SITE=$(grep '^SITE_URL=' ../deploy/outputs.env | cut -d= -f2-)

SECRETS=deploy/.secrets
[ -f "$SECRETS" ] || echo "BOT_API_KEY=$($PY -c 'import secrets;print(secrets.token_urlsafe(32))')" > "$SECRETS"
source "$SECRETS"

log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }

# ---------------- DynamoDB ----------------
log "DynamoDB 資料表 $TABLE"
if ! aws dynamodb describe-table --table-name "$TABLE" >/dev/null 2>&1; then
  aws dynamodb create-table --table-name "$TABLE" \
    --attribute-definitions AttributeName=PK,AttributeType=S AttributeName=SK,AttributeType=S \
    --key-schema AttributeName=PK,KeyType=HASH AttributeName=SK,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST >/dev/null
  aws dynamodb wait table-exists --table-name "$TABLE"
  aws dynamodb update-time-to-live --table-name "$TABLE" \
    --time-to-live-specification Enabled=true,AttributeName=ttl >/dev/null
fi

# ---------------- S3 ----------------
log "S3 buckets"
for B in "$WEB_BUCKET" "$UP_BUCKET"; do
  aws s3api head-bucket --bucket "$B" >/dev/null 2>&1 || aws s3api create-bucket --bucket "$B" >/dev/null
done
aws s3api put-public-access-block --bucket "$WEB_BUCKET" --public-access-block-configuration \
  BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false
cat > "$BUILD/web-policy.json" <<EOF
{"Version":"2012-10-17","Statement":[{"Sid":"PublicRead","Effect":"Allow","Principal":"*",
 "Action":"s3:GetObject","Resource":"arn:aws:s3:::$WEB_BUCKET/*"}]}
EOF
aws s3api put-bucket-policy --bucket "$WEB_BUCKET" --policy "file://$BUILD/web-policy.json"
aws s3 website "s3://$WEB_BUCKET" --index-document index.html --error-document index.html
SITE_URL="http://$WEB_BUCKET.s3-website-$REGION.amazonaws.com"
cat > "$BUILD/cors.json" <<EOF
{"CORSRules":[{"AllowedOrigins":["*"],"AllowedMethods":["PUT"],"AllowedHeaders":["*"],"MaxAgeSeconds":3000}]}
EOF
aws s3api put-bucket-cors --bucket "$UP_BUCKET" --cors-configuration "file://$BUILD/cors.json"
cat > "$BUILD/lifecycle.json" <<EOF
{"Rules":[{"ID":"expire-uploads","Status":"Enabled","Filter":{"Prefix":"uploads/"},"Expiration":{"Days":1}}]}
EOF
aws s3api put-bucket-lifecycle-configuration --bucket "$UP_BUCKET" --lifecycle-configuration "file://$BUILD/lifecycle.json" >/dev/null

# ---------------- AI 模型與相依套件 ----------------
log "Depth Anything V2 模型 → S3"
if ! aws s3api head-object --bucket "$UP_BUCKET" --key "$MODEL_KEY" >/dev/null 2>&1; then
  # 同帳號的絕區零 bucket 已經有同一個模型的話，直接在 S3 內部複製（不用從本機上傳 95MB）
  if aws s3api head-object --bucket "zzz-hub-uploads-$ACCOUNT" --key "$MODEL_KEY" >/dev/null 2>&1; then
    aws s3 cp "s3://zzz-hub-uploads-$ACCOUNT/$MODEL_KEY" "s3://$UP_BUCKET/$MODEL_KEY" --only-show-errors
  else
    MODEL_FILE=$($PY -c "from server import config; from huggingface_hub import hf_hub_download; print(hf_hub_download('onnx-community/depth-anything-v2-small', 'onnx/model.onnx'))" | tr -d '\r')
    aws s3 cp "$MODEL_FILE" "s3://$UP_BUCKET/$MODEL_KEY" --only-show-errors
  fi
fi
aws s3api head-object --bucket "$UP_BUCKET" --key "$MODEL_KEY" --query ContentLength --output text \
  || { echo "模型沒有成功上傳到 S3" >&2; exit 1; }

log "相依套件（numpy / onnxruntime / OpenCV / RapidOCR）→ S3"
if [ ! -f "$BUILD/deps.zip" ]; then
  rm -rf "$BUILD/layer"; mkdir -p "$BUILD/layer/python"
  $PY -m pip install --quiet --target "$BUILD/layer/python" \
    --platform manylinux_2_28_x86_64 --platform manylinux_2_27_x86_64 \
    --platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64 \
    --implementation cp --python-version 3.12 --only-binary=:all: --no-deps \
    numpy pillow onnxruntime opencv-python-headless rapidocr_onnxruntime shapely pyclipper pyyaml six tqdm
  $PY - <<'PYEOF'
import os, shutil, zipfile
root = "build/layer/python"
for d in ["bin", "onnxruntime/transformers", "onnxruntime/tools", "onnxruntime/quantization", "onnxruntime/datasets",
          "numpy/tests", "numpy/_pyinstaller", "numpy/f2py", "numpy/doc", "shapely/tests"]:
    shutil.rmtree(os.path.join(root, d), ignore_errors=True)
for dp, dn, fn in os.walk(root):
    for d in list(dn):
        if d in ("__pycache__", "tests"):
            shutil.rmtree(os.path.join(dp, d), ignore_errors=True)
with zipfile.ZipFile("build/deps.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for dp, _, fn in os.walk(root):
        for f in fn:
            if not f.endswith(".pyi"):
                full = os.path.join(dp, f)
                z.write(full, os.path.relpath(full, root))
PYEOF
fi
DEPS_KEY="deps/hoyo-deps-$($PY -c "import hashlib;print(hashlib.md5(open('build/deps.zip','rb').read()).hexdigest()[:10])").zip"
aws s3api head-object --bucket "$UP_BUCKET" --key "$DEPS_KEY" >/dev/null 2>&1 || \
  aws s3 cp "$BUILD/deps.zip" "s3://$UP_BUCKET/$DEPS_KEY" >/dev/null
echo "  $DEPS_KEY"

# ---------------- Lambda ----------------
log "打包程式碼（與本機版共用 server/）"
$PY - <<'PYEOF'
import os, zipfile
with zipfile.ZipFile("build/code.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for dp, dn, fn in os.walk("server"):
        dn[:] = [d for d in dn if d != "__pycache__"]
        for f in fn:
            if f.endswith(".py"):
                z.write(os.path.join(dp, f))
PYEOF
$PY - "$TABLE" "$WEB_BUCKET" "$UP_BUCKET" "$FN_WORKER" "$BOT_API_KEY" "$GAMES" "$ZZZ_SITE" "$MODEL_KEY" "$DEPS_KEY" > "$BUILD/env.json" <<'PYEOF'
import json, sys
table, web, up, worker, key, games, zzz, model, deps = sys.argv[1:10]
# 本站沒有的遊戲才放進選單的外部連結；絕區零已在本站，只把 3D 建模連到舊站
ext = [{"id": "zzz", "url": zzz + "/"}] if zzz and "zzz" not in games.split(",") else []
links = [{"game": "zzz", "label": "3D 建模", "url": zzz + "/?tab=model3d"}] if zzz else []
print(json.dumps({"Variables": {
    "TABLE_NAME": table, "WEB_BUCKET": web, "UPLOAD_BUCKET": up, "WORKER_FN": worker, "BOT_API_KEY": key,
    "HUB_GAMES": games, "HUB_DEFAULT": games.split(",")[0], "HUB_EXTERNAL": json.dumps(ext), "HUB_LINKS": json.dumps(links),
    "MODEL_BUCKET": up, "MODEL_KEY": model, "DEPS_BUCKET": up, "DEPS_KEY": deps}}))
PYEOF

deploy_fn() {  # name handler timeout memory [其他參數…]
  local NAME=$1 HANDLER=$2 TIMEOUT=$3 MEM=$4; shift 4
  if aws lambda get-function --function-name "$NAME" >/dev/null 2>&1; then
    aws lambda update-function-code --function-name "$NAME" --zip-file "fileb://$BUILD/code.zip" >/dev/null
    aws lambda wait function-updated-v2 --function-name "$NAME"
    aws lambda update-function-configuration --function-name "$NAME" --handler "$HANDLER" \
      --timeout "$TIMEOUT" --memory-size "$MEM" --environment "file://$BUILD/env.json" "$@" >/dev/null
  else
    aws lambda create-function --function-name "$NAME" --runtime python3.12 --role "$ROLE_ARN" \
      --handler "$HANDLER" --timeout "$TIMEOUT" --memory-size "$MEM" \
      --zip-file "fileb://$BUILD/code.zip" --environment "file://$BUILD/env.json" "$@" >/dev/null
  fi
  aws lambda wait function-updated-v2 --function-name "$NAME"
  echo "  $NAME ✔"
}
log "部署 Lambda 函式"
deploy_fn "$FN_API" server.lambda_api.handler 29 512
deploy_fn "$FN_WORKER" server.lambda_worker.handler 600 3008 --ephemeral-storage Size=2048
deploy_fn "$FN_CHECKIN" server.lambda_checkin.handler 300 256

log "S3 上傳事件 → $FN_WORKER"
aws lambda add-permission --function-name "$FN_WORKER" --statement-id s3-invoke \
  --action lambda:InvokeFunction --principal s3.amazonaws.com \
  --source-arn "arn:aws:s3:::$UP_BUCKET" --source-account "$ACCOUNT" >/dev/null 2>&1 || true
WORKER_ARN=$(aws lambda get-function --function-name "$FN_WORKER" --query Configuration.FunctionArn --output text)
echo "{\"LambdaFunctionConfigurations\":[{\"LambdaFunctionArn\":\"$WORKER_ARN\",\"Events\":[\"s3:ObjectCreated:*\"],\"Filter\":{\"Key\":{\"FilterRules\":[{\"Name\":\"prefix\",\"Value\":\"uploads/\"}]}}}]}" > "$BUILD/notify.json"
aws s3api put-bucket-notification-configuration --bucket "$UP_BUCKET" --notification-configuration "file://$BUILD/notify.json"

# ---------------- API Gateway ----------------
log "API Gateway (HTTP API)"
API_ID=$(aws apigatewayv2 get-apis --query "Items[?Name=='$APP-api'].ApiId | [0]" --output text)
API_FN_ARN=$(aws lambda get-function --function-name "$FN_API" --query Configuration.FunctionArn --output text)
if [ "$API_ID" = "None" ] || [ -z "$API_ID" ]; then
  API_ID=$(aws apigatewayv2 create-api --name "$APP-api" --protocol-type HTTP \
    --cors-configuration 'AllowOrigins=*,AllowMethods=GET,POST,PUT,DELETE,OPTIONS,AllowHeaders=content-type,authorization,x-bot-key,MaxAge=3600' \
    --query ApiId --output text)
  INTEG_ID=$(aws apigatewayv2 create-integration --api-id "$API_ID" --integration-type AWS_PROXY \
    --integration-uri "$API_FN_ARN" --payload-format-version 2.0 --query IntegrationId --output text)
  aws apigatewayv2 create-route --api-id "$API_ID" --route-key 'ANY /api/{proxy+}' --target "integrations/$INTEG_ID" >/dev/null
  aws apigatewayv2 create-stage --api-id "$API_ID" --stage-name '$default' --auto-deploy >/dev/null
fi
aws lambda add-permission --function-name "$FN_API" --statement-id apigw-invoke \
  --action lambda:InvokeFunction --principal apigateway.amazonaws.com \
  --source-arn "arn:aws:execute-api:$REGION:$ACCOUNT:$API_ID/*" >/dev/null 2>&1 || true
# 限流（防止被濫用、刷爆 Lambda 費用）：全站每秒 50 個、瞬間 100 個，超過回 429。
# /api/ping 另外限制每秒 2 個，方便實際測試／示範限流（連續打幾十次就會看到 429）。
INTEG_ID=$(aws apigatewayv2 get-integrations --api-id "$API_ID" --query "Items[0].IntegrationId" --output text)
if [ "$(aws apigatewayv2 get-routes --api-id "$API_ID" --query "Items[?RouteKey=='GET /api/ping'] | length(@)" --output text)" = "0" ]; then
  aws apigatewayv2 create-route --api-id "$API_ID" --route-key 'GET /api/ping' --target "integrations/$INTEG_ID" >/dev/null
fi
aws apigatewayv2 update-stage --api-id "$API_ID" --stage-name '$default'   --default-route-settings ThrottlingBurstLimit=100,ThrottlingRateLimit=50   --route-settings '{"GET /api/ping":{"ThrottlingBurstLimit":3,"ThrottlingRateLimit":2}}' >/dev/null
API_URL="https://$API_ID.execute-api.$REGION.amazonaws.com"

# ---------------- EventBridge：每小時檢查一次簽到（00:10 UTC+8 後才動作，失敗自動重試） ----------------
log "EventBridge 每日簽到排程"
RULE_ARN=$(aws events put-rule --name "$APP-checkin" --schedule-expression 'cron(10 * * * ? *)' \
  --description "HoYo Proxy Hub HoYoLAB check-in" --query RuleArn --output text)
CHECKIN_ARN=$(aws lambda get-function --function-name "$FN_CHECKIN" --query Configuration.FunctionArn --output text)
aws events put-targets --rule "$APP-checkin" --targets "Id=checkin,Arn=$CHECKIN_ARN" >/dev/null
aws lambda add-permission --function-name "$FN_CHECKIN" --statement-id events-invoke \
  --action lambda:InvokeFunction --principal events.amazonaws.com --source-arn "$RULE_ARN" >/dev/null 2>&1 || true

# ---------------- 前端 ----------------
log "上傳前端到 S3"
rm -rf "$BUILD/site"; cp -r frontend "$BUILD/site"
echo "window.APP_CONFIG = { API_BASE: \"$API_URL\" };" > "$BUILD/site/config.js"
$PY - "$GAMES" <<'PYEOF'
import sys
from server.routes import render_script
for g in sys.argv[1].split(","):
    open(f"build/site/get_gacha_url_{g}.ps1", "w", encoding="utf-8", newline="\r\n").write(render_script(g))
PYEOF
aws s3 sync "$BUILD/site" "s3://$WEB_BUCKET" --delete --exclude "gallery/*" --exclude "*.ps1" --cache-control "no-cache" >/dev/null
for f in "$BUILD"/site/*.ps1; do
  aws s3 cp "$f" "s3://$WEB_BUCKET/$(basename "$f")" --content-type "text/plain; charset=utf-8" --cache-control "no-cache" >/dev/null
done

cat > deploy/outputs.env <<EOF
SITE_URL=$SITE_URL
API_URL=$API_URL
WEB_BUCKET=$WEB_BUCKET
UPLOAD_BUCKET=$UP_BUCKET
TABLE=$TABLE
GAMES=$GAMES
EOF

log "完成！"
echo "  網站：$SITE_URL"
echo "  API ：$API_URL"
echo "  遊戲：$GAMES（絕區零的 3D 建模連到 ${ZZZ_SITE:-（未設定）}）"
