"""HoYo Proxy Hub — discord.py Cog（絕區零 / 原神 / 崩壞：星穹鐵道）

放進你的 bot 的 cogs 資料夾，然後：
    await bot.load_extension("cogs.hoyo_hub")

環境變數：
    ZZZ_API_URL   本機版：http://127.0.0.1:8787
    ZZZ_BOT_KEY   data/secrets.json 裡的 BOT_API_KEY
    ZZZ_SITE_URL  （選填）網站網址，embed 標題會連過去
    HOYO_DEFAULT_GAME（選填）不指定遊戲時的預設值：zzz / gi / hsr，預設 zzz

AI 分析：bot 上若有 `async def zzz_ai_analyze(self, system_prompt, context) -> str` 會優先使用，
否則有安裝 anthropic 且設定 ANTHROPIC_API_KEY 就用 Claude。

需要套件：discord.py>=2.3、aiohttp
"""
import os
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

GAMES = {
    "zzz": {"name": "絕區零", "color": 0xC6F432, "S": "S 級"},
    "gi": {"name": "原神", "color": 0xE8C87A, "S": "5★"},
    "hsr": {"name": "崩壞：星穹鐵道", "color": 0x9DB8FF, "S": "5★"},
}
GAME_CHOICES = [app_commands.Choice(name=g["name"], value=k) for k, g in GAMES.items()]
RESULT_TAG = {"won": "✅", "lost": "💔歪", "guaranteed": "🔒大保底"}
CHECKIN_ICON = {"signed": "✅", "already": "☑️", "captcha": "🧩", "error": "⚠️"}

AI_SYSTEM_PROMPT = (
    "你是幽默又專業的米哈遊遊戲抽卡分析師。根據玩家資料，用繁體中文在 300 字內："
    "1) 評論他的運氣（可以適度吐槽）2) 解讀保底與歪的狀況 3) 給出下一步抽卡建議。"
    "不要捏造資料中沒有的數字。"
)


class HoyoApiError(Exception):
    pass


class HoyoHub(commands.Cog):
    """米哈遊三款遊戲：抽卡統計、AI 分析、HoYoLAB 簽到"""

    hoyo = app_commands.Group(name="hoyo", description="HoYo Proxy Hub：絕區零 / 原神 / 星穹鐵道")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.api = os.environ["ZZZ_API_URL"].rstrip("/")
        self.key = os.environ["ZZZ_BOT_KEY"]
        self.site = os.environ.get("ZZZ_SITE_URL", "")
        self.default_game = os.environ.get("HOYO_DEFAULT_GAME", "zzz")
        self.session: Optional[aiohttp.ClientSession] = None

    async def cog_load(self):
        self.session = aiohttp.ClientSession(headers={"X-Bot-Key": self.key},
                                             timeout=aiohttp.ClientTimeout(total=30))

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    # ---------------- helpers ----------------
    def gid(self, choice: Optional[app_commands.Choice[str]]) -> str:
        return choice.value if choice else self.default_game

    async def call(self, method: str, path: str, game: Optional[str] = None, **kw) -> dict:
        prefix = f"/api/{game}" if game else "/api"
        async with self.session.request(method, self.api + prefix + path, **kw) as r:
            data = await r.json(content_type=None)
            if r.status >= 400:
                raise HoyoApiError((data or {}).get("error", f"HTTP {r.status}"))
            return data

    def embed(self, game: str, title: str, desc: str = "") -> discord.Embed:
        e = discord.Embed(title=title, description=desc, color=GAMES[game]["color"])
        e.set_footer(text=f"HoYo Proxy Hub · {GAMES[game]['name']} · 非官方粉絲工具")
        if self.site:
            e.url = f"{self.site}?game={game}"
        return e

    async def cog_app_command_error(self, inter: discord.Interaction, error: app_commands.AppCommandError):
        err = getattr(error, "original", error)
        msg = f"⚠️ {err}" if isinstance(err, HoyoApiError) else "⚠️ 發生錯誤，請稍後再試"
        if not isinstance(err, HoyoApiError):
            print("[hoyo_hub]", repr(err))
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
        msg = await anthropic.AsyncAnthropic().messages.create(
            model=os.environ.get("ZZZ_AI_MODEL", "claude-sonnet-5-5"), max_tokens=800,
            system=AI_SYSTEM_PROMPT, messages=[{"role": "user", "content": context}],
        )
        return "".join(b.text for b in msg.content if b.type == "text")

    # ---------------- 綁定（三款遊戲共用） ----------------
    @hoyo.command(name="link", description="用網站上的綁定碼連結你的 HoYo Proxy Hub 資料")
    @app_commands.describe(code="網站「Discord / 設定」頁產生的 6 碼綁定碼")
    async def link(self, inter: discord.Interaction, code: str):
        await self.call("POST", "/bot/link", json={"code": code, "discord_id": str(inter.user.id)})
        await inter.response.send_message("✅ 綁定成功！三款遊戲都可以用 `/hoyo pity`、`/hoyo analyze` 了。", ephemeral=True)

    @hoyo.command(name="unlink", description="解除綁定")
    async def unlink(self, inter: discord.Interaction):
        await self.call("DELETE", f"/bot/users/{inter.user.id}/link")
        await inter.response.send_message("已解除綁定。", ephemeral=True)

    # ---------------- 抽卡 ----------------
    @hoyo.command(name="pity", description="查看各卡池目前墊了幾抽")
    @app_commands.choices(game=GAME_CHOICES)
    @app_commands.describe(game="遊戲", user="查看其他人（對方需已綁定）")
    async def pity(self, inter: discord.Interaction, game: Optional[app_commands.Choice[str]] = None,
                   user: Optional[discord.User] = None):
        g, target = self.gid(game), user or inter.user
        d = await self.call("GET", f"/bot/users/{target.id}/summary", g)
        e = self.embed(g, f"{target.display_name} 的保底進度")
        for c in d["stats"]["channels"].values():
            if not c["total"]:
                continue
            filled = round(c["current_pity"] / c["pity_max"] * 12)
            bar = "█" * filled + "░" * (12 - filled)
            extra = "\n🔒 下一個必定 UP" if c["guaranteed"] else ""
            e.add_field(name=c["label"], value=f"`{bar}` **{c['current_pity']}**/{c['pity_max']}{extra}", inline=False)
        if not e.fields:
            e.description = "還沒有抽卡資料，先到網站匯入吧！"
        await inter.response.send_message(embed=e)

    @hoyo.command(name="stats", description="某個卡池的詳細統計")
    @app_commands.choices(game=GAME_CHOICES)
    @app_commands.describe(game="遊戲", channel="卡池名稱（例如：獨家頻段、角色活動祈願），不填則為限定角色池")
    async def stats(self, inter: discord.Interaction, game: Optional[app_commands.Choice[str]] = None,
                    channel: Optional[str] = None, user: Optional[discord.User] = None):
        g, target = self.gid(game), user or inter.user
        d = await self.call("GET", f"/bot/users/{target.id}/summary", g)
        chans = list(d["stats"]["channels"].values())
        c = next((x for x in chans if channel and channel in x["label"]), chans[0])
        gl = d["global"]["channels"].get(c["gacha_type"], {})
        S = GAMES[g]["S"]
        e = self.embed(g, f"{target.display_name}｜{c['label']}", f"歐非評價：**{c['luck_label']}**")
        e.add_field(name="總抽數", value=f"{c['total']} 抽")
        e.add_field(name=f"{S} 數量", value=str(c["s_count"]))
        e.add_field(name="平均出金", value=f"{c['avg_s_pulls'] or '—'} 抽（全站 {gl.get('avg_s_pulls') or '—'}）")
        if c["lose_rate"] is not None:
            e.add_field(name="小保底歪率", value=f"{c['lose_rate']}%（{c['lost_5050']}/{c['won_5050'] + c['lost_5050']}）")
        e.add_field(name="目前已墊", value=f"{c['current_pity']}/{c['pity_max']}")
        hist = "\n".join(f"{s['name']} — **{s['pulls']}** 抽 {RESULT_TAG.get(s['result'], '')}" for s in c["s_history"][:10])
        e.add_field(name=f"近期 {S}", value=hist or "還沒有", inline=False)
        await inter.response.send_message(embed=e)

    @hoyo.command(name="analyze", description="讓 AI 分析你的抽卡運氣")
    @app_commands.choices(game=GAME_CHOICES)
    async def analyze(self, inter: discord.Interaction, game: Optional[app_commands.Choice[str]] = None):
        g = self.gid(game)
        await inter.response.defer(thinking=True)
        d = await self.call("GET", f"/bot/users/{inter.user.id}/summary", g)
        if not d["stats"]["total_pulls"]:
            return await inter.followup.send(f"還沒有{GAMES[g]['name']}的抽卡資料，先到網站匯入吧！")
        text = await self.ai_analyze(d["llm_context"])
        await inter.followup.send(embed=self.embed(g, f"🤖 {inter.user.display_name} 的抽卡運勢分析", text[:4000]))

    @hoyo.command(name="global", description="全站抽卡統計")
    @app_commands.choices(game=GAME_CHOICES)
    async def global_stats(self, inter: discord.Interaction, game: Optional[app_commands.Choice[str]] = None):
        g = self.gid(game)
        d = await self.call("GET", "/bot/global", g)
        e = self.embed(g, f"🌐 {GAMES[g]['name']} 全站統計", f"共 {d['players']} 位玩家的匿名資料")
        for c in d["channels"].values():
            if not c.get("s_count"):
                continue
            rate = f"\n歪率 {c['lose_rate']}%" if c.get("lose_rate") is not None else ""
            e.add_field(name=c["label"], value=f"平均 **{c['avg_s_pulls']}** 抽出金\n共 {c['total_pulls']} 抽{rate}")
        await inter.response.send_message(embed=e)

    # ---------------- HoYoLAB 簽到 ----------------
    @hoyo.command(name="checkin", description="立即在 HoYoLAB 簽到")
    @app_commands.choices(game=GAME_CHOICES)
    async def checkin(self, inter: discord.Interaction, game: Optional[app_commands.Choice[str]] = None):
        g = self.gid(game)
        await inter.response.defer(ephemeral=True, thinking=True)
        r = await self.call("POST", f"/bot/users/{inter.user.id}/checkin", g)
        msg = f"{GAMES[g]['name']}｜{CHECKIN_ICON.get(r['status'], '')} {r['message']}"
        if r.get("reward"):
            msg += f"\n🎁 {r['reward']}（本月第 {r['total_days']} 天）"
        await inter.followup.send(msg, ephemeral=True)

    @hoyo.command(name="checkin_status", description="查看自動簽到狀態")
    @app_commands.choices(game=GAME_CHOICES)
    async def checkin_status(self, inter: discord.Interaction, game: Optional[app_commands.Choice[str]] = None):
        g = self.gid(game)
        r = await self.call("GET", f"/bot/users/{inter.user.id}/checkin", g)
        if not r["configured"]:
            return await inter.response.send_message("尚未設定 HoYoLAB Cookie，請到網站設定。", ephemeral=True)
        lr = r.get("last_result") or {}
        msg = f"{GAMES[g]['name']} 自動簽到：**{'開啟' if r['enabled'] else '關閉'}**"
        if lr:
            msg += f"\n最近一次（{lr.get('date')}）：{CHECKIN_ICON.get(lr.get('status'), '')} {lr.get('message')}"
            if lr.get("reward"):
                msg += f"｜🎁 {lr['reward']}"
        await inter.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(HoyoHub(bot))
