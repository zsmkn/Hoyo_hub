"""ZZZ Proxy Hub — discord.py Cog

放進你的 bot 的 cogs 資料夾，然後：
    await bot.load_extension("cogs.zzz_hub")

需要的環境變數：
    ZZZ_API_URL   例：https://xxxx.execute-api.us-east-1.amazonaws.com
    ZZZ_BOT_KEY   deploy/.secrets 裡的 BOT_API_KEY
    ZZZ_SITE_URL  （選填）網站網址，用來在訊息裡附連結

AI 分析：
    預設若有安裝 anthropic 且設定 ANTHROPIC_API_KEY 就用 Claude；
    如果你的 bot 已經有自己的 AI 呼叫方式，在 bot 上提供
        async def zzz_ai_analyze(self, system_prompt: str, context: str) -> str
    Cog 會優先使用它。

需要套件：discord.py>=2.3、aiohttp
"""
import os
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

ACCENT = 0xC6F432
CHANNELS = {"2": "獨家頻段", "3": "音擎頻段", "1": "常駐頻段", "5": "邦布頻段"}
CHANNEL_CHOICES = [app_commands.Choice(name=v, value=k) for k, v in CHANNELS.items()]
RESULT_TAG = {"won": "✅", "lost": "💔歪", "guaranteed": "🔒大保底"}
CHECKIN_ICON = {"signed": "✅", "already": "☑️", "captcha": "🧩", "error": "⚠️"}

AI_SYSTEM_PROMPT = (
    "你是幽默又專業的《絕區零》抽卡分析師。根據玩家資料，用繁體中文在 300 字內："
    "1) 評論他的運氣（可以適度吐槽）2) 解讀保底與歪的狀況 3) 給出下一步抽卡建議。"
    "不要捏造資料中沒有的數字。"
)


class ZZZApiError(Exception):
    pass


class ZZZHub(commands.Cog):
    """絕區零工具站：抽卡統計、AI 分析、HoYoLAB 簽到"""

    zzz = app_commands.Group(name="zzz", description="絕區零工具站")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.api = os.environ["ZZZ_API_URL"].rstrip("/")
        self.key = os.environ["ZZZ_BOT_KEY"]
        self.site = os.environ.get("ZZZ_SITE_URL", "")
        self.session: Optional[aiohttp.ClientSession] = None

    async def cog_load(self):
        self.session = aiohttp.ClientSession(
            headers={"X-Bot-Key": self.key},
            timeout=aiohttp.ClientTimeout(total=30),
        )

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    # ---------------- helpers ----------------
    async def call(self, method: str, path: str, **kw) -> dict:
        async with self.session.request(method, self.api + path, **kw) as r:
            data = await r.json(content_type=None)
            if r.status >= 400:
                raise ZZZApiError((data or {}).get("error", f"HTTP {r.status}"))
            return data

    async def summary(self, user_id: int) -> dict:
        return await self.call("GET", f"/api/bot/users/{user_id}/summary")

    def embed(self, title: str, desc: str = "") -> discord.Embed:
        e = discord.Embed(title=title, description=desc, color=ACCENT)
        e.set_footer(text="ZZZ Proxy Hub · 非官方粉絲工具")
        if self.site:
            e.url = self.site
        return e

    async def cog_app_command_error(self, inter: discord.Interaction, error: app_commands.AppCommandError):
        err = getattr(error, "original", error)
        msg = f"⚠️ {err}" if isinstance(err, ZZZApiError) else "⚠️ 發生錯誤，請稍後再試"
        if not isinstance(err, ZZZApiError):
            print("[zzz_hub]", repr(err))
        if inter.response.is_done():
            await inter.followup.send(msg, ephemeral=True)
        else:
            await inter.response.send_message(msg, ephemeral=True)

    async def ai_analyze(self, context: str) -> str:
        custom = getattr(self.bot, "zzz_ai_analyze", None)
        if custom:
            return await custom(AI_SYSTEM_PROMPT, context)
        try:
            import anthropic
        except ImportError:
            return "（尚未設定 AI：請安裝 anthropic 或在 bot 上實作 zzz_ai_analyze）\n\n" + context
        client = anthropic.AsyncAnthropic()
        msg = await client.messages.create(
            model=os.environ.get("ZZZ_AI_MODEL", "claude-sonnet-5-5"),
            max_tokens=800,
            system=AI_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": context}],
        )
        return "".join(b.text for b in msg.content if b.type == "text")

    # ---------------- 綁定 ----------------
    @zzz.command(name="link", description="用網站上的綁定碼連結你的 ZZZ Proxy Hub 資料")
    @app_commands.describe(code="網站「Discord / 設定」頁產生的 6 碼綁定碼")
    async def link(self, inter: discord.Interaction, code: str):
        await self.call("POST", "/api/bot/link", json={"code": code, "discord_id": str(inter.user.id)})
        await inter.response.send_message("✅ 綁定成功！現在可以用 `/zzz pity`、`/zzz analyze` 了。", ephemeral=True)

    @zzz.command(name="unlink", description="解除綁定")
    async def unlink(self, inter: discord.Interaction):
        await self.call("DELETE", f"/api/bot/users/{inter.user.id}/link")
        await inter.response.send_message("已解除綁定。", ephemeral=True)

    # ---------------- 抽卡 ----------------
    @zzz.command(name="pity", description="查看各頻段目前墊了幾抽")
    @app_commands.describe(user="查看其他人（對方需已綁定）")
    async def pity(self, inter: discord.Interaction, user: Optional[discord.User] = None):
        target = user or inter.user
        d = await self.summary(target.id)
        e = self.embed(f"{target.display_name} 的保底進度")
        for gt, label in CHANNELS.items():
            c = d["stats"]["channels"].get(gt)
            if not c or not c["total"]:
                continue
            bar_len = 12
            filled = round(c["current_pity"] / c["pity_max"] * bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)
            extra = "\n🔒 下一隻必定 UP" if c["guaranteed"] else ""
            e.add_field(name=label, value=f"`{bar}` **{c['current_pity']}**/{c['pity_max']}{extra}", inline=False)
        if not e.fields:
            e.description = "還沒有抽卡資料，先到網站匯入吧！"
        await inter.response.send_message(embed=e)

    @zzz.command(name="stats", description="某個頻段的詳細統計")
    @app_commands.choices(channel=CHANNEL_CHOICES)
    async def stats(self, inter: discord.Interaction, channel: Optional[app_commands.Choice[str]] = None,
                    user: Optional[discord.User] = None):
        target = user or inter.user
        gt = channel.value if channel else "2"
        d = await self.summary(target.id)
        c = d["stats"]["channels"][gt]
        g = d["global"]["channels"].get(gt, {})
        e = self.embed(f"{target.display_name}｜{c['label']}", f"歐非評價：**{c['luck_label']}**")
        e.add_field(name="總抽數", value=f"{c['total']} 抽")
        e.add_field(name="S / A 級", value=f"{c['s_count']} / {c['a_count']}")
        e.add_field(name="平均出金", value=f"{c['avg_s_pulls'] or '—'} 抽（全服 {g.get('avg_s_pulls') or '—'}）")
        if c["lose_rate"] is not None:
            e.add_field(name="小保底歪率", value=f"{c['lose_rate']}%（{c['lost_5050']}/{c['won_5050'] + c['lost_5050']}）")
        e.add_field(name="目前已墊", value=f"{c['current_pity']}/{c['pity_max']}")
        hist = "\n".join(
            f"{s['name']} — **{s['pulls']}** 抽 {RESULT_TAG.get(s['result'], '')}" for s in c["s_history"][:10]
        )
        e.add_field(name="近期 S 級", value=hist or "還沒有", inline=False)
        await inter.response.send_message(embed=e)

    @zzz.command(name="analyze", description="讓 AI 分析你的抽卡運氣")
    async def analyze(self, inter: discord.Interaction):
        await inter.response.defer(thinking=True)
        d = await self.summary(inter.user.id)
        if not d["stats"]["total_pulls"]:
            return await inter.followup.send("還沒有抽卡資料，先到網站匯入吧！")
        text = await self.ai_analyze(d["llm_context"])
        e = self.embed(f"🤖 {inter.user.display_name} 的抽卡運勢分析", text[:4000])
        await inter.followup.send(embed=e)

    @zzz.command(name="global", description="全服抽卡統計")
    async def global_stats(self, inter: discord.Interaction):
        d = await self.call("GET", "/api/bot/global")
        e = self.embed("🌐 全服統計", f"共 {d['players']} 位玩家的匿名資料")
        for gt, label in CHANNELS.items():
            c = d["channels"].get(gt, {})
            if not c.get("s_count"):
                continue
            rate = f"\n歪率 {c['lose_rate']}%" if c.get("lose_rate") is not None else ""
            e.add_field(name=label, value=f"平均 **{c['avg_s_pulls']}** 抽出金\n共 {c['total_pulls']} 抽{rate}")
        await inter.response.send_message(embed=e)

    # ---------------- HoYoLAB 簽到 ----------------
    @zzz.command(name="checkin", description="立即在 HoYoLAB 簽到")
    async def checkin(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        r = await self.call("POST", f"/api/bot/users/{inter.user.id}/checkin")
        msg = f"{CHECKIN_ICON.get(r['status'], '')} {r['message']}"
        if r.get("reward"):
            msg += f"\n🎁 {r['reward']}（本月第 {r['total_days']} 天）"
        await inter.followup.send(msg, ephemeral=True)

    @zzz.command(name="checkin_status", description="查看自動簽到狀態")
    async def checkin_status(self, inter: discord.Interaction):
        r = await self.call("GET", f"/api/bot/users/{inter.user.id}/checkin")
        if not r["configured"]:
            return await inter.response.send_message("尚未設定 HoYoLAB Cookie，請到網站設定。", ephemeral=True)
        lr = r.get("last_result") or {}
        msg = f"自動簽到：**{'開啟' if r['enabled'] else '關閉'}**"
        if lr:
            msg += f"\n最近一次（{lr.get('date')}）：{CHECKIN_ICON.get(lr.get('status'), '')} {lr.get('message')}"
            if lr.get("reward"):
                msg += f"｜🎁 {lr['reward']}"
        await inter.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ZZZHub(bot))
