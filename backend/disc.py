"""驅動盤評分：以「有效詞條數」計分。"""
from common import ApiError
from gamedata import (
    SUB_ROLL, MAX_ROLLS, STAT_LABELS, RECOMMENDED_MAIN, AGENTS_BY_ID, weights_for,
)


def grade(eff):
    for th, g in ((7, "SSS"), (6, "SS"), (5, "S"), (4, "A"), (3, "B")):
        if eff >= th:
            return g
    return "C"


def score_disc(character=None, role=None, slot=None, main=None, subs=None):
    subs = subs or []
    if len(subs) > 4:
        raise ApiError(400, "副詞條最多 4 條")
    agent = AGENTS_BY_ID.get(character)
    if agent:
        role = agent["specialty"]
    weights = weights_for(character, role)

    rows, eff_total = [], 0.0
    for s in subs:
        stat, value = s.get("stat"), float(s.get("value") or 0)
        if stat not in SUB_ROLL:
            raise ApiError(400, f"不支援的副詞條：{stat}")
        rolls = value / SUB_ROLL[stat]
        w = weights.get(stat, 0)
        eff = rolls * w
        eff_total += eff
        rows.append({
            "stat": stat, "label": STAT_LABELS[stat], "value": value,
            "rolls": round(rolls, 2), "weight": w, "effective": round(eff, 2),
        })

    advice = []
    try:
        slot = int(slot) if slot else None
    except (TypeError, ValueError):
        slot = None
    main_stat = (main or {}).get("stat")
    rec = RECOMMENDED_MAIN.get(role or "attack", {}).get(slot)
    if rec and main_stat:
        if main_stat in rec:
            advice.append(f"{slot} 號位主詞條「{STAT_LABELS.get(main_stat, main_stat)}」✔ 符合推薦")
        else:
            names = "、".join(STAT_LABELS[r] for r in rec)
            advice.append(f"{slot} 號位建議主詞條：{names}（目前是 {STAT_LABELS.get(main_stat, main_stat)}）")
    useless = [r["label"] for r in rows if r["weight"] == 0]
    if useless:
        advice.append("無效副詞條：" + "、".join(useless))

    eff_total = round(eff_total, 2)
    return {
        "character": agent["name"] if agent else None,
        "role": role,
        "slot": slot,
        "main": main,
        "subs": rows,
        "effective_rolls": eff_total,
        "score": round(min(eff_total / MAX_ROLLS, 1) * 100, 1),
        "grade": grade(eff_total),
        "weights": {STAT_LABELS[k]: v for k, v in weights.items()},
        "advice": advice,
    }
