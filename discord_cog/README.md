# zzz_hub Cog

把 `zzz_hub.py` 複製到你 bot 的 cogs 資料夾。

## 安裝

```bash
pip install "discord.py>=2.3" aiohttp
pip install anthropic   # 選用：AI 分析
```

環境變數：

| 變數 | 說明 |
|---|---|
| `ZZZ_API_URL` | `deploy/outputs.env` 的 `API_URL` |
| `ZZZ_BOT_KEY` | `deploy/.secrets` 的 `BOT_API_KEY` |
| `ZZZ_SITE_URL` | （選填）網站網址，embed 標題會連過去 |
| `ANTHROPIC_API_KEY` | （選填）用 Claude 做 `/zzz analyze` |
| `ZZZ_AI_MODEL` | （選填）預設 `claude-sonnet-5-5` |

載入：

```python
await bot.load_extension("cogs.zzz_hub")
await bot.tree.sync()   # 第一次載入需要同步斜線指令
```

## 指令（都在 `/zzz` 群組底下）

| 指令 | 說明 |
|---|---|
| `/zzz link <code>` | 用網站產生的綁定碼綁定 |
| `/zzz unlink` | 解除綁定 |
| `/zzz pity [user]` | 各頻段保底進度條 |
| `/zzz stats [channel] [user]` | 單一頻段詳細統計與近期 S 級 |
| `/zzz analyze` | AI 抽卡運勢分析 |
| `/zzz global` | 全服統計 |
| `/zzz checkin` | 立即 HoYoLAB 簽到 |
| `/zzz checkin_status` | 自動簽到狀態 |

## 使用你 bot 既有的 AI

如果你的 bot 已經串好 AI，在 bot 物件上加這個方法，Cog 會優先使用它：

```python
class MyBot(commands.Bot):
    async def zzz_ai_analyze(self, system_prompt: str, context: str) -> str:
        return await self.my_llm.chat(system=system_prompt, user=context)
```
