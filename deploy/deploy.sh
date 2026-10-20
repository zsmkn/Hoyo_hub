#!/usr/bin/env bash
# ZZZ Proxy Hub 一鍵部署（AWS Learner Lab）
# 用法：在專案根目錄執行  bash deploy/deploy.sh
# 可重複執行：已存在的資源會更新而不是重建。
set -euo pipefail
# PowerShell 裡打 bash 可能叫到 WSL 的 bash（裡面沒有 Windows 版的 aws / python），這些腳本要用 Git Bash 執行
if grep -qi microsoft /proc/version 2>/dev/null; then
  echo "這是 WSL 的 bash，請改用 Git Bash 執行，例如在 PowerShell 輸入：" >&2
  echo '  & "C:\Program Files\Git\bin\bash.exe" '"$0" >&2
  echo "或直接雙擊 quick_deploy_aws\deploy.bat" >&2
  exit 1
fi
command -v aws >/dev/null || { echo "找不到 AWS CLI，請先安裝：https://aws.amazon.com/cli/" >&2; exit 1; }
export MSYS_NO_PATHCONV=1            # Git Bash 下避免路徑被轉換
export AWS_PROFILE="${AWS_PROFILE:-learnerlab}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
export AWS_PAGER=""
cd "$(dirname "$0")/.."

REGION="$AWS_DEFAULT_REGION"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
APP=zzz-hub
TABLE=$APP
WEB_BUCKET="$APP-web-$ACCOUNT"
UP_BUCKET="$APP-uploads-$ACCOUNT"
FN_API="$APP-api"; FN_WORKER="$APP-worker"; FN_CHECKIN="$APP-checkin"; FN_DEPTH="$APP-depth"
LAYER_NAME="$APP-depth-deps"
MODEL_KEY="models/depth_anything_v2_small.onnx"
MODEL_URL="https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx"
ROLE_ARN=$(aws iam get-role --role-name LabRole --query Role.Arn --output text)
BUILD=build; mkdir -p "$BUILD"

# Bot API Key：第一次部署時產生，之後沿用
SECRETS=deploy/.secrets
if [ ! -f "$SECRETS" ]; then
  echo "BOT_API_KEY=$(python -c 'import secrets;print(secrets.token_urlsafe(32))')" > "$SECRETS"
fi
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

# 網站 bucket：公開讀取 + 靜態網站
aws s3api put-public-access-block --bucket "$WEB_BUCKET" --public-access-block-configuration \
  BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false
cat > "$BUILD/web-policy.json" <<EOF
{"Version":"2012-10-17","Statement":[{"Sid":"PublicRead","Effect":"Allow","Principal":"*",
 "Action":"s3:GetObject","Resource":"arn:aws:s3:::$WEB_BUCKET/*"}]}
EOF
aws s3api put-bucket-policy --bucket "$WEB_BUCKET" --policy "file://$BUILD/web-policy.json"
aws s3 website "s3://$WEB_BUCKET" --index-document index.html --error-document index.html
SITE_URL="http://$WEB_BUCKET.s3-website-$REGION.amazonaws.com"

# 上傳 bucket：瀏覽器直傳的 CORS + 1 天後自動刪除
cat > "$BUILD/cors.json" <<EOF
{"CORSRules":[{"AllowedOrigins":["*"],"AllowedMethods":["PUT"],"AllowedHeaders":["*"],"MaxAgeSeconds":3000}]}
EOF
aws s3api put-bucket-cors --bucket "$UP_BUCKET" --cors-configuration "file://$BUILD/cors.json"
cat > "$BUILD/lifecycle.json" <<EOF
{"Rules":[{"ID":"expire-uploads","Status":"Enabled","Filter":{"Prefix":"uploads/"},"Expiration":{"Days":1}}]}
EOF
aws s3api put-bucket-lifecycle-configuration --bucket "$UP_BUCKET" --lifecycle-configuration "file://$BUILD/lifecycle.json" >/dev/null

# ---------------- 2.5D：AI 模型與相依套件 ----------------
log "Depth Anything V2 模型 → S3"
if ! aws s3api head-object --bucket "$UP_BUCKET" --key "$MODEL_KEY" >/dev/null 2>&1; then
  mkdir -p "$BUILD/models"
  [ -f "$BUILD/models/depth.onnx" ] || curl -sSL --fail -o "$BUILD/models/depth.onnx" "$MODEL_URL"
  aws s3 cp "$BUILD/models/depth.onnx" "s3://$UP_BUCKET/$MODEL_KEY" >/dev/null
fi

log "Lambda Layer（numpy / onnxruntime / Pillow）"
LAYER_ARN=$(aws lambda list-layer-versions --layer-name "$LAYER_NAME" --query 'LayerVersions[0].LayerVersionArn' --output text 2>/dev/null || echo None)
if [ "$LAYER_ARN" = "None" ] || [ -z "$LAYER_ARN" ] || [ "${REBUILD_LAYER:-0}" = "1" ]; then
  rm -rf "$BUILD/layer" "$BUILD/depth-layer.zip"; mkdir -p "$BUILD/layer/python"
  python -m pip install --quiet --target "$BUILD/layer/python" \
    --platform manylinux_2_28_x86_64 --platform manylinux_2_27_x86_64 \
    --platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64 \
    --implementation cp --python-version 3.12 --only-binary=:all: --no-deps \
    onnxruntime numpy pillow
  ( cd "$BUILD/layer/python" && rm -rf bin onnxruntime/transformers onnxruntime/tools \
      onnxruntime/quantization onnxruntime/datasets numpy/*/tests numpy/tests numpy/_pyinstaller numpy/f2py numpy/doc \
    && find . -name "__pycache__" -type d -prune -exec rm -rf {} + ; find . -name "*.pyi" -delete )
  python - <<'PYEOF'
import zipfile, os
root = "build/layer"
with zipfile.ZipFile("build/depth-layer.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for d, _, files in os.walk(root):
        for f in files:
            full = os.path.join(d, f)
            z.write(full, os.path.relpath(full, root))
PYEOF
  aws s3 cp "$BUILD/depth-layer.zip" "s3://$UP_BUCKET/layers/depth-layer.zip" >/dev/null
  LAYER_ARN=$(aws lambda publish-layer-version --layer-name "$LAYER_NAME" \
    --content "S3Bucket=$UP_BUCKET,S3Key=layers/depth-layer.zip" \
    --compatible-runtimes python3.12 --compatible-architectures x86_64 \
    --query LayerVersionArn --output text)
fi
echo "  $LAYER_ARN"

# ---------------- 3D：SQS 佇列 + EC2 worker ----------------
log "SQS 佇列 $APP-model3d"
QUEUE_URL=$(aws sqs create-queue --queue-name "$APP-model3d" --query QueueUrl --output text)
QUEUE_ARN=$(aws sqs get-queue-attributes --queue-url "$QUEUE_URL" --attribute-names QueueArn --query Attributes.QueueArn --output text)
python - "$QUEUE_ARN" "$UP_BUCKET" "$ACCOUNT" > "$BUILD/sqs-attrs.json" <<'PYEOF'
import json, sys
arn, bucket, account = sys.argv[1:4]
policy = {"Version": "2012-10-17", "Statement": [{
    "Effect": "Allow", "Principal": {"Service": "s3.amazonaws.com"}, "Action": "sqs:SendMessage",
    "Resource": arn, "Condition": {"ArnLike": {"aws:SourceArn": f"arn:aws:s3:::{bucket}"},
                                   "StringEquals": {"aws:SourceAccount": account}}}]}
print(json.dumps({"Policy": json.dumps(policy), "VisibilityTimeout": "900",
                  "ReceiveMessageWaitTimeSeconds": "20", "MessageRetentionPeriod": "86400"}))
PYEOF
aws sqs set-queue-attributes --queue-url "$QUEUE_URL" --attributes "file://$BUILD/sqs-attrs.json"

log "上傳 3D worker 程式到 S3"
aws s3 cp worker3d/worker.py "s3://$UP_BUCKET/code/worker3d/worker.py" >/dev/null

log "EC2 3D worker（r5.large，Learner Lab 可用的最大記憶體機型）"
INSTANCE_ID=$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=$APP-3d-worker" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)
if [ "$INSTANCE_ID" = "None" ] || [ -z "$INSTANCE_ID" ]; then
  VPC_ID=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
  SG_ID=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$APP-3d-worker" "Name=vpc-id,Values=$VPC_ID" \
    --query 'SecurityGroups[0].GroupId' --output text)
  if [ "$SG_ID" = "None" ]; then  # 不開任何 inbound，worker 只主動連出去
    SG_ID=$(aws ec2 create-security-group --group-name "$APP-3d-worker" --vpc-id "$VPC_ID" \
      --description "ZZZ 3D worker (no inbound)" --query GroupId --output text)
  fi
  AMI=$(aws ssm get-parameter --name /aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id \
    --query Parameter.Value --output text)
  sed -e "s#__REGION__#$REGION#" -e "s#__QUEUE_URL__#$QUEUE_URL#" -e "s#__TABLE__#$TABLE#" \
      -e "s#__WEB_BUCKET__#$WEB_BUCKET#" -e "s#__CODE_BUCKET__#$UP_BUCKET#" worker3d/userdata.sh > "$BUILD/userdata.sh"
  INSTANCE_ID=$(aws ec2 run-instances --image-id "$AMI" --instance-type r5.large \
    --iam-instance-profile Name=LabInstanceProfile --security-group-ids "$SG_ID" \
    --user-data "fileb://$BUILD/userdata.sh" \
    --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=30,VolumeType=gp3}' \
    --instance-initiated-shutdown-behavior stop \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$APP-3d-worker}]" \
    --query 'Instances[0].InstanceId' --output text)
  echo "  新建 $INSTANCE_ID（首次安裝約 10～15 分鐘）"
else
  echo "  沿用 $INSTANCE_ID"
  # 主機在跑的話重啟服務，讓它抓最新的 worker.py（停機中的會在下次開機時自動更新）
  if [ "$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" --query 'Reservations[0].Instances[0].State.Name' --output text)" = "running" ]; then
    aws ssm send-command --instance-ids "$INSTANCE_ID" --document-name AWS-RunShellScript \
      --parameters 'commands=["systemctl restart zzz3d"]' >/dev/null 2>&1 && echo "  已重啟 worker 服務" || true
  fi
fi

# ---------------- Lambda ----------------
log "打包 Lambda 程式碼"
rm -f "$BUILD/backend.zip"
python - <<'EOF'
import zipfile, glob, os
with zipfile.ZipFile("build/backend.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for f in glob.glob("backend/*.py"):
        z.write(f, os.path.basename(f))
EOF

ENV_JSON="{\"Variables\":{\"TABLE_NAME\":\"$TABLE\",\"UPLOAD_BUCKET\":\"$UP_BUCKET\",\"WORKER_FN\":\"$FN_WORKER\",\"BOT_API_KEY\":\"$BOT_API_KEY\",\"WEB_BUCKET\":\"$WEB_BUCKET\",\"MODEL_BUCKET\":\"$UP_BUCKET\",\"MODEL_KEY\":\"$MODEL_KEY\",\"QUEUE_URL\":\"$QUEUE_URL\",\"WORKER_INSTANCE_ID\":\"$INSTANCE_ID\"}}"
echo "$ENV_JSON" > "$BUILD/env.json"

deploy_fn() {  # name handler timeout memory [其他 lambda 參數…]
  local NAME=$1 HANDLER=$2 TIMEOUT=$3 MEM=$4; shift 4
  if aws lambda get-function --function-name "$NAME" >/dev/null 2>&1; then
    aws lambda update-function-code --function-name "$NAME" --zip-file "fileb://$BUILD/backend.zip" >/dev/null
    aws lambda wait function-updated-v2 --function-name "$NAME"
    aws lambda update-function-configuration --function-name "$NAME" --handler "$HANDLER" \
      --timeout "$TIMEOUT" --memory-size "$MEM" --environment "file://$BUILD/env.json" "$@" >/dev/null
  else
    aws lambda create-function --function-name "$NAME" --runtime python3.12 --role "$ROLE_ARN" \
      --handler "$HANDLER" --timeout "$TIMEOUT" --memory-size "$MEM" \
      --zip-file "fileb://$BUILD/backend.zip" --environment "file://$BUILD/env.json" "$@" >/dev/null
  fi
  aws lambda wait function-updated-v2 --function-name "$NAME"
  echo "  $NAME ✔"
}
log "部署 Lambda 函式"
deploy_fn "$FN_API" api.handler 29 256
deploy_fn "$FN_WORKER" worker.handler 300 512
deploy_fn "$FN_CHECKIN" checkin.handler 300 256
deploy_fn "$FN_DEPTH" depth.handler 120 3008 --layers "$LAYER_ARN" --ephemeral-storage Size=1024

# S3 上傳 → worker
log "S3 事件通知 → worker / depth"
for FN in "$FN_WORKER" "$FN_DEPTH"; do
  aws lambda add-permission --function-name "$FN" --statement-id s3-invoke \
    --action lambda:InvokeFunction --principal s3.amazonaws.com \
    --source-arn "arn:aws:s3:::$UP_BUCKET" --source-account "$ACCOUNT" >/dev/null 2>&1 || true
done
WORKER_ARN=$(aws lambda get-function --function-name "$FN_WORKER" --query Configuration.FunctionArn --output text)
DEPTH_ARN=$(aws lambda get-function --function-name "$FN_DEPTH" --query Configuration.FunctionArn --output text)
rule() {  # function_arn prefix
  echo "{\"LambdaFunctionArn\":\"$1\",\"Events\":[\"s3:ObjectCreated:*\"],\"Filter\":{\"Key\":{\"FilterRules\":[{\"Name\":\"prefix\",\"Value\":\"$2\"}]}}}"
}
QUEUE_RULE="{\"QueueArn\":\"$QUEUE_ARN\",\"Events\":[\"s3:ObjectCreated:*\"],\"Filter\":{\"Key\":{\"FilterRules\":[{\"Name\":\"prefix\",\"Value\":\"uploads/model3d/\"}]}}}"
echo "{\"LambdaFunctionConfigurations\":[$(rule "$WORKER_ARN" uploads/gacha/),$(rule "$WORKER_ARN" uploads/disc/),$(rule "$DEPTH_ARN" uploads/depth/)],\"QueueConfigurations\":[$QUEUE_RULE]}" > "$BUILD/notify.json"
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
# 限流：每秒 50 個請求、瞬間 100 個，超過回 429（防止被濫用、刷爆 Lambda 費用）
aws apigatewayv2 update-stage --api-id "$API_ID" --stage-name '$default'   --default-route-settings ThrottlingBurstLimit=100,ThrottlingRateLimit=50 >/dev/null
API_URL="https://$API_ID.execute-api.$REGION.amazonaws.com"

# ---------------- EventBridge 每日簽到 ----------------
log "EventBridge 排程（每天 00:10 UTC+8 = 16:10 UTC）"
RULE_ARN=$(aws events put-rule --name "$APP-daily-checkin" --schedule-expression 'cron(10 16 * * ? *)' \
  --description "ZZZ HoYoLAB daily check-in" --query RuleArn --output text)
CHECKIN_ARN=$(aws lambda get-function --function-name "$FN_CHECKIN" --query Configuration.FunctionArn --output text)
aws events put-targets --rule "$APP-daily-checkin" --targets "Id=checkin,Arn=$CHECKIN_ARN" >/dev/null
aws lambda add-permission --function-name "$FN_CHECKIN" --statement-id events-invoke \
  --action lambda:InvokeFunction --principal events.amazonaws.com --source-arn "$RULE_ARN" >/dev/null 2>&1 || true

# ---------------- 前端 ----------------
log "上傳前端到 S3"
echo "window.APP_CONFIG = { API_BASE: \"$API_URL\" };" > frontend/config.js
aws s3 sync frontend "s3://$WEB_BUCKET" --delete --exclude "*.ps1" --exclude "gallery/*" --exclude "gallery3d/*" --cache-control "no-cache" >/dev/null
aws s3 cp frontend/get_gacha_url.ps1 "s3://$WEB_BUCKET/get_gacha_url.ps1" --content-type "text/plain" --cache-control "no-cache" >/dev/null

# ---------------- 初始資料 ----------------
log "寫入預設配隊"
aws lambda invoke --function-name "$FN_WORKER" --cli-binary-format raw-in-base64-out \
  --payload '{"action":"seed"}' "$BUILD/seed-out.json" >/dev/null && cat "$BUILD/seed-out.json"; echo

cat > deploy/outputs.env <<EOF
SITE_URL=$SITE_URL
API_URL=$API_URL
WEB_BUCKET=$WEB_BUCKET
UPLOAD_BUCKET=$UP_BUCKET
TABLE=$TABLE
QUEUE_URL=$QUEUE_URL
WORKER_INSTANCE_ID=$INSTANCE_ID
EOF

log "完成！"
echo "  網站：$SITE_URL"
echo "  API ：$API_URL"
echo "  Bot API Key 存在 deploy/.secrets"
