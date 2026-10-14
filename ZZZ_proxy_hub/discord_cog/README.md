# hoyo_hub Cog（絕區零 / 原神 / 崩壞：星穹鐵道）

把 `hoyo_hub.py` 複製到你 bot 的 cogs 資料夾。

## 安裝

```bash
pip install "discord.py>=2.3" aiohttp
pip install anthropic   # 選用：AI 分析
```

| 環境變數 | 說明 |
|---|---|
| `ZZZ_API_URL` | 本機版：`http://127.0.0.1:8787` |
| `ZZZ_BOT_KEY` | `data/secrets.json` 的 `BOT_API_KEY` |
| `ZZZ_SITE_URL` | （選填）網站網址，embed 標題會連過去 |
| `HOYO_DEFAULT_GAME` | （選填）沒選遊戲時的預設值：`zzz` / `gi` / `hsr`，預設 `zzz` |
| `ANTHROPIC_API_KEY` | （選填）用 Claude 做 `/hoyo analyze` |

```python
await bot.load_extension("cogs.hoyo_hub")
await bot.tree.sync()   # 第一次載入需要同步斜線指令
```

## 指令（`/hoyo` 群組，大部分都有 `game` 選項）

| 指令 | 說明 |
|---|---|
| `/hoyo link <code>` | 用網站產生的綁定碼綁定（三款遊戲共用） |
| `/hoyo unlink` | 解除綁定 |
| `/hoyo pity [game] [user]` | 各卡池保底進度條 |
| `/hoyo stats [game] [channel] [user]` | 單一卡池詳細統計與近期 5★ / S 級 |
| `/hoyo analyze [game]` | AI 抽卡運勢分析 |
| `/hoyo global [game]` | 全站統計 |
| `/hoyo checkin [game]` | 立即 HoYoLAB 簽到 |
| `/hoyo checkin_status [game]` | 自動簽到狀態 |

## 使用你 bot 既有的 AI

```python
class MyBot(commands.Bot):
    async def zzz_ai_analyze(self, system_prompt: str, context: str) -> str:
        return await self.my_llm.chat(system=system_prompt, user=context)
```
