"""高峰窗口的一次性评估：查询 → 磁盘 → 连接池 → 应用层。"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.model.state import GameState
from core.rules import constants as C
from core.rules import load_profile
from core.rules import query as qmod
from core.rules.queue import disk_wait_ms, mmc_p95, mmc_tail
from core.rules.storage import buffer_hit, disk_used_gb
from core.rules.traffic import traffic


@dataclass
class QueryMetric:
    id: str
    name: str
    qps: float
    time_ms: float
    rows: int
    path: str
    io_ops: float
    user_facing: bool = True
    qpr: int = 1


@dataclass
class PeakMetrics:
    week: int
    dau: float
    avg_qps: float
    peak_qps: float
    buffer_hit: float
    disk_used_gb: float
    io_iops: float
    io_util: float
    disk_wait_ms: float
    queries: list[QueryMetric] = field(default_factory=list)
    db_qps: float = 0.0
    pool_rho: float = 0.0
    pool_wait_ms: float = 0.0
    p95_ms: float = 0.0
    api_rho: float = 0.0
    error_rate: float = 0.0

    @property
    def availability(self) -> float:
        return max(0.0, min(1.0, 1.0 - self.error_rate))

    def by_id(self, qid: str) -> QueryMetric | None:
        for item in self.queries:
            if item.id == qid:
                return item
        return None


def cache_hit(qps: float, ttl_s: int) -> float:
    spread = qps * ttl_s
    if spread <= 1.0:
        return 0.0
    return max(0.0, min(0.999, 1.0 - 1.0 / spread))


def effective_query(state: GameState, q: dict) -> dict:
    """玩家改写过 SQL 之后，谓词从不可 sargable 变成可走索引的范围查询。

    写查询（下单 / 支付回调）只有在 flags.writes_enabled 打开后才参与模拟，
    默认关闭是为了保住开局基线（852ms / 100% 可用性）。
    读查询是否参与则由目录里的 active 决定。
    """
    eff = dict(q)
    if q.get("kind") == "write":
        eff["active"] = bool(state.flags.get("writes_enabled", False))
    if q["id"] == "q_dashboard" and state.flags.get("q_dashboard_rewritten"):
        rows = max(1, state.db.tables[q["table"]].rows)
        eff["predicates"] = [{"column": "created_at", "op": "eq"}]
        eff["selectivity"] = min(1.0, 3000.0 / rows)
        eff["active"] = True
    return eff


def is_active(state: GameState, q: dict) -> bool:
    """写查询是否参与取决于 flags；其余看目录里的 active。"""
    eff = effective_query(state, q)
    return bool(eff.get("active", True))


def evaluate_peak(state: GameState, catalog: list[dict] | None = None) -> PeakMetrics:
    t = traffic(state.company.registered)
    spike = float(state.flags.get("spike_mult", 1.0))
    if spike > 1.0:
        t.peak_qps *= spike
        t.avg_qps *= spike
    catalog = catalog or qmod.load_catalog()
    active = [q for q in qmod.active_queries(catalog) if is_active(state, q)]
    hit = buffer_hit(state.db)
    # 科技树修正：学到的东西必须真的改变数字，否则「知识」只是文案
    from core.rules import tech as tech_rules

    mods = tech_rules.mods(state)
    qt = float(mods.get("query_time", 1.0))
    # 只有物理上命中该查询的索引才降低它的耗时；建错的索引只留代价。
    per_query = dict(mods.get("per_query") or {})

    base: list[QueryMetric] = []
    for q in active:
        table = state.db.tables[q["table"]]
        # 大屏是外部驱动（老板的墙），不随业务 QPS 缩放；其余查询按压测权重缩放
        if q.get("fixed_qps") is not None:
            qps = float(q["fixed_qps"]) * load_profile.weight(q["id"])
        elif q.get("fixed_share") is not None:
            qps = float(q["fixed_share"]) * t.peak_qps
        else:
            qps = float(q.get("share") or 0.0) * t.peak_qps * load_profile.weight(q["id"])

        if q.get("kind") == "other":
            base.append(QueryMetric(q["id"], q["name"], qps, 2.0, 1, "other",
                                    C.OTHER_IO_OPS * qps))
            continue

        policy = state.cache.policies.get(q["id"]) if state.cache.enabled else None
        if policy is not None:
            qps *= 1.0 - cache_hit(qps, policy.ttl_s)

        q_eff = effective_query(state, q)
        path = qmod.choose_path(table, q_eff, hit, wait_ms=0.0)
        base.append(QueryMetric(
            id=q["id"],
            name=q["name"],
            qps=qps,
            time_ms=path.cost.total_ms * float(per_query.get(q["id"], qt)),
            rows=path.cost.rows,
            path=path.kind,
            io_ops=path.cost.io_ops,
            user_facing=bool(q.get("user_facing", q["id"] != "q_dashboard")),
            qpr=int(q.get("queries_per_request", 1)),
        ))

    iops = sum(r.qps * r.io_ops for r in base)
    # 只读副本：把读请求分走一部分，主库 IO 下降；代价是复制延迟（见 error_rate）
    replicas = max(0, int(getattr(state.app, "read_replicas", 0)))
    if replicas:
        offload = min(0.75, 0.35 * replicas)
        for r in base:
            if r.path not in ("write", "other"):
                r.io_ops *= 1.0 - offload
        iops = sum(r.qps * r.io_ops for r in base)
    io_cap = state.db.disk_iops_limit
    wait = disk_wait_ms(iops, io_cap) * float(mods.get("io_wait", 1.0))
    # 显示用：磁盘利用率封顶 100%，物理上不存在 105%
    io_util = min(1.0, iops / io_cap)
    io_saturated = iops / io_cap >= 0.995

    metrics: list[QueryMetric] = []
    for r, q in zip(base, active):
        if r.path == "other":
            metrics.append(r)
            continue
        table = state.db.tables[q["table"]]
        path = qmod.choose_path(table, effective_query(state, q), hit, wait_ms=wait)
        r.time_ms = path.cost.total_ms * qt
        r.rows = path.cost.rows
        metrics.append(r)

    db_qps = sum(r.qps * r.qpr for r in metrics)
    s_db_ms = sum(r.qps * r.qpr * r.time_ms for r in metrics) / db_qps if db_qps else 0.0
    pool_cap = max(1, int(round(state.app.pool_size * float(mods.get("pool_capacity", 1.0)))))
    pool = mmc_p95(db_qps, s_db_ms / 1000.0, pool_cap)

    user_facing = [r for r in metrics if r.user_facing and r.path != "other"]
    slowest_ms = max((r.time_ms for r in user_facing), default=1.0)

    # 长尾传染：慢查询会抢占连接与 IO，把整体延迟一起抬起来。
    # 所以只优化 TOP1 慢 SQL 不够，长尾堆积一样会拖垮服务。
    slow_share = (
        sum(r.qps for r in user_facing if r.time_ms > 500) / t.peak_qps if t.peak_qps else 0.0
    )
    contagion_ms = min(400.0, max(0.0, slow_share - 0.05) * 4000) * float(mods.get("contagion", 1.0))

    api_service_ms = slowest_ms + C.API_OVERHEAD_MS * float(mods.get("api_overhead", 1.0)) + contagion_ms
    api = mmc_p95(
        t.peak_qps * C.API_ARRIVAL_SHARE,
        api_service_ms / 1000.0,
        state.app.servers * state.app.workers_per_server,
    )
    # 过载时 mmc_p95 返回哨兵值 60000，不要直接当 p95 展示
    p95_ms = api["latency_p95_ms"]
    if io_saturated or api["rho"] >= 1.0 or p95_ms >= 60_000:
        p95_ms = max(slowest_ms, 10_000.0)

    # 过载迹象：磁盘被非用户查询（大屏、后台任务）打满时，
    # 用户请求也会被拖慢，不能只看"单条查询本身超没超时"。
    timed_out = sum(r.qps for r in user_facing if r.time_ms > state.app.request_timeout_ms)
    query_timeout_share = (timed_out / t.peak_qps) if t.peak_qps else 0.0
    # 应用层排队也会导致超时：P(等待 > 超时阈值)
    app_timeout_share = mmc_tail(
        t.peak_qps * C.API_ARRIVAL_SHARE,
        api_service_ms / 1000.0,
        state.app.servers * state.app.workers_per_server,
        max(0.0, (state.app.request_timeout_ms - api_service_ms) / 1000.0),
    )
    error_rate = min(0.99, max(query_timeout_share, app_timeout_share))
    # 只读副本的复制延迟：少量读到旧数据，表现为轻微错误率
    if replicas:
        error_rate = min(0.99, error_rate + 0.002 * replicas * float(mods.get("replica_lag", 1.0)))
    # 磁盘饱和：所有请求一起被拖慢，按 p95 超出阈值的程度折算错误率
    if io_util >= 0.90:
        over = (io_util - 0.90) / 0.10
        error_rate = max(error_rate, min(0.5, 0.25 * over * min(1.0, p95_ms / 800.0)))
    # 朴素 DDL 的锁冲击：生效当周写入被锁，出现一次尖峰
    if state.flags.get("lock_week") == state.meta.week:
        lock_ms = float(state.flags.get("lock_ms", 0.0))
        p95_ms = max(p95_ms, lock_ms)
        error_rate = max(error_rate, 0.03)
    # 磁盘打满时，(p95, 可用性) 必须是自洽的一对：慢 ⇒ 有错误，
    # 不能出现「p95 数万毫秒但可用性 97%」这种互相打架的指标。
    if p95_ms >= 5_000:
        error_rate = max(error_rate, 0.10)

    return PeakMetrics(
        week=state.meta.week,
        dau=t.dau,
        avg_qps=t.avg_qps,
        peak_qps=t.peak_qps,
        buffer_hit=hit,
        disk_used_gb=disk_used_gb(state.db),
        io_iops=iops,
        io_util=io_util,
        disk_wait_ms=wait,
        queries=metrics,
        db_qps=db_qps,
        pool_rho=min(1.0, pool["rho"]),
        pool_wait_ms=pool["wait_p95_ms"],
        p95_ms=p95_ms,
        api_rho=min(1.0, api["rho"]),
        error_rate=error_rate,
    )
