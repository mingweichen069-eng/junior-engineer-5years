"""生产负载权重：读查询不一定"每一次请求都要查一次库"。

压测实测：订单详情只有约 1/6 的请求真的带缓存未命中，
写入只占读请求的一小部分。这个文件把"名义占比"换成"实际打到数据库的权重"。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]      # core/rules/x.py → 项目根
PROFILE_PATH = ROOT / "content" / "load_profile.json"

# 唯一数据源是 content/load_profile.json。
# 这里的默认值只在文件缺失时兜底，必须与 JSON 保持一致（有测试守着）。
DEFAULT_WEIGHTS = {
    "q_my_orders": 1.0,      # 用户主动打开订单页，每次都打库
    "q_dashboard": 1.0,      # fixed_qps 已经是绝对值
    "q_pending": 0.15,       # 客服后台轮询，大部分命中应用缓存
    "q_detail": 0.17,        # 2% 请求，平均只有约 1 次缓存未命中
    "q_create": 1.0,
    "q_pay": 1.0,
}

_STATE: dict[str, float] | None = None


def load() -> dict[str, float]:
    global _STATE
    if _STATE is None:
        if PROFILE_PATH.exists():
            _STATE = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        else:
            _STATE = dict(DEFAULT_WEIGHTS)
    return _STATE


def weight(query_id: str) -> float:
    return load().get(query_id, 1.0)


def set_weights(weights: dict[str, float]) -> None:
    global _STATE
    _STATE = dict(weights)
