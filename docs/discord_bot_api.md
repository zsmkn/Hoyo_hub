# Discord Bot API

給 Discord Bot 使用的後端 API。Bot 自己負責 Discord 指令與 AI 模型呼叫；本站提供資料。

- **Base URL**：見 `deploy/outputs.env` 的 `API_URL`
- **驗證**：每個請求帶 Header `X-Bot-Key: <BOT_API_KEY>`（在 `deploy/.secrets`，只放在 Bot 的伺服器端環境變數，不要寫進前端或公開 repo）
- 回應皆為 JSON；錯誤格式 `{"error": "訊息"}`

## 綁定流程

1. 使用者在網站「Discord / 設定」頁按「產生綁定碼」，得到 6 碼（10 分鐘有效）
2. 使用者在 Discord 輸入 `/link ABC123`
3. Bot 呼叫 `POST /api/bot/link`，之後即可用 Discord user id 查詢

## 端點

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/api/bot/link` | body `{"code": "ABC123", "discord_id": "123..."}` |
| DELETE | `/api/bot/users/{discord_id}/link` | 解除綁定 |
| GET | `/api/bot/users/{discord_id}/summary` | 抽卡統計、全服統計、`llm_context` |
| GET | `/api/bot/users/{discord_id}/pulls` | 明細；query：`gacha_type`(1/2/3/5)、`rank`(S/A/B)、`limit`(≤500) |
| GET | `/api/bot/users/{discord_id}/checkin` | HoYoLAB 簽到設定與最近結果 |
| POST | `/api/bot/users/{discord_id}/checkin` | 立即簽到 |
| GET | `/api/bot/global` | 全服統計 |

`gacha_type`：`1` 常駐頻段、`2` 獨家頻段、`3` 音擎頻段、`5` 邦布頻段。

### summary 回應重點

```json
{
  "stats": {
    "total_pulls": 812,
    "channels": {
      "2": {"label": "獨家頻段", "current_pity": 37, "pity_max": 90, "guaranteed": true,
            "avg_s_pulls": 58.3, "lose_rate": 40.0, "luck_label": "普通人",
            "s_history": [{"name": "雅", "pulls": 74, "result": "guaranteed"}]}
    }
  },
  "global": {"players": 12, "channels": {"2": {"avg_s_pulls": 61.2}}},
  "llm_context": "玩家總抽數 812 抽…【獨家頻段】共 420 抽，S級 7 個…"
}
```

`llm_context` 是整理好的中文純文字，可以直接塞進 AI 模型的 prompt。

## 範例 Bot（discord.py + Claude API）

```python
import os, aiohttp, discord
from discord import app_commands
import anthropic

API = os.environ["ZZZ_API_URL"]
HEADERS = {"X-Bot-Key": os.environ["ZZZ_BOT_KEY"]}
claude = anthropic.AsyncAnthropic()

bot = discord.Client(intents=discord.Intents.default())
tree = app_commands.CommandTree(bot)

async def call(method, path, **kw):
    async with aiohttp.ClientSession(headers=HEADERS) as s:
        async with s.request(method, API + path, **kw) as r:
            return r.status, await r.json()

@tree.command(name="link", description="綁定 ZZZ Proxy Hub")
async def link(inter: discord.Interaction, code: str):
    st, d = await call("POST", "/api/bot/link", json={"code": code, "discord_id": str(inter.user.id)})
    await inter.response.send_message("綁定成功！" if st == 200 else d["error"], ephemeral=True)

@tree.command(name="pity", description="查看目前墊了幾抽")
async def pity(inter: discord.Interaction):
    st, d = await call("GET", f"/api/bot/users/{inter.user.id}/summary")
    if st != 200:
        return await inter.response.send_message(d["error"], ephemeral=True)
    c = d["stats"]["channels"]["2"]
    await inter.response.send_message(
        f"獨家頻段已墊 **{c['current_pity']}/90**" + ("，下一隻必定 UP！" if c["guaranteed"] else ""))

@tree.command(name="analyze", description="讓 AI 分析你的抽卡運氣")
async def analyze(inter: discord.Interaction):
    await inter.response.defer()
    st, d = await call("GET", f"/api/bot/users/{inter.user.id}/summary")
    if st != 200:
        return await inter.followup.send(d["error"])
    msg = await claude.messages.create(
        model="claude-sonnet-5-5", max_tokens=600,
        system="你是幽默的絕區零抽卡分析師，用繁體中文、300 字內評論玩家運氣並給下一步抽卡建議。",
        messages=[{"role": "user", "content": d["llm_context"]}],
    )
    await inter.followup.send(msg.content[0].text)

@tree.command(name="checkin", description="立即 HoYoLAB 簽到")
async def checkin(inter: discord.Interaction):
    st, d = await call("POST", f"/api/bot/users/{inter.user.id}/checkin")
    await inter.response.send_message(d.get("message") or d.get("error"), ephemeral=True)

@bot.event
async def on_ready():
    await tree.sync()

bot.run(os.environ["DISCORD_TOKEN"])
```
