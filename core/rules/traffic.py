"""用户与流量。"""

from __future__ import annotations

from dataclasses import dataclass

from core.rules import constants as C


@dataclass
class Traffic:
    dau: float
    req_day: float
    avg_qps: float
    peak_qps: float


def traffic(registered: int, dau_factor: float = 1.0) -> Traffic:
    dau = registered * C.DAU_RATIO * dau_factor
    req_day = dau * C.REQ_PER_DAU_DAY
    avg = req_day / 86_400
    return Traffic(dau=dau, req_day=req_day, avg_qps=avg, peak_qps=avg * C.PEAK_FACTOR)
