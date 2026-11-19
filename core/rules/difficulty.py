"""难度档位：四档各有一组独立参数。

设计约束：只调"外部条件"，不动核心公式与平衡常数。
- 起始现金：决定容错空间
- 事件密度与间隔：随机事件的触发概率，以及两次事件之间至少隔几周
- 可用性红线：技术破产的判定线
- A 轮评审线：注册用户数与近三月可用性
- 融资窗口：外部环境的宽松程度
- 每周行动力：简单 28 / 普通 24 / 困难 21
- 科技考核：简单模式读条完成即掌握，其余模式必须提交考核作业
- 随机种子偏移：让同一 seed 在不同难度下不是同一局
"""

from __future__ import annotations

from core.model.state import GameState

DEFAULT = "normal"

CONFIGS: dict[str, dict] = {
    "easy": {
        "label": "简单",
        "desc": "现金更厚、事故更少、评审线更低。适合第一次玩。",
        "cash_cents": 750_000_00,
        "ap_base": 28,
        "study_regen": 16,
        "tech_exam": False,
        "event_density": 0.30,
        "event_gap_weeks": 4,
        "availability_redline": 0.965,
        "funding_registered": 185_000,
        "funding_avail": 0.985,
        "funding_window": 0.85,
        "seed_offset": 0,
        "start_skills": {"sql": 10.0, "db": 12.0, "backend": 20.0, "ops": 8.0},
        "unlock_tech": [],
    },
    "normal": {
        "label": "普通",
        "desc": "设计基准难度。做对顺序能赢，但容错不大。",
        "cash_cents": 500_000_00,
        "ap_base": 24,
        "study_regen": 12,
        "tech_exam": True,
        "event_density": 0.55,
        "event_gap_weeks": 3,
        "availability_redline": 0.98,
        "funding_registered": 188_000,
        "funding_avail": 0.99,
        "funding_window": 0.72,
        "seed_offset": 0,
        "start_skills": {"sql": 10.0, "db": 12.0, "backend": 20.0, "ops": 8.0},
        "unlock_tech": [],
    },
    "hard": {
        "label": "困难",
        "desc": "现金紧、事故密、红线高。每一步都要算成本。",
        "cash_cents": 350_000_00,
        "ap_base": 21,
        "study_regen": 10,
        "tech_exam": True,
        "event_density": 0.75,
        "event_gap_weeks": 2,
        "availability_redline": 0.985,
        "funding_registered": 195_000,
        "funding_avail": 0.993,
        "funding_window": 0.6,
        "seed_offset": 7919,
        "start_skills": {"sql": 10.0, "db": 12.0, "backend": 20.0, "ops": 8.0},
        "unlock_tech": [],
    },
    "veteran": {
        "label": "老手",
        "desc": "SQL 技能 55 起步，并直接解锁 db_t1；考核仍然要一题一题自己写。",
        "cash_cents": 500_000_00,
        "ap_base": 24,
        "study_regen": 12,
        "tech_exam": True,
        "event_density": 0.65,
        "event_gap_weeks": 3,
        "availability_redline": 0.98,
        "funding_registered": 188_000,
        "funding_avail": 0.99,
        "funding_window": 0.72,
        "seed_offset": 104729,
        "start_skills": {"sql": 55.0, "db": 20.0, "backend": 24.0, "ops": 12.0},
        "unlock_tech": ["db_t1"],
    },
}


def config(name: str | None) -> dict:
    return CONFIGS.get(name or DEFAULT, CONFIGS[DEFAULT])


def get(state: GameState) -> dict:
    return config(state.meta.difficulty)


def labels() -> dict[str, str]:
    return {k: v["label"] for k, v in CONFIGS.items()}
