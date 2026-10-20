# Quick Deploy：一鍵部署到 Cloudflare（不需要 AWS）

把**主站**（絕區零／原神／崩壞：星穹鐵道）的所有功能部署到 Cloudflare 免費方案：
抽卡分析（官方網址匯入、截圖 OCR）、裝備評分、角色圖鑑、2.5D 立繪、HoYoLAB 自動簽到、Discord Bot API。

| 功能 | AWS 版 | Cloudflare 版 |
|---|---|---|
| 網頁 + API | S3 靜態網站（http）+ API Gateway + Lambda | **Workers**（https，網頁與 API 同網域） |
| 資料庫 | DynamoDB | **D1** |
| 作品圖片、上傳暫存 | S3 | **KV** |
| 背景工作（網址匯入、OCR） | worker Lambda | **Queues** |
| 每小時自動簽到 | EventBridge | **Cron Triggers** |
| 截圖 OCR | RapidOCR（Lambda） | **Workers AI** 視覺模型 |
| 2.5D 深度估計 | Lambda（ONNX） | **使用者的瀏覽器**（transformers.js，有 WebGPU 時 2～4 秒） |
| 限流 | API Gateway throttling | **Rate Limiting** |

3D 建模站需要 EC2 跑 TripoSR，Cloudflare 沒有對應的服務，只有 AWS 版（`quick_deploy_aws`）有。

## 需要先安裝

- [Node.js LTS](https://nodejs.org/)（`winget install OpenJS.NodeJS.LTS`）
- Cloudflare CLI：`npm install -g wrangler`
- [Git for Windows](https://git-scm.com/download/win)（提供 Git Bash）
- [Python 3](https://www.python.org/downloads/)（只用來匯出遊戲資料）
- 一個 Cloudflare 帳號（免費方案即可，不用綁信用卡）

## 步驟

1. 雙擊 **`quick_deploy_cloudflare\deploy.bat`**
2. 第一次執行會開瀏覽器登入 Cloudflare，按 **Allow**（之後不用再登入）

約 30 秒完成，網址會顯示在畫面上，也存在 `quick_deploy_cloudflare\urls.txt`
（`https://hoyo-hub.<你的子網域>.workers.dev`）。可以重複執行，資料不會被清掉。

## 腳本做了什麼

1. 檢查 Node.js、wrangler、Python，登入 Cloudflare
2. 建立 D1 資料庫 `hoyo-hub`、KV `hoyo-hub-files`、Queue `hoyo-hub-jobs`（已存在就沿用），建立資料表
3. 從 `ZZZ_proxy_hub/server/games/` 匯出遊戲資料；複製 `ZZZ_proxy_hub/frontend/` 並調整 CSP
4. 由 `ZZZ_proxy_hub/cloudflare/wrangler.template.toml` 產生設定檔，部署 Worker
5. 第一次執行時產生 Bot API Key 與上傳簽章金鑰（存在 `quick_deploy_cloudflare/.secrets`，已在 `.gitignore`），設定成 Worker secret

Discord Bot 用的 API 網址就是主站網址，Bot API Key 在 `.secrets` 的 `BOT_API_KEY`。

## 免費方案的額度

| 項目 | 免費額度 | 這個網站的用量 |
|---|---|---|
| Workers 請求 | 每天 10 萬次 | 一般使用遠低於此 |
| Workers AI | 每天 10,000 Neurons | 一張 OCR 截圖約數百 Neurons |
| KV 寫入 | 每天 1,000 次 | 一件 2.5D 作品 3 次、一張 OCR 截圖 2 次 |
| D1 | 5GB、每天 10 萬次寫入 | |

## 安全性

- 每日配額（同一 IP；IPv6 以 /64 計）：建立帳號 300、網址匯入 60、OCR 40、2.5D 30；
  全站每日上限 OCR 120（一張約 75 Neurons）、2.5D 200，避免有人用迴圈把免費額度（KV 寫入、Workers AI）一次用完。上限在 `cloudflare/src/routes.js` 的 `QUOTAS`
- HoYoLAB Cookie 只保留 `ltoken_v2 / ltuid_v2 / ltmid_v2`，並以 AES-GCM 加密後才存進 D1（金鑰只在 Worker secret）
- 上傳網址是 HMAC 簽章、5 分鐘有效、只能用一次，檔案類型不符回 403；圖片檔案檢查檔頭，只接受 PNG / JPEG / WebP
- JSON 請求上限 64KB、上傳一定要有 Content-Length 並限制大小；匯入最多 1,500 頁
- 網頁加上 CSP（只允許固定版本的 transformers.js）、`frame-ancestors`（防點擊劫持）、HSTS、nosniff、no-referrer
- 金鑰沒設定時直接拒絕服務（503），不使用預設值；記錄檔不寫入抽卡網址的 authkey

### 不想開瀏覽器登入（或有多個 Cloudflare 帳號）

建立 `quick_deploy_cloudflare\cloudflare.env`（已在 `.gitignore`）：

```
CLOUDFLARE_API_TOKEN=（Cloudflare 後台 → My Profile → API Tokens，用「Edit Cloudflare Workers」範本，再加上 D1、Workers AI 權限）
CLOUDFLARE_ACCOUNT_ID=（Cloudflare 後台右側的 Account ID）
```

## 常見問題

**`找不到 Cloudflare CLI`**：剛裝完 Node.js 要重開視窗，再執行 `npm install -g wrangler`。

**`找不到網址` / workers.dev 子網域**：第一次使用 Workers 的帳號要先到 Cloudflare 後台的 Workers & Pages 頁面開通子網域。

**剛部署完網址打不開**：新的 workers.dev 網址要 1～2 分鐘才會生效。

**2.5D 卡在「下載 AI 深度模型」**：第一次要從 Hugging Face 下載約 50MB，之後瀏覽器會快取。
