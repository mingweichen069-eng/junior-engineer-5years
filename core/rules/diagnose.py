"""瓶颈诊断：每 tick 只给一个主瓶颈标签，不给答案。

真实工程师的排查顺序是「先定位大类，再下钻日志」。
所以这里只输出一个 code + 一句提示，具体是哪条 SQL 仍然要玩家自己去看。
"""

from __future__ import annotations

from core.model.state import GameState
from core.rules.evaluate import PeakMetrics

SCAN_PATHS = {"full_scan", "index_order"}

LABELS = {
    "db_scan": ("全表扫描", "监控显示瓶颈来自全表扫描，优先核查高频查询的执行计划。"),
    "db_write": ("写入压力", "写入与索引维护占用了大量资源，检查索引数量与写入频率。"),
    "queue_application": ("应用排队", "请求在连接池或应用侧排队，先看并发与超时设置。"),
    "cache_stampede": ("缓存风险", "缓存命中率高但过期集中，存在击穿风险。"),
    "disk": ("磁盘饱和", "磁盘 IO 已经打满，加索引或改 SQL 的收益都在下降。"),
    "capacity": ("容量不足", "当前瓶颈是硬件容量，查询优化收益有限，考虑扩容。"),
    "healthy": ("运行平稳", "没有明显瓶颈，继续观察趋势。"),
}


def diagnose(state: GameState, m: PeakMetrics) -> dict:
    reads = [q for q in m.queries if q.path not in ("write", "other")]
    total_io = sum(q.qps * q.io_ops for q in m.queries) or 1.0
    scan_io = sum(q.qps * q.io_ops for q in reads if q.path in SCAN_PATHS)
    write_io = sum(q.qps * q.io_ops for q in m.queries if q.path == "write")
    scan_share = scan_io / total_io
    has_cache = bool(state.cache.policies)

    code = "healthy"
    if scan_share > 0.45 and m.io_iops > 0:
        code = "db_scan"
    elif write_io / total_io > 0.35:
        code = "db_write"
    elif m.pool_rho >= 0.8 or m.api_rho >= 0.9:
        code = "queue_application"
    elif has_cache and m.io_iops > 0 and scan_share > 0.2:
        code = "cache_stampede"
    elif m.io_util >= 0.85:
        # 全是索引访问还打满 → 不是 SQL 的锅
        code = "capacity" if scan_share < 0.2 else "disk"
    elif m.p95_ms > 500 and scan_share < 0.2:
        code = "capacity"

    label, hint = LABELS[code]
    return {"code": code, "label": label, "hint": hint, "scan_share": round(scan_share, 3)}
