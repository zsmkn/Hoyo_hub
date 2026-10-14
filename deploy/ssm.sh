#!/usr/bin/env bash
# 在 3D worker EC2 上執行指令並顯示輸出：bash deploy/ssm.sh "tail -20 /var/log/zzz3d-setup.log"
set -euo pipefail
export MSYS_NO_PATHCONV=1 AWS_PROFILE="${AWS_PROFILE:-learnerlab}" AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}" AWS_PAGER=""
cd "$(dirname "$0")/.."
source deploy/outputs.env
PARAMS=$(python -c 'import json,sys; print(json.dumps({"commands":[sys.argv[1]]}))' "$1")
CID=$(aws ssm send-command --instance-ids "$WORKER_INSTANCE_ID" --document-name AWS-RunShellScript \
  --parameters "$PARAMS" --query Command.CommandId --output text)
for i in $(seq 1 60); do
  sleep 2
  S=$(aws ssm get-command-invocation --command-id "$CID" --instance-id "$WORKER_INSTANCE_ID" --query Status --output text 2>/dev/null || echo Pending)
  case "$S" in Pending|InProgress|Delayed) ;; *) break ;; esac
done
aws ssm get-command-invocation --command-id "$CID" --instance-id "$WORKER_INSTANCE_ID" \
  --query '[StandardOutputContent,StandardErrorContent]' --output text
