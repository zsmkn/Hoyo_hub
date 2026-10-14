# HoYo Proxy Hub｜本機版（絕區零 / 原神 / 崩壞：星穹鐵道）

不需要 AWS 帳號、也不需要顯示卡的二遊玩家工具站。點左上角的遊戲圖示（ZZZ / GI / HSR）就能切換遊戲。

## 快速開始

需要：Windows、Python 3.12

1. 執行 `setup.bat`：建立虛擬環境、安裝套件、下載 AI 模型（約 100MB，只需要一次）
2. 執行 `start.bat`：啟動伺服器並開啟瀏覽器 → http://127.0.0.1:8787

資料存在 `data/`（資料庫、作品），AI 模型存在 `models/`，刪掉整個資料夾就能完全移除。

## 功能（三款遊戲都有）

| 功能 | 絕區零 | 原神 | 星穹鐵道 |
|---|---|---|---|
| 抽卡分析（貼網址匯入 / 截圖 OCR） | 調頻：獨家、音擎、常駐、邦布 | 祈願：角色活動（含活動祈願-2）、武器、集錄、常駐、新手 | 躍遷：角色活動、光錐活動、常駐、新手 |
| 保底進度、小保底歪率、歐非指數、全站統計 | ✔ | ✔ | ✔ |
| 裝備評分（截圖自動填入） | 驅動盤（6 個位置） | 聖遺物（花／羽／沙／杯／冠） | 遺器（頭／手／軀幹／腳／位面球／連結繩） |
| 角色圖鑑（屬性／特性篩選、搜尋） | 代理人 36 位 | 角色 95 位 | 角色 67 位 |
| HoYoLAB 每日自動簽到 | ✔ | ✔ | ✔ |
| Discord Bot API / Cog | ✔ | ✔ | ✔ |
| 2.5D 立繪（Depth Anything V2） | 三款共用作品庫 | | |

- HoYoLAB Cookie 三款遊戲**共用一組**，每款遊戲的自動簽到可以分別開關
- 伺服器開著時每天 00:10（台灣時間）自動簽到；那時電腦沒開的話，**下次啟動會自動補簽**
- OCR 使用 RapidOCR，支援**繁中 / 簡中 / 英文**介面；繁體字常被認錯（擊→撃、祈願→祈），已加上模糊比對

## 雲端版（AWS）

同一份程式碼也能部署到 AWS（雲端版提供**絕區零、原神、星穹鐵道**三款；絕區零的分頁列多一個「3D 建模 ↗」，連到保留 3D 建模的舊絕區零雲端站）：

```bash
cd ZZZ_proxy_hub
bash deploy/deploy_aws.sh        # 預設使用 AWS CLI profile「learnerlab」
```

| 本機 | 雲端 | 差異集中在 |
|---|---|---|
| FastAPI（`server/app.py`） | API Gateway + Lambda（`server/lambda_api.py`） | 兩者都呼叫 `server/routes.py` |
| SQLite | DynamoDB（`server/store_dynamo.py`） | `server/store.py` 依環境自動選擇 |
| 執行緒池 / PUT 上傳 / `data/gallery` | worker Lambda / S3 預簽名網址 / 網站 bucket | `server/runtime.py` |
| 背景排程執行緒 | EventBridge 每小時（`server/lambda_checkin.py`） | |

- OCR 在雲端一樣用 RapidOCR（支援中文）。它需要的 numpy、onnxruntime、OpenCV 合計約 330MB，超過 Lambda 250MB 的上限，
  所以打包成 zip 放在 S3，worker 冷啟動時下載到 `/tmp`（只有冷啟動多幾秒）
- 部署結果寫在 `deploy/outputs.env`；Bot API Key 在 `deploy/.secrets`（雲端版和本機版是不同的 Key）
- 沒有遊戲前綴的 API 預設為絕區零
- API Gateway 限流：全站每秒 50 個請求（瞬間 100）；`GET /api/ping` 限每秒 2 個，可用來測試限流（429）
- 環境變數 `HUB_GAMES`（提供哪些遊戲）、`HUB_LINKS`（某款遊戲分頁列的外部連結）由部署腳本設定

## 新增遊戲或角色

每款遊戲的設定都在 `server/games/{zzz,gi,hsr}.py`：角色列表、卡池規則（保底、常駐池名單）、官方 API 網址、
簽到活動、裝備評分權重、OCR 關鍵字。新角色推出時在 `_A` 加一行即可；新遊戲則新增一個設定檔並加進 `server/games/__init__.py`。

## API

遊戲相關的 API 都可以加上遊戲前綴 `/api/{zzz|gi|hsr}/...`，沒有前綴時預設為絕區零（相容舊版）。
完整列表見網站「Discord / 設定」頁或 http://127.0.0.1:8787/api-docs 。

## 設定（環境變數）

| 變數 | 預設 | 說明 |
|---|---|---|
| `ZZZ_PORT` | 8787 | 網站埠號 |
| `ZZZ_HOST` | 127.0.0.1 | 改成 `0.0.0.0` 可讓區網其他裝置連線 |
| `ZZZ_BOT_KEY` | 自動產生 | Discord Bot API Key，預設存在 `data/secrets.json` |

## 專案結構

```
server/
  app.py            FastAPI：所有 API 路由 + 靜態檔案 + 取網址 PowerShell 腳本
  games/            各遊戲設定（zzz.py / gi.py / hsr.py）與共用工具
  gacha.py          抽卡抓取與統計（三款通用）
  scoring.py        裝備評分（三款通用）
  ocr.py            RapidOCR + 抽卡紀錄 / 裝備截圖解析（中英文、模糊比對）
  checkin.py        HoYoLAB 簽到與排程
  depth.py          2.5D 深度估計
  store.py          SQLite PK/SK 儲存
frontend/           網頁
discord_cog/        discord.py Cog（hoyo_hub.py，/hoyo 指令群組）
```

## 已知限制

- 角色資料、常駐池名單是人工整理的，新版本推出時需要手動補上
- 抽卡匯入與 HoYoLAB 簽到的官方 API 只用假憑證驗證過「網址與參數正確」，還沒用真實帳號完整跑過
- 2D→3D 模型功能已移除：輕量模型（TripoSR、Hunyuan3D-2mini）在一般顯示卡上的效果不夠理想

非官方粉絲工具，與 miHoYo / HoYoverse 無關。Depth Anything V2 Small 為 Apache-2.0 授權。
