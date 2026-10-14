"""裝備評分（驅動盤 / 聖遺物 / 遺器）：以「有效詞條數」計分。"""
from .common import ApiError
from .games import agent_by_id


def grade(eff):
    for th, g in ((7, "SSS"), (6, "SS"), (5, "S"), (4, "A"), (3, "B")):
        if eff >= th:
            return g
    return "C"


def build_of(game, agent):
    d = game["disc"]
    if agent and agent.get("build"):
        return agent["build"]
    if agent and agent["specialty"] in d.get("specialty_build", {}):
        return d["specialty_build"][agent["specialty"]]
    if agent and agent["specialty"] in d["builds"]:
        return agent["specialty"]
    return d.get("default_build") or next(iter(d["builds"]))


def score(game, character=None, slot=None, main=None, subs=None):
    d, labels = game["disc"], game["stat_labels"]
    subs = subs or []
    if len(subs) > 4:
        raise ApiError(400, "副詞條最多 4 條")
    agent = agent_by_id(game, character)
    build = build_of(game, agent)
    weights = d["char_weights"].get(character) or d["builds"][build]["weights"]

    rows, eff_total = [], 0.0
    for s in subs:
        stat, value = s.get("stat"), float(s.get("value") or 0)
        if stat not in d["sub_roll"]:
            raise ApiError(400, f"不支援的副詞條：{stat}")
        rolls = value / d["sub_roll"][stat]
        w = weights.get(stat, 0)
        eff_total += rolls * w
        rows.append({"stat": stat, "label": labels[stat], "value": value,
                     "rolls": round(rolls, 2), "weight": w, "effective": round(rolls * w, 2)})

    try:
        slot = int(slot) if slot else None
    except (TypeError, ValueError):
        slot = None
    slot_label = next((s["label"] for s in d["slots"] if s["id"] == slot), f"{slot} 號位")
    advice = []
    main_stat = (main or {}).get("stat")
    rec = d["builds"][build]["mains"].get(slot)
    if rec and main_stat:
        if main_stat in rec:
            advice.append(f"{slot_label}主詞條「{labels.get(main_stat, main_stat)}」✔ 符合推薦")
        else:
            advice.append(f"{slot_label}建議主詞條：{'、'.join(labels[r] for r in rec)}"
                          f"（目前是 {labels.get(main_stat, main_stat)}）")
    useless = [r["label"] for r in rows if r["weight"] == 0]
    if useless:
        advice.append("無效副詞條：" + "、".join(useless))

    eff_total = round(eff_total, 2)
    return {
        "character": agent["name"] if agent else None, "build": build, "slot": slot, "main": main,
        "subs": rows, "effective_rolls": eff_total,
        "score": round(min(eff_total / d["max_rolls"], 1) * 100, 1), "grade": grade(eff_total),
        "weights": {labels[k]: v for k, v in weights.items()}, "advice": advice,
    }
