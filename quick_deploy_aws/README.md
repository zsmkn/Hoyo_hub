# Quick Start：一鍵部署到 AWS Learner Lab

一次部署兩個網站：

| 網站 | 內容 | 原本的部署腳本 |
|---|---|---|
| **主站** | 絕區零／原神／星穹鐵道：抽卡分析、裝備評分、角色圖鑑、2.5D 立繪、HoYoLAB 自動簽到、Discord Bot API | `ZZZ_proxy_hub/deploy/deploy_aws.sh` |
| **3D 建模站** | 2D 圖片轉 3D 模型（EC2）、絕區零配隊推薦；主站絕區零分頁的「3D 建模 ↗」連到這裡 | `deploy/deploy.sh` |

## 需要先安裝

- [AWS CLI v2](https://aws.amazon.com/cli/)
- [Git for Windows](https://git-scm.com/download/win)（提供 Git Bash）
- [Python 3.12](https://www.python.org/downloads/)（安裝時勾選 **Add python.exe to PATH**）

## 三步驟

1. 到 Learner Lab 按 **Start Lab**，等左上角變綠燈
2. 按 **AWS Details** → AWS CLI 旁的 **Show**，把整段文字（`[default]` 開頭那三行）複製起來
3. 雙擊 **`quick_deploy_aws\deploy.bat`**，看到提示時貼上，按兩次 Enter

   也可以先把那段文字存成 `quick_deploy_aws\credentials.txt`（格式見 `credentials.example.txt`），
   腳本會自動匯入，匯入後刪除檔案。

完成後網址會顯示在畫面上，也會存在 `quick_deploy_aws\urls.txt`。

| | 第一次 | 之後重跑 |
|---|---|---|
| 所需時間 | 約 15～20 分鐘（建立虛擬環境、下載 AI 模型、建立 EC2） | 約 5 分鐘 |

## 腳本做了什麼

1. 檢查 AWS CLI、Python
2. 匯入憑證到 AWS CLI profile `learnerlab`，並確認是 Learner Lab 帳號（有 `LabRole`）
3. 第一次執行時建立 `ZZZ_proxy_hub\.venv` 並安裝套件（不會下載本機版用的 AI 模型）
4. 部署 3D 建模站：DynamoDB、S3、SQS、EC2 r5.large（閒置 20 分鐘自動關機）、Lambda、API Gateway、EventBridge
5. 部署主站：DynamoDB、S3、Lambda × 3、API Gateway（含限流）、EventBridge 每小時簽到排程

可以重複執行，已經存在的資源會更新而不是重建，資料不會被清掉。

## 選項

```bash
bash quick_deploy_aws/deploy_all.sh --hub-only   # 只部署主站，跳過 3D 建模站（省時間、也不會建立 EC2）
```

在 PowerShell 裡請用 `quick_deploy_aws\deploy.bat --hub-only`。

## 常見問題

**`aws: command not found`**
在 PowerShell 打 `bash` 會叫到 WSL 的 bash，裡面沒有 Windows 版的 AWS CLI。
請改用 `deploy.bat`，或在 PowerShell 執行：
```powershell
& "C:\Program Files\Git\bin\bash.exe" quick_deploy_aws/deploy_all.sh
```

**`憑證無效` / `ExpiredToken`**
Learner Lab 每次 Start Lab 都會換一組憑證，約 4 小時後失效。重新複製新的憑證再執行一次即可。

**換了一個新的 Learner Lab 帳號**
直接執行即可，所有資源都會在新帳號重新建立。網址會變（bucket 名稱含帳號 ID），
舊帳號的資料不會自動搬過來。
