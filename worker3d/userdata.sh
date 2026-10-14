#!/bin/bash
# EC2 第一次開機時執行：安裝 TripoSR 環境並註冊 systemd 服務
# __QUEUE_URL__ 等佔位符由 deploy.sh 替換
set -eux
exec > /var/log/zzz3d-setup.log 2>&1

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3-venv python3-dev git build-essential libgl1 libglib2.0-0

mkdir -p /opt/zzz3d && cd /opt/zzz3d
git clone --depth 1 https://github.com/VAST-AI-Research/TripoSR.git
python3 -m venv venv
venv/bin/pip install -U pip wheel
venv/bin/pip install torch==2.4.1 torchvision==0.19.1 --index-url https://download.pytorch.org/whl/cpu
venv/bin/pip install "numpy<2" omegaconf==2.3.0 einops==0.7.0 transformers==4.35.0 \
  "huggingface-hub<0.26" trimesh==4.5.3 fast-simplification PyMCubes scipy imageio \
  rembg==2.0.57 "onnxruntime<1.20" boto3 Pillow

cat > /opt/zzz3d/env <<'EOF'
AWS_DEFAULT_REGION=__REGION__
QUEUE_URL=__QUEUE_URL__
TABLE_NAME=__TABLE__
WEB_BUCKET=__WEB_BUCKET__
CODE_BUCKET=__CODE_BUCKET__
IDLE_MINUTES=20
MC_RESOLUTION=192
MAX_FACES=60000
HF_HOME=/opt/zzz3d/hf
U2NET_HOME=/opt/zzz3d/u2net
EOF

# 每次服務啟動前從 S3 抓最新版 worker.py，更新程式不用重建機器
cat > /opt/zzz3d/fetch_code.py <<'EOF'
import os, boto3
boto3.client("s3").download_file(os.environ["CODE_BUCKET"], "code/worker3d/worker.py", "/opt/zzz3d/worker.py")
EOF

cat > /etc/systemd/system/zzz3d.service <<'EOF'
[Unit]
Description=ZZZ Proxy Hub 3D worker
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=/opt/zzz3d/env
WorkingDirectory=/opt/zzz3d
ExecStartPre=/opt/zzz3d/venv/bin/python /opt/zzz3d/fetch_code.py
ExecStart=/opt/zzz3d/venv/bin/python -u /opt/zzz3d/worker.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now zzz3d.service
echo "SETUP DONE"
