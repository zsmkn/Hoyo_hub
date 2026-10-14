"""絕區零靜態遊戲資料：代理人、卡池規則、驅動盤數值。

※ 非官方整理，新角色推出時請直接在 AGENTS 補上。
"""

# 調頻頻段（real_gacha_type）
GACHA_TYPES = {
    "1": "常駐頻段",
    "2": "獨家頻段",
    "3": "音擎頻段",
    "5": "邦布頻段",
}
PITY_MAX = {"1": 90, "2": 90, "3": 80, "5": 80}
# 含軟保底的綜合期望出金抽數，用來算歐非指數
EXPECTED_S_PULLS = {"1": 62.5, "2": 62.5, "3": 50.0, "5": 50.0}

ELEMENTS = {"ice": "冰", "fire": "火", "electric": "電", "physical": "物理", "ether": "以太"}
SPECIALTIES = {
    "attack": "強攻", "stun": "擊破", "anomaly": "異常",
    "support": "支援", "defense": "防護", "rupture": "命破",
}

# id, 繁中名, 英文名, 稀有度, 屬性, 特性, 陣營, 是否常駐
_A = [
    ("ellen", "艾蓮", "Ellen", "S", "ice", "attack", "維多利亞家政", False),
    ("zhuyuan", "朱鳶", "Zhu Yuan", "S", "ether", "attack", "刑偵特勤組", False),
    ("jane", "簡", "Jane", "S", "physical", "anomaly", "刑偵特勤組", False),
    ("qingyi", "青衣", "Qingyi", "S", "electric", "stun", "刑偵特勤組", False),
    ("caesar", "凱撒", "Caesar", "S", "physical", "defense", "卡呂冬之子", False),
    ("burnice", "柏妮思", "Burnice", "S", "fire", "anomaly", "卡呂冬之子", False),
    ("yanagi", "柳", "Yanagi", "S", "electric", "anomaly", "對空洞特別行動部第六課", False),
    ("lighter", "萊特", "Lighter", "S", "fire", "stun", "卡呂冬之子", False),
    ("miyabi", "雅", "Miyabi", "S", "ice", "anomaly", "對空洞特別行動部第六課", False),
    ("harumasa", "悠真", "Harumasa", "S", "electric", "attack", "對空洞特別行動部第六課", False),
    ("astra", "耀嘉音", "Astra Yao", "S", "ether", "support", "天琴座", False),
    ("evelyn", "伊芙琳", "Evelyn", "S", "fire", "attack", "天琴座", False),
    ("trigger", "扳機", "Trigger", "S", "electric", "stun", "奧波勒斯小隊", False),
    ("vivian", "薇薇安", "Vivian", "S", "ether", "anomaly", "反舌鳥", False),
    ("hugo", "雨果", "Hugo", "S", "ice", "attack", "反舌鳥", False),
    ("yixuan", "儀玄", "Yixuan", "S", "ether", "rupture", "雲巋山", False),
    ("jufufu", "橘福福", "Ju Fufu", "S", "fire", "stun", "雲巋山", False),
    ("yuzuha", "柚葉", "Yuzuha", "S", "physical", "support", "怪啖屋", False),
    ("alice", "愛麗絲", "Alice", "S", "physical", "anomaly", "怪啖屋", False),
    # 常駐 S
    ("grace", "格莉絲", "Grace", "S", "electric", "anomaly", "白祇重工", True),
    ("rina", "麗娜", "Rina", "S", "electric", "support", "維多利亞家政", True),
    ("koleda", "珂蕾妲", "Koleda", "S", "fire", "stun", "白祇重工", True),
    ("nekomata", "貓又", "Nekomata", "S", "physical", "attack", "狡兔屋", True),
    ("soldier11", "「11號」", "Soldier 11", "S", "fire", "attack", "奧波勒斯小隊", True),
    ("lycaon", "萊卡恩", "Lycaon", "S", "ice", "stun", "維多利亞家政", True),
    # A 級
    ("anby", "安比", "Anby", "A", "electric", "stun", "狡兔屋", True),
    ("billy", "比利", "Billy", "A", "physical", "attack", "狡兔屋", True),
    ("nicole", "妮可", "Nicole", "A", "ether", "support", "狡兔屋", True),
    ("corin", "可琳", "Corin", "A", "physical", "attack", "維多利亞家政", True),
    ("anton", "安東", "Anton", "A", "electric", "attack", "白祇重工", True),
    ("ben", "本", "Ben", "A", "fire", "defense", "白祇重工", True),
    ("soukaku", "蒼角", "Soukaku", "A", "ice", "support", "對空洞特別行動部第六課", True),
    ("lucy", "露西", "Lucy", "A", "fire", "support", "卡呂冬之子", True),
    ("piper", "派派", "Piper", "A", "physical", "anomaly", "卡呂冬之子", True),
    ("seth", "賽斯", "Seth", "A", "electric", "defense", "刑偵特勤組", True),
    ("pulchra", "波可娜", "Pulchra", "A", "physical", "stun", "卡呂冬之子", True),
]

AGENTS = [
    {
        "id": a[0], "name": a[1], "name_en": a[2], "rank": a[3],
        "element": a[4], "element_zh": ELEMENTS[a[4]],
        "specialty": a[5], "specialty_zh": SPECIALTIES[a[5]],
        "faction": a[6], "standard": a[7],
    }
    for a in _A
]
AGENTS_BY_ID = {a["id"]: a for a in AGENTS}

# 常駐 S 級（用來判斷「歪」）— 收錄繁中、簡中、英文寫法
STANDARD_S_AGENTS = {
    "格莉絲", "格莉丝", "Grace", "麗娜", "丽娜", "Rina", "珂蕾妲", "Koleda",
    "貓又", "猫又", "Nekomata", "「11號」", "「11号」", "11號", "11号", "Soldier 11",
    "萊卡恩", "莱卡恩", "Lycaon", "Von Lycaon",
}
STANDARD_S_ENGINES = {
    "鋼鐵肉墊", "钢铁肉垫", "Steel Cushion",
    "硫磺石", "The Brimstone",
    "燃獄齒輪", "燃狱齿轮", "Hellfire Gears",
    "啜泣搖籃", "啜泣摇篮", "Weeping Cradle",
    "嵌合編譯器", "嵌合编译器", "Fusion Compiler",
    "拘縛者", "拘缚者", "The Restrained",
}


def is_standard_s(gacha_type, name):
    name = (name or "").strip()
    if gacha_type == "2":
        return name in STANDARD_S_AGENTS
    if gacha_type == "3":
        return name in STANDARD_S_ENGINES
    return False


def agent_by_name(name):
    n = (name or "").strip().lower()
    for a in AGENTS:
        if n in (a["name"].lower(), a["name_en"].lower()):
            return a
    return None


# ---------- 驅動盤 ----------
STAT_LABELS = {
    "crit_rate": "暴擊率", "crit_dmg": "暴擊傷害",
    "atk": "攻擊力", "atk_pct": "攻擊力%", "hp": "生命值", "hp_pct": "生命值%",
    "def": "防禦力", "def_pct": "防禦力%", "pen": "穿透值", "pen_ratio": "穿透率",
    "ap": "異常精通", "am": "異常掌控", "impact": "衝擊力", "er": "能量自動回復",
    "dmg_bonus": "屬性傷害加成",
}
# S 級驅動盤副詞條單次強化數值
SUB_ROLL = {
    "crit_rate": 2.4, "crit_dmg": 4.8, "atk": 19, "atk_pct": 3.0,
    "hp": 112, "hp_pct": 3.0, "def": 15, "def_pct": 4.8, "pen": 9, "ap": 9,
}
MAX_ROLLS = 9  # 初始 4 條 + 強化 5 次

ROLE_WEIGHTS = {
    "attack":  {"crit_rate": 1, "crit_dmg": 1, "atk_pct": 0.75, "pen": 0.5, "atk": 0.25},
    "anomaly": {"ap": 1, "atk_pct": 0.75, "pen": 0.75, "atk": 0.3},
    "stun":    {"crit_rate": 0.75, "crit_dmg": 0.5, "atk_pct": 0.5, "pen": 0.25, "atk": 0.2},
    "support": {"atk_pct": 1, "atk": 0.5, "crit_rate": 0.25, "pen": 0.25},
    "defense": {"def_pct": 1, "crit_rate": 0.5, "crit_dmg": 0.5, "atk_pct": 0.5, "def": 0.3},
    "rupture": {"hp_pct": 1, "crit_rate": 1, "crit_dmg": 1, "hp": 0.4},
}
# 個別角色覆寫
CHAR_WEIGHTS = {
    "jane":   {"ap": 1, "crit_rate": 0.75, "crit_dmg": 0.5, "atk_pct": 0.75, "pen": 0.5},
    "miyabi": {"crit_rate": 1, "crit_dmg": 1, "atk_pct": 0.75, "ap": 0.5, "pen": 0.5},
    "caesar": {"atk_pct": 0.75, "crit_rate": 0.75, "crit_dmg": 0.5, "def_pct": 0.5, "pen": 0.25},
    "astra":  {"atk_pct": 1, "atk": 0.5, "crit_rate": 0.5, "crit_dmg": 0.25},
}
RECOMMENDED_MAIN = {
    "attack":  {4: ["crit_rate", "crit_dmg"], 5: ["dmg_bonus", "atk_pct", "pen_ratio"], 6: ["atk_pct"]},
    "anomaly": {4: ["ap"], 5: ["dmg_bonus", "pen_ratio", "atk_pct"], 6: ["am", "atk_pct"]},
    "stun":    {4: ["crit_rate", "atk_pct"], 5: ["dmg_bonus", "atk_pct"], 6: ["impact"]},
    "support": {4: ["atk_pct", "crit_rate"], 5: ["atk_pct", "pen_ratio"], 6: ["er", "atk_pct"]},
    "defense": {4: ["crit_rate", "def_pct"], 5: ["def_pct", "dmg_bonus"], 6: ["def_pct", "impact"]},
    "rupture": {4: ["crit_rate", "crit_dmg"], 5: ["dmg_bonus", "hp_pct"], 6: ["hp_pct"]},
}


def weights_for(character_id=None, role=None):
    if character_id in CHAR_WEIGHTS:
        return CHAR_WEIGHTS[character_id]
    if character_id in AGENTS_BY_ID:
        role = AGENTS_BY_ID[character_id]["specialty"]
    return ROLE_WEIGHTS.get(role or "attack", ROLE_WEIGHTS["attack"])


# 預設推薦配隊（首次部署時寫入資料庫）
SEED_TEAMS = [
    ("艾蓮冰隊", ["ellen", "lycaon", "soukaku"], "經典冰屬性強攻隊，萊卡恩擊破、蒼角增傷。"),
    ("朱鳶以太隊", ["zhuyuan", "qingyi", "nicole"], "青衣快速擊破，妮可聚怪減防。"),
    ("雅·柳 紊亂隊", ["miyabi", "yanagi", "astra"], "雙異常紊亂，耀嘉音提供大量攻擊力。"),
    ("簡物理異常", ["jane", "seth", "caesar"], "簡強擊、賽斯護盾、凱撒全隊增益。"),
    ("柏妮思火隊", ["burnice", "lighter", "lucy"], "燃燒持續傷害，萊特擊破增傷。"),
]
