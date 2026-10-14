"""多遊戲設定註冊表。新增遊戲：寫一個設定檔並加進 ALL_GAMES。

環境變數（雲端部署用）：
  HUB_GAMES       這個站台提供哪些遊戲，例如 "gi,hsr"（預設全部）
  HUB_DEFAULT     預設遊戲（預設為第一個）
  HUB_EXTERNAL    JSON 陣列，選單中顯示、但連到其他網站的遊戲，例如 [{"id":"zzz","url":"http://..."}]
  HUB_LINKS       JSON 陣列，某款遊戲的分頁列多一個外部連結，例如 [{"game":"zzz","label":"3D 建模","url":"http://..."}]
"""
import json
import os

from ..common import ApiError
from .base import norm_name
from . import gi, hsr, zzz

ALL_GAMES = {"zzz": zzz.GAME, "gi": gi.GAME, "hsr": hsr.GAME}
_enabled = [g.strip() for g in os.environ.get("HUB_GAMES", ",".join(ALL_GAMES)).split(",") if g.strip() in ALL_GAMES]
GAMES = {k: ALL_GAMES[k] for k in _enabled}
DEFAULT_GAME = os.environ.get("HUB_DEFAULT") if os.environ.get("HUB_DEFAULT") in GAMES else next(iter(GAMES))


def external_games():
    out = []
    for e in json.loads(os.environ.get("HUB_EXTERNAL") or "[]"):
        g = ALL_GAMES.get(e.get("id"))
        if g and e.get("url") and e["id"] not in GAMES:
            out.append({"id": g["id"], "short": g["short"], "name": g["name"], "accent": g["accent"], "url": e["url"]})
    return out


def extra_links():
    return [{"game": e["game"], "label": e["label"], "url": e["url"]}
            for e in json.loads(os.environ.get("HUB_LINKS") or "[]")
            if e.get("game") in GAMES and e.get("label") and e.get("url")]


def get_game(key):
    if key not in GAMES:
        raise ApiError(404, f"不支援的遊戲：{key}")
    return GAMES[key]


def agent_by_name(game, name):
    return game["name_index"].get(norm_name(name))


def agent_by_id(game, cid):
    return next((a for a in game["agents"] if a["id"] == cid), None)


def is_standard(game, channel, name):
    return (name or "").strip() in game["gacha"]["standard"].get(channel, set())


def public_meta(game):
    """前端切換遊戲時需要的所有設定（不含伺服器內部用的欄位）。"""
    d = game["disc"]
    return {
        "id": game["id"], "short": game["short"], "name": game["name"], "name_en": game["name_en"],
        "accent": game["accent"], "accent_ink": game["accent_ink"], "labels": game["labels"],
        "agents": game["agents"], "elements": game["elements"], "specialties": game["specialties"],
        "channels": game["gacha"]["channels"], "featured_channel": game["gacha"]["featured_channel"],
        # JS 物件會把數字 key 重新排序（原神 100 會跑到 301 前面），所以另外給有順序的列表
        "channel_order": list(game["gacha"]["channels"]),
        "stat_labels": game["stat_labels"],
        "disc": {"item": d["item"], "slots": d["slots"], "default_slot": d["default_slot"],
                 "sub_stats": list(d["sub_roll"])},
    }
