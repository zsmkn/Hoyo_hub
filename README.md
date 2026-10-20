# HoYo Proxy Hub｜米哈遊二遊玩家工具站

以 AWS Serverless 架構打造的二遊玩家工具站，支援《絕區零》、《原神》、《崩壞：星穹鐵道》：
抽卡紀錄分析、裝備評分、角色圖鑑、2D → 2.5D 立繪、2D → 3D 模型、HoYoLAB 每日自動簽到、Discord Bot API。

## 專案組成

| 網站 | 內容 | 程式碼 | 部署 |
|---|---|---|---|
| **主站** | 三款遊戲的所有功能（也可以在本機執行，不需要 AWS） | [`ZZZ_proxy_hub/`](ZZZ_proxy_hub/README.md) | `ZZZ_proxy_hub/deploy/deploy_aws.sh` |
| **3D 建模站** | 2D 圖片轉 3D 模型、絕區零配隊推薦；主站絕區零分頁列的「3D 建模 ↗」連到這裡 | 本資料夾（`backend/`、`frontend/`、`worker3d/`） | `deploy/deploy.sh` |

## 快速開始

需要：[AWS CLI v2](https://aws.amazon.com/cli/)、[Git for Windows](https://git-scm.com/download/win)、[Python 3.12](https://www.python.org/downloads/)

雙擊 **[`quick_deploy_aws\deploy.bat`](quick_deploy_aws/README.md)**，貼上 AWS 憑證，就會依序部署 3D 建模站與主站，完成後顯示網址。

不想用 AWS？雙擊 **[`quick_deploy_cloudflare\deploy.bat`](quick_deploy_cloudflare/README.md)**，主站的所有功能都部署到 Cloudflare 免費方案（Workers、D1、KV、Queues、Workers AI），約 30 秒完成（需要 Node.js 與 `npm install -g wrangler`；3D 建模站只有 AWS 版）。

> 在 PowerShell 打 `bash` 可能會叫到 WSL 的 bash（裡面沒有 Windows 版的 aws），請用 `.bat` 或 Git Bash 執行部署腳本。

不想用 AWS？主站有本機版：執行 `ZZZ_proxy_hub/setup.bat` 再執行 `start.bat`，詳見 [`ZZZ_proxy_hub/README.md`](ZZZ_proxy_hub/README.md)。

---

以下是 **3D 建模站**（本資料夾）的說明。它是最早的絕區零雲端版，所以也包含抽卡、評分、2.5D 等功能；這些功能在主站有三款遊戲的版本。

## 功能

| 功能 | 說明 | 使用的 AWS 服務 |
|---|---|---|
| 抽卡紀錄分析 | 貼上調頻紀錄網址自動匯入；統計保底進度、小保底歪率、歐非指數、出金圖表 | Lambda、DynamoDB |
| 截圖 OCR 匯入 | 上傳紀錄截圖，由 Textract 辨識成紀錄，可人工修正後匯入 | S3、Textract、Lambda |
| 全服統計 | 匿名彙整所有玩家的平均出金抽數、歪率 | DynamoDB |
| 驅動盤評分 | 依角色權重計算有效詞條數與評級，支援截圖自動填入 | Lambda、Textract |
| 2D → 2.5D 立繪 | Lambda 執行 AI 深度估計（Depth Anything V2），瀏覽器以 WebGL 做視差動態，可錄影下載、分享、公開畫廊 | S3、Lambda（Layer）、DynamoDB |
| 2D → 3D 模型 | EC2 上以 TripoSR 從單張圖片生成 3D 模型（GLB），網頁 360° 檢視、手機 AR、下載 | S3、SQS、EC2、DynamoDB |
| 角色與配隊 | 代理人資料庫篩選、玩家分享配隊與投票排行 | DynamoDB |
| HoYoLAB 自動簽到 | 每天 00:10 自動簽到，可用 Discord Webhook 通知結果 | EventBridge、Lambda |
| Discord Bot API | 讓 Bot 查詢抽卡統計、取得 AI 分析用摘要、觸發簽到 | API Gateway、Lambda |

## 架構

```
 使用者瀏覽器 ──▶ S3 靜態網站（HTML/CSS/JS）
      │
      ├──▶ API Gateway (HTTP API) ──▶ Lambda: zzz-hub-api ──▶ DynamoDB (zzz-hub)
      │                                   │ 非同步呼叫
      │                                   ▼
      │                              Lambda: zzz-hub-worker ──▶ 官方調頻紀錄 API
      │                                   ▲
      └── 預簽名網址直傳 ──▶ S3 uploads ──┘ (ObjectCreated 事件) ──▶ Textract
                                │
                                ├─ uploads/depth/ ──▶ Lambda: zzz-hub-depth（ONNX AI 模型）
                                │                        └─▶ S3 網站 gallery/（原圖 + 深度圖）
                                └─ uploads/model3d/ ──▶ SQS ──▶ EC2 r5.large（TripoSR）
                                                                  └─▶ S3 網站 gallery3d/（GLB）
 API 建立 3D 任務時 ──(ec2:StartInstances)──▶ 喚醒 EC2；worker 閒置 20 分鐘自動關機

 EventBridge（每日 cron）──▶ Lambda: zzz-hub-checkin ──▶ HoYoLAB ──▶ Discord Webhook
 Discord Bot ──(X-Bot-Key)──▶ API Gateway /api/bot/*
```

設計重點：
- **全 Serverless**：沒有 EC2，Lab 關閉時網站與排程仍持續運作，閒置成本約為 0
- **非同步工作**：匯入與 OCR 較慢，以 Job + 輪詢的方式避免 API Gateway 30 秒逾時
- **前端直傳 S3**：圖片不經過 Lambda，用預簽名網址上傳，並設定 1 天後自動刪除
- **DynamoDB 單表設計**：以 `PK`/`SK` 存放個人檔案、抽卡紀錄、工作、配隊、投票、簽到紀錄，TTL 自動清除過期資料

### 2.5D 立繪原理

1. 前端以預簽名網址把圖片傳到 S3 `uploads/depth/`
2. S3 事件觸發 `zzz-hub-depth`。模型（Depth Anything V2 Small，ONNX，約 95MB，Apache-2.0）在冷啟動時從 S3 下載到 `/tmp`，用 onnxruntime 在 CPU 上推論，約 2～5 秒
3. 輸出原圖（WebP，保留透明）與深度圖（白＝近、黑＝遠）到網站 bucket 的 `gallery/{id}/`
4. 瀏覽器用 WebGL fragment shader，依深度把每個像素往滑鼠方向位移，近的移得多、遠的移得少，形成視差立體感（`frontend/parallax.js`）
5. 用 `canvas.captureStream` + `MediaRecorder` 錄成 4 秒無縫循環的 WebM

為什麼不用 EC2/GPU：Learner Lab 限制 GPU 機型，Session 結束 EC2 也會停機；改用 Lambda + Layer（numpy/onnxruntime/Pillow），隨用隨付、永遠在線。

### 3D 模型生成

| 步驟 | 位置 | 說明 |
|---|---|---|
| 1. 上傳 | 瀏覽器 → S3 | 預簽名網址直傳 `uploads/model3d/` |
| 2. 排隊 | S3 → SQS | S3 事件通知直接送進 SQS 佇列（可見性逾時 15 分鐘） |
| 3. 喚醒 | API Lambda → EC2 | 建立任務時若主機停止就 `StartInstances`；查詢狀態時若佇列有任務也會自動喚醒 |
| 4. 去背 | EC2 | rembg `isnet-anime`（動漫專用），失敗時自動改用 `isnet-general-use` |
| 5. 生成 | EC2 | TripoSR（MIT 授權）從單張圖推測三平面（triplane）神經場，Marching Cubes 取出網格與頂點顏色 |
| 6. 後處理 | EC2 | 減面到 6 萬面、修正法線、轉成 Y 軸朝上，匯出 GLB |
| 7. 檢視 | 瀏覽器 | Google `<model-viewer>` 360° 旋轉，手機支援 AR |

- **為什麼用 EC2 而不是 Lambda**：PyTorch + 模型權重超過 Lambda 250MB 的 zip 上限，容器映像又需要 Docker/ECR
- **為什麼沒有 GPU**：Learner Lab 不允許 GPU 機型，CPU 機型最大到 `*.large`，所以選 16GB 記憶體的 r5.large；實測約 50 秒產生一個模型
- **成本控制**：worker 每 20 秒寫心跳到 DynamoDB，閒置 20 分鐘就 `shutdown`（instance-initiated-shutdown-behavior = stop），有任務時再由 API 喚醒；r5.large 每小時約 0.126 美元，只在使用時計費
- **為什麼用 SQS**：生成很慢，SQS 讓任務在主機開機期間安全排隊，worker 處理完才刪除訊息，主機當機時任務也不會遺失
- **除錯**：`bash deploy/ssm.sh "journalctl -u zzz3d -n 50"` 可以透過 SSM 直接在 EC2 上執行指令，不需要 SSH 或開放任何連接埠

### DynamoDB 資料模型

| PK | SK | 內容 |
|---|---|---|
| `PROFILE#{pid}` | `META` | 個人檔案（遊戲 UID、Discord ID） |
| `PROFILE#{pid}` | `PULL#{type}#{id}` | 單筆抽卡紀錄 |
| `PROFILE#{pid}` | `HOYOLAB` | 簽到 Cookie、Webhook、最近結果 |
| `PROFILE#{pid}` | `CHECKIN#{date}` | 簽到歷史（TTL 90 天） |
| `TOKEN#{sha256}` | `PROFILE` | 存取 Token → pid |
| `DISCORD#{id}` | `PROFILE` | Discord 使用者 → pid |
| `LINKCODE#{code}` | `PROFILE` | 綁定碼（TTL 10 分鐘） |
| `JOB#{id}` | `META` | 背景工作狀態（TTL 1 天） |
| `GLOBAL` | `P#{pid}` | 匿名統計摘要 |
| `TEAMS` / `TEAMVOTE#{tid}` | `TEAM#{tid}` / `{pid}` | 配隊與投票 |
| `CHECKIN_SET` | `{pid}` | 啟用自動簽到的名單 |
| `DEPTH#{id}` / `PROFILE#{pid}` | `META` / `DEPTH#{ts}#{id}` | 2.5D 作品 |
| `GALLERY` | `{ts}#{id}` | 公開畫廊索引 |
| `MODEL3D#{id}` / `PROFILE#{pid}` | `META` / `MODEL3D#{ts}#{id}` | 3D 模型作品 |
| `GALLERY3D` | `{ts}#{id}` | 3D 公開畫廊索引 |
| `WORKER` | `model3d` | EC2 worker 心跳 |

## 專案結構

```
ZZZ_proxy_hub/ 主站（三款遊戲；本機版與雲端版共用程式碼），說明見其 README
quick_deploy_aws/   一鍵部署到 AWS（3D 建模站 + 主站）
quick_deploy_cloudflare/  一鍵部署主站到 Cloudflare（不需要 AWS）
backend/    Lambda 程式（Python 3.12，只用標準函式庫 + boto3）
  api.py        API 路由（HTTP API 進入點）
  worker.py     背景工作：網址匯入、S3→Textract OCR、初始資料
  checkin.py    HoYoLAB 簽到（EventBridge 進入點）
  depth.py      2.5D 深度估計（S3 事件進入點，需要 Layer）
  gacha.py      抽卡抓取與統計
  ocr.py        Textract 結果解析
  disc.py       驅動盤評分
  gamedata.py   角色、卡池、詞條資料
frontend/   靜態網站（index.html / style.css / app.js / parallax.js / get_gacha_url.ps1）
worker3d/   EC2 3D worker（worker.py、首次開機安裝腳本 userdata.sh）
deploy/     deploy.sh 一鍵部署（含模型下載與 Layer 打包）、ssm.sh 遠端除錯
discord_cog/ discord.py Cog 模組（zzz_hub.py；三款遊戲版在 ZZZ_proxy_hub/discord_cog/hoyo_hub.py）
docs/       Discord Bot API 文件
```

## 單獨部署 3D 建模站

需要：AWS CLI v2、Python 3、Git Bash。（一般建議直接用上面的 `quick_deploy_aws`。）

1. Learner Lab 按 **Start Lab** → **AWS Details** → 複製 credentials
2. 寫入 profile：
   ```bash
   aws configure set aws_access_key_id ... --profile learnerlab
   aws configure set aws_secret_access_key ... --profile learnerlab
   aws configure set aws_session_token ... --profile learnerlab
   ```
3. 在專案根目錄執行 `bash deploy/deploy.sh`

腳本可以重複執行。改完程式碼後再跑一次就會更新 Lambda 與前端。網址會寫在 `deploy/outputs.env`。

## 部署環境：AWS Academy Learner Lab 的限制與對應

- **不能建立 IAM Role** → 所有 Lambda 使用 `LabRole`
- **CloudFront 無權限** → 前端改用 S3 靜態網站（HTTP）
- **Textract 不支援中文** → 截圖 OCR 需要遊戲切換英文；主要匯入方式改為官方紀錄網址
- **Session 4 小時** → 除 3D 外全部採用 Serverless 服務，不受 Lab 關閉影響；3D worker 被 Lab 停機後，下一個任務會自動喚醒它
- **不能開 GPU、CPU 機型上限 `*.large`** → 3D 生成使用 r5.large CPU 推論

## 安全性

| 項目 | 做法 |
|---|---|
| 身分驗證 | 匿名 Token（`secrets.token_urlsafe`），資料庫只存 SHA-256 雜湊；所有個人資料、工作、作品都檢查擁有者 |
| HoYoLAB Cookie | 只保留簽到需要的 `ltoken_v2` / `ltuid_v2` / `ltmid_v2`（`cookie_token_v2` 等權限較大的欄位直接丟掉）；只送往 HoYoLAB 官方網域；API 只回傳遮罩後的 ltuid，不回傳 Cookie |
| 抽卡網址 authkey | 只用來呼叫官方 API，不寫進資料庫或日誌；請求一律送往程式內建的官方網域，不會照使用者給的網址連線（避免 SSRF） |
| Discord | Webhook 只接受 `discord.com/api/webhooks/`；Bot API 用獨立金鑰並以常數時間比對 |
| 上傳 | S3 uploads bucket 封鎖公開存取；只能用 5 分鐘有效、綁定檔案類型的預簽名網址上傳 |
| 前端 | 使用者輸入一律跳脫後才插入頁面；CDN 腳本加 SRI 完整性檢查；主站設定 CSP，只允許連線到自己的 API 與 S3 |
| 濫用防護 | API Gateway 限流每秒 50 個請求；3D 任務佇列上限 10 個，避免 EC2 被灌任務一直開著 |
| 機密檔案 | `deploy/.secrets`（Bot API Key）、`outputs.env`、憑證檔都在 `.gitignore` 裡 |

已知限制：S3 靜態網站只有 HTTP（Learner Lab 不能用 CloudFront），API 本身是 HTTPS；Learner Lab 不能建立 IAM 角色，Lambda 只能用共用的 `LabRole`。正式上線建議改用 CloudFront（HTTPS）與最小權限的 IAM 角色。

## 注意事項

- 非官方粉絲工具，與 miHoYo / HoYoverse 無關
- 「歪」的判斷依 `gamedata.py` 的常駐名單；新增常駐角色或音擎時需更新
