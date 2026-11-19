"""排队论：Erlang-C、M/M/c p95、磁盘 M/M/1。"""

from __future__ import annotations

import math

from core.rules import constants as C


def erlang_c(c: int, a: float) -> float:
    """c 个服务台、a 爱尔兰负载的等待概率。"""
    if c <= 0:
        return 1.0
    if a >= c:
        return 1.0
    b = 1.0
    for i in range(1, c + 1):
        b = a * b / (i + a * b)
    return b / (1 - (a / c) * (1 - b))


def mmc_p95(lam: float, service_s: float, c: int) -> dict:
    """返回 {a, rho, wait_p95_ms, latency_p95_ms}。p95 等待时间。"""
    if lam <= 0 or service_s <= 0 or c <= 0:
        return {"a": 0.0, "rho": 0.0, "wait_p95_ms": 0.0, "latency_p95_ms": 0.0}
    a = lam * service_s
    rho = a / c
    if rho >= 1.0:
        return {"a": a, "rho": rho, "wait_p95_ms": 60_000.0, "latency_p95_ms": 60_000.0}
    cc = erlang_c(c, a)
    if cc <= 0.05:
        return {"a": a, "rho": rho, "wait_p95_ms": 0.0, "latency_p95_ms": service_s * 1000}
    wait95 = math.log(cc / 0.05) / (c / service_s - lam)
    return {
        "a": a,
        "rho": rho,
        "wait_p95_ms": wait95 * 1000,
        "latency_p95_ms": (service_s + wait95) * 1000,
    }


def disk_wait_ms(iops: float, cap: int | None = None) -> float:
    """M/M/1 下单次 IO 的平均排队时间；ρ≥0.995 视为饱和。"""
    cap = cap or C.DEFAULT_IOPS
    rho = iops / cap
    if rho >= 0.995:
        return C.DISK_WAIT_CAP_MS
    return rho / (cap * (1 - rho)) * 1000


def mmc_tail(lam: float, service_s: float, c: int, t_s: float) -> float:
    """P(排队等待 > t_s)，用于估算超时比例。"""
    if lam <= 0 or service_s <= 0 or c <= 0:
        return 0.0
    a = lam * service_s
    rho = a / c
    if rho >= 1.0:
        # 过载：服务能力固定，超出部分被拒绝（1 - 1/ρ）
        return min(0.99, 1.0 - 1.0 / rho)
    cc = erlang_c(c, a)
    if cc <= 1e-9:
        return 0.0
    return min(0.99, cc * math.exp(-(c / service_s - lam) * max(0.0, t_s)))
