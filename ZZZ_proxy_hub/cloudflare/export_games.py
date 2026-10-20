"""把 server/games 的遊戲設定匯出成 JSON，給 Cloudflare Worker 使用（資料只維護 Python 這一份）。
用法：在 ZZZ_proxy_hub 目錄執行  python cloudflare/export_games.py 輸出檔.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.games.base import COMMON_ALIASES  # noqa: E402  只 import games，不會碰到資料庫與 AI 套件
from server.games import ALL_GAMES  # noqa: E402


def plain(o):
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    raise TypeError(type(o))


out = {}
for gid, g in ALL_GAMES.items():
    g = dict(g)
    g["name_index"] = {k: a["id"] for k, a in g["name_index"].items()}  # 名稱 → 角色 id（避免重複整份角色資料）
    # JS 物件會把數字 key 重新排序（原神 100 會跑到 301 前面），另外存一份有順序的卡池列表
    g["gacha"] = {**g["gacha"], "channel_order": list(g["gacha"]["channels"])}
    out[gid] = g
Path(sys.argv[1]).write_text(json.dumps({"games": out, "common_aliases": COMMON_ALIASES},
                                        ensure_ascii=False, default=plain), encoding="utf-8")
