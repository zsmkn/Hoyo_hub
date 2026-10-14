"""路徑與設定。必須最先 import（會設定 AI 模型的快取位置）。

同一份程式碼可以跑在兩種環境：
  本機（start.bat）：SQLite + 本機資料夾
  雲端（AWS Lambda，有 TABLE_NAME 環境變數）：DynamoDB + S3，只有 /tmp 可以寫入
"""
import json
import os
import secrets
from pathlib import Path

CLOUD = bool(os.environ.get("TABLE_NAME"))
ROOT = Path(__file__).resolve().parent.parent
DATA = Path("/tmp/hub") if CLOUD else ROOT / "data"
MODELS = Path("/tmp/models") if CLOUD else ROOT / "models"
FRONTEND = ROOT / "frontend"
UPLOADS = DATA / "uploads"
GALLERY = DATA / "gallery"
DB_PATH = DATA / "zzz.db"

for d in (DATA, MODELS, UPLOADS, GALLERY):
    d.mkdir(parents=True, exist_ok=True)

# AI 模型都下載到專案內的 models/，不污染使用者家目錄
os.environ.setdefault("HF_HOME", str(MODELS / "hf"))

HOST = os.environ.get("ZZZ_HOST", "127.0.0.1")
PORT = int(os.environ.get("ZZZ_PORT", "8787"))
MAX_UPLOAD = 10 * 1024 * 1024

# 雲端資源（只有 CLOUD 模式會用到）
TABLE_NAME = os.environ.get("TABLE_NAME", "")
WEB_BUCKET = os.environ.get("WEB_BUCKET", "")
UPLOAD_BUCKET = os.environ.get("UPLOAD_BUCKET", "")
WORKER_FN = os.environ.get("WORKER_FN", "")

# Bot API Key：雲端由部署腳本放在環境變數；本機第一次啟動時產生，存在 data/secrets.json
if CLOUD:
    BOT_API_KEY = os.environ.get("BOT_API_KEY", "")
else:
    _SECRETS = DATA / "secrets.json"
    if _SECRETS.exists():
        _s = json.loads(_SECRETS.read_text(encoding="utf-8"))
    else:
        _s = {"BOT_API_KEY": secrets.token_urlsafe(32)}
        _SECRETS.write_text(json.dumps(_s, indent=2), encoding="utf-8")
    BOT_API_KEY = os.environ.get("ZZZ_BOT_KEY", _s["BOT_API_KEY"])
