"""絕區零 Zenless Zone Zero。※ 非官方整理，新角色推出時直接在 _A 補上。"""
from .base import STAT_LABELS, make_agents, name_index

ELEMENTS = {"ice": "冰", "fire": "火", "electric": "電", "physical": "物理", "ether": "以太"}
SPECIALTIES = {"attack": "強攻", "stun": "擊破", "anomaly": "異常", "support": "支援",
               "defense": "防護", "rupture": "命破"}

_A = [
    ("ellen", "艾蓮", "Ellen", "S", "ice", "attack", "維多利亞家政"),
    ("zhuyuan", "朱鳶", "Zhu Yuan", "S", "ether", "attack", "刑偵特勤組"),
    ("jane", "簡", "Jane", "S", "physical", "anomaly", "刑偵特勤組"),
    ("qingyi", "青衣", "Qingyi", "S", "electric", "stun", "刑偵特勤組"),
    ("caesar", "凱撒", "Caesar", "S", "physical", "defense", "卡呂冬之子"),
    ("burnice", "柏妮思", "Burnice", "S", "fire", "anomaly", "卡呂冬之子"),
    ("yanagi", "柳", "Yanagi", "S", "electric", "anomaly", "對空洞特別行動部第六課"),
    ("lighter", "萊特", "Lighter", "S", "fire", "stun", "卡呂冬之子"),
    ("miyabi", "雅", "Miyabi", "S", "ice", "anomaly", "對空洞特別行動部第六課"),
    ("harumasa", "悠真", "Harumasa", "S", "electric", "attack", "對空洞特別行動部第六課"),
    ("astra", "耀嘉音", "Astra Yao", "S", "ether", "support", "天琴座"),
    ("evelyn", "伊芙琳", "Evelyn", "S", "fire", "attack", "天琴座"),
    ("trigger", "扳機", "Trigger", "S", "electric", "stun", "奧波勒斯小隊"),
    ("vivian", "薇薇安", "Vivian", "S", "ether", "anomaly", "反舌鳥"),
    ("hugo", "雨果", "Hugo", "S", "ice", "attack", "反舌鳥"),
    ("yixuan", "儀玄", "Yixuan", "S", "ether", "rupture", "雲巋山"),
    ("jufufu", "橘福福", "Ju Fufu", "S", "fire", "stun", "雲巋山"),
    ("yuzuha", "柚葉", "Yuzuha", "S", "physical", "support", "怪啖屋"),
    ("alice", "愛麗絲", "Alice", "S", "physical", "anomaly", "怪啖屋"),
    ("grace", "格莉絲", "Grace", "S", "electric", "anomaly", "白祇重工"),
    ("rina", "麗娜", "Rina", "S", "electric", "support", "維多利亞家政"),
    ("koleda", "珂蕾妲", "Koleda", "S", "fire", "stun", "白祇重工"),
    ("nekomata", "貓又", "Nekomata", "S", "physical", "attack", "狡兔屋"),
    ("soldier11", "「11號」", "Soldier 11", "S", "fire", "attack", "奧波勒斯小隊"),
    ("lycaon", "萊卡恩", "Lycaon", "S", "ice", "stun", "維多利亞家政"),
    ("anby", "安比", "Anby", "A", "electric", "stun", "狡兔屋"),
    ("billy", "比利", "Billy", "A", "physical", "attack", "狡兔屋"),
    ("nicole", "妮可", "Nicole", "A", "ether", "support", "狡兔屋"),
    ("corin", "可琳", "Corin", "A", "physical", "attack", "維多利亞家政"),
    ("anton", "安東", "Anton", "A", "electric", "attack", "白祇重工"),
    ("ben", "本", "Ben", "A", "fire", "defense", "白祇重工"),
    ("soukaku", "蒼角", "Soukaku", "A", "ice", "support", "對空洞特別行動部第六課"),
    ("lucy", "露西", "Lucy", "A", "fire", "support", "卡呂冬之子"),
    ("piper", "派派", "Piper", "A", "physical", "anomaly", "卡呂冬之子"),
    ("seth", "賽斯", "Seth", "A", "electric", "defense", "刑偵特勤組"),
    ("pulchra", "波可娜", "Pulchra", "A", "physical", "stun", "卡呂冬之子"),
]
STANDARD = {"grace", "rina", "koleda", "nekomata", "soldier11", "lycaon"}
ALIASES = {
    "ellen": ["艾蓮·喬", "艾莲·乔", "艾莲", "Ellen Joe"], "zhuyuan": ["朱鸢"], "jane": ["簡·杜", "简·杜", "简", "Jane Doe"],
    "caesar": ["凯撒", "Caesar King"], "yanagi": ["月城柳", "Tsukishiro Yanagi"], "lighter": ["莱特"],
    "miyabi": ["星見雅", "星见雅", "Hoshimi Miyabi"], "harumasa": ["淺羽悠真", "浅羽悠真", "悠真", "Asaba Harumasa"],
    "trigger": ["扳机"], "yixuan": ["仪玄"], "yuzuha": ["浮波柚葉", "浮波柚叶", "柚叶"], "alice": ["爱丽丝"],
    "grace": ["格莉絲·霍華德", "格莉丝", "Grace Howard"], "rina": ["丽娜"], "koleda": ["珂蕾妲·貝洛伯格", "Koleda Belobog"],
    "nekomata": ["貓宮又奈", "猫宫又奈", "猫又", "Nekomiya Mana"], "soldier11": ["11號", "11号", "「11号」"],
    "lycaon": ["莱卡恩", "Von Lycaon"], "anby": ["安比·德瑪拉", "Anby Demara"], "billy": ["比利·奇德", "Billy Kid"],
    "nicole": ["妮可·德瑪拉", "Nicole Demara"], "corin": ["可琳·威克斯", "Corin Wickes"], "anton": ["安东", "Anton Ivanov"],
    "ben": ["本·比格", "Ben Bigger"], "soukaku": ["苍角"], "lucy": ["露西亞娜", "Luciana de Montefio"],
    "piper": ["派派·韋爾", "Piper Wheel"], "seth": ["赛斯", "Seth Lowell"], "pulchra": ["Pulchra Fellini"],
}

AGENTS = make_agents(_A, ELEMENTS, SPECIALTIES, STANDARD)

STD_ENGINES = {
    "鋼鐵肉墊", "钢铁肉垫", "Steel Cushion", "硫磺石", "The Brimstone", "燃獄齒輪", "燃狱齿轮", "Hellfire Gears",
    "啜泣搖籃", "啜泣摇篮", "Weeping Cradle", "嵌合編譯器", "嵌合编译器", "Fusion Compiler", "拘縛者", "拘缚者", "The Restrained",
}

GAME = {
    "id": "zzz", "short": "ZZZ", "name": "絕區零", "name_en": "Zenless Zone Zero",
    "accent": "#c6f432", "accent_ink": "#141a00",
    "labels": {
        "agent": "代理人", "agent_db": "代理人資料庫", "rank_S": "S 級", "rank_A": "A 級",
        "currency": "菲林", "disc_item": "驅動盤", "disc_tab": "驅動盤評分", "specialty": "特性", "faction": "陣營",
        "record_page": "調頻 → 紀錄", "record_name": "調頻紀錄",
    },
    "agents": AGENTS,
    "name_index": name_index(AGENTS, ALIASES),
    "elements": ELEMENTS, "specialties": SPECIALTIES,
    "gacha": {
        "hosts": {"global": "https://public-operation-nap-sg.hoyoverse.com",
                  "cn": "https://public-operation-nap.mihoyo.com"},
        "path": "/common/gacha_record/api/getGachaLog", "type_param": "real_gacha_type",
        "cn_biz_suffix": "_cn", "cn_domain": "mihoyo.com",
        "fetch_types": ["2", "3", "1", "5"], "channel_of": {},
        "channels": {"2": "獨家頻段", "3": "音擎頻段", "1": "常駐頻段", "5": "邦布頻段"},
        "pity": {"1": 90, "2": 90, "3": 80, "5": 80},
        "expected": {"1": 62.5, "2": 62.5, "3": 50.0, "5": 50.0},
        "rank_map": {"4": "S", "3": "A", "2": "B"},
        "standard": {"2": {a["name"] for a in AGENTS if a["id"] in STANDARD}
                     | {n for i in STANDARD for n in ALIASES.get(i, [])}
                     | {a["name_en"] for a in AGENTS if a["id"] in STANDARD},
                     "3": STD_ENGINES},
        "featured_channel": "2",
    },
    "checkin": {"base": "https://sg-public-api.hoyolab.com/event/luna/zzz/os", "act_id": "e202406031448091",
                "headers": {"x-rpc-signgame": "zzz"}},
    "disc": {
        "item": "驅動盤",
        "slots": [
            {"id": 1, "label": "1 號位", "mains": ["hp"]},
            {"id": 2, "label": "2 號位", "mains": ["atk"]},
            {"id": 3, "label": "3 號位", "mains": ["def"]},
            {"id": 4, "label": "4 號位", "mains": ["crit_rate", "crit_dmg", "ap", "atk_pct", "hp_pct", "def_pct"]},
            {"id": 5, "label": "5 號位", "mains": ["dmg_bonus", "pen_ratio", "atk_pct", "hp_pct", "def_pct"]},
            {"id": 6, "label": "6 號位", "mains": ["am", "impact", "er", "atk_pct", "hp_pct", "def_pct"]},
        ],
        "default_slot": 4,
        "sub_roll": {"crit_rate": 2.4, "crit_dmg": 4.8, "atk": 19, "atk_pct": 3.0, "hp": 112, "hp_pct": 3.0,
                     "def": 15, "def_pct": 4.8, "pen": 9, "ap": 9},
        "max_rolls": 9,
        "builds": {
            "attack": {"weights": {"crit_rate": 1, "crit_dmg": 1, "atk_pct": 0.75, "pen": 0.5, "atk": 0.25},
                       "mains": {4: ["crit_rate", "crit_dmg"], 5: ["dmg_bonus", "atk_pct", "pen_ratio"], 6: ["atk_pct"]}},
            "anomaly": {"weights": {"ap": 1, "atk_pct": 0.75, "pen": 0.75, "atk": 0.3},
                        "mains": {4: ["ap"], 5: ["dmg_bonus", "pen_ratio", "atk_pct"], 6: ["am", "atk_pct"]}},
            "stun": {"weights": {"crit_rate": 0.75, "crit_dmg": 0.5, "atk_pct": 0.5, "pen": 0.25, "atk": 0.2},
                     "mains": {4: ["crit_rate", "atk_pct"], 5: ["dmg_bonus", "atk_pct"], 6: ["impact"]}},
            "support": {"weights": {"atk_pct": 1, "atk": 0.5, "crit_rate": 0.25, "pen": 0.25},
                        "mains": {4: ["atk_pct", "crit_rate"], 5: ["atk_pct", "pen_ratio"], 6: ["er", "atk_pct"]}},
            "defense": {"weights": {"def_pct": 1, "crit_rate": 0.5, "crit_dmg": 0.5, "atk_pct": 0.5, "def": 0.3},
                        "mains": {4: ["crit_rate", "def_pct"], 5: ["def_pct", "dmg_bonus"], 6: ["def_pct", "impact"]}},
            "rupture": {"weights": {"hp_pct": 1, "crit_rate": 1, "crit_dmg": 1, "hp": 0.4},
                        "mains": {4: ["crit_rate", "crit_dmg"], 5: ["dmg_bonus", "hp_pct"], 6: ["hp_pct"]}},
        },
        "char_weights": {
            "jane": {"ap": 1, "crit_rate": 0.75, "crit_dmg": 0.5, "atk_pct": 0.75, "pen": 0.5},
            "miyabi": {"crit_rate": 1, "crit_dmg": 1, "atk_pct": 0.75, "ap": 0.5, "pen": 0.5},
            "caesar": {"atk_pct": 0.75, "crit_rate": 0.75, "crit_dmg": 0.5, "def_pct": 0.5, "pen": 0.25},
            "astra": {"atk_pct": 1, "atk": 0.5, "crit_rate": 0.5, "crit_dmg": 0.25},
        },
    },
    "stat_labels": {**STAT_LABELS, "er": "能量自動回復"},
    "ocr": {
        "item_types": {"agents": "代理人", "agent": "代理人", "w-engines": "音擎", "w-engine": "音擎",
                       "bangboo": "邦布", "代理人": "代理人", "音擎": "音擎", "邦布": "邦布"},
        "channel_words": ("頻段", "频段", "channel"),
        "stat_aliases": [
            ("pen ratio", "pen_ratio"), ("pen", "pen"), ("anomaly proficiency", "ap"), ("anomaly mastery", "am"),
            ("energy regen", "er"), ("impact", "impact"),
            ("穿透率", "pen_ratio"), ("穿透值", "pen"), ("穿透", "pen"), ("異常精通", "ap"), ("异常精通", "ap"),
            ("異常掌控", "am"), ("异常掌控", "am"), ("能量自動回復", "er"), ("能量自动回复", "er"),
            ("衝擊力", "impact"), ("冲击力", "impact"),
        ],
        "slot_words": {},
    },
    "script": {"log_roots": [r"miHoYo"], "log_names": ["Player.log"],
               "data_regex": r"([A-Za-z]:[\\/].+?ZenlessZoneZero_Data)"},
}
