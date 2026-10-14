"""各遊戲設定共用的小工具。"""


def make_agents(rows, elements, specialties, standard_ids=(), builds=None):
    """rows: (id, 繁中名, 英文名, 稀有度 S/A, 屬性, 特性, 陣營/地區)"""
    builds = builds or {}
    return [
        {
            "id": r[0], "name": r[1], "name_en": r[2], "rank": r[3],
            "element": r[4], "element_zh": elements[r[4]],
            "specialty": r[5], "specialty_zh": specialties[r[5]],
            "faction": r[6] if len(r) > 6 else "",
            "standard": r[0] in standard_ids or r[3] == "A",
            "build": builds.get(r[0]),
        }
        for r in rows
    ]


def name_index(agents, aliases):
    idx = {}
    for a in agents:
        for n in [a["name"], a["name_en"], *aliases.get(a["id"], [])]:
            idx[norm_name(n)] = a
    return idx


def norm_name(n):
    return (n or "").strip().lower().replace(" ", "").replace("・", "·").replace("•", "·")


# 各遊戲共用的詞條中文名稱
STAT_LABELS = {
    "crit_rate": "暴擊率", "crit_dmg": "暴擊傷害",
    "atk": "攻擊力", "atk_pct": "攻擊力%", "hp": "生命值", "hp_pct": "生命值%",
    "def": "防禦力", "def_pct": "防禦力%",
    "dmg_bonus": "屬性傷害加成", "heal": "治療加成",
    # 絕區零
    "pen": "穿透值", "pen_ratio": "穿透率", "ap": "異常精通", "am": "異常掌控", "impact": "衝擊力",
    "er": "能量回復",  # 各遊戲名稱不同，由遊戲設定覆寫
    # 原神
    "em": "元素精通",
    # 星穹鐵道
    "spd": "速度", "ehr": "效果命中", "res": "效果抵抗", "be": "擊破特攻",
}

# OCR 共用詞條別名（中英文；長的寫法要排在前面）
COMMON_ALIASES = [
    ("crit rate", "crit_rate"), ("crit dmg", "crit_dmg"),
    ("暴擊率", "crit_rate"), ("暴击率", "crit_rate"), ("暴擊傷害", "crit_dmg"), ("暴击伤害", "crit_dmg"),
    ("攻擊力", "atk"), ("攻击力", "atk"), ("生命值", "hp"), ("防禦力", "def"), ("防御力", "def"),
    ("治療加成", "heal"), ("治疗加成", "heal"), ("outgoing healing boost", "heal"), ("healing bonus", "heal"),
    ("atk", "atk"), ("hp", "hp"), ("def", "def"),
]
