"""游戏状态模型。所有金额为分（int），所有时间为毫秒。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Meta:
    seed: int
    week: int = 1
    difficulty: str = "normal"      # easy | normal | hard | veteran
    rules_version: str = "0.9.0"
    save_version: int = 6
    sandbox: bool = False
    # 玩家在开局选的「自动评价周」（50~100）。到点只给参考评价，不结束这一局。
    review_week: int = 60
    review_done: bool = False
    review_continued: bool = False


@dataclass
class Company:
    cash_cents: int
    registered: int
    marketing_cents: int = 200_000_00


@dataclass
class Column:
    type: str
    cardinality: int


@dataclass
class Index:
    columns: list[str]
    bloat: float = 0.0
    entry_bytes: int | None = None


@dataclass
class Table:
    name: str
    rows: int
    row_bytes: int
    fill_factor: float
    columns: dict[str, Column] = field(default_factory=dict)
    indexes: dict[str, Index] = field(default_factory=dict)


@dataclass
class Db:
    tables: dict[str, Table]
    buffer_pool_gb: float = 2.0
    disk_capacity_gb: float = 500.0
    disk_iops_limit: int = 3000
    max_connections: int = 200


@dataclass
class CachePolicy:
    endpoint: str
    ttl_s: int
    invalidate_on_write: bool = False


@dataclass
class Cache:
    enabled: bool = False
    memory_mb: int = 512
    policies: dict[str, CachePolicy] = field(default_factory=dict)


@dataclass
class App:
    servers: int = 6
    workers_per_server: int = 8
    pool_size: int = 40
    pool_timeout_ms: int = 2000
    request_timeout_ms: int = 1000
    read_replicas: int = 0          # 只读副本：分流读流量，但有复制延迟


@dataclass
class Org:
    manager_trust: float = 60.0
    satisfaction: dict[str, float] = field(default_factory=lambda: {"laochen": 62.0, "zhaolei": 58.0})
    tickets: int = 0


@dataclass
class Player:
    name: str = "你"
    level: str = "P4"
    ap: int = 24
    # 自学精力：每周自动回复，给科技读条加速（越高读得越快），随读条推进被消耗
    energy: float = 50.0
    # 每周行动力的永久加成（事件给的，比如招到人、拿到预算）
    ap_bonus_event: int = 0
    # 临时加成（只持续到 ap_temp_until 这一周）
    ap_temp_bonus: int = 0
    ap_temp_until: int = 0
    # 专业技能：动作门槛与考核用
    skills: dict[str, float] = field(default_factory=lambda: {"sql": 10.0, "db": 12.0, "backend": 20.0, "ops": 8.0})
    # 通用计算机素养：由「计算机基础」轨道提供，决定跨线成本与部分节点门槛
    traits: dict[str, float] = field(
        default_factory=lambda: {"arch": 0.0, "os": 0.0, "algo": 0.0, "net": 0.0, "conc": 0.0, "dist": 0.0}
    )


@dataclass
class DebtItem:
    id: str
    severity: float
    growth: float = 0.02
    resolved: bool = False
    category: str = "query"      # query | ops | arch
    name: str = ""


@dataclass
class Debt:
    items: dict[str, DebtItem] = field(default_factory=dict)


@dataclass
class PendingEffect:
    action_id: str
    due_week: int
    effect: dict


@dataclass
class ActionState:
    pending: list[PendingEffect] = field(default_factory=list)
    applied: dict[str, bool] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    # 本周实际执行的动作数：动作越多，下周自学精力回复越低（救火挤掉学习）。
    actions_this_week: int = 0
    monitoring: bool = False
    backup: bool = False
    docs: int = 0


@dataclass
class WeekSnapshot:
    week: int
    peak_qps: float
    p95_ms: float
    error_rate: float
    disk_used_gb: float
    io_util: float
    cash_cents: int
    revenue_cents: int = 0
    cost_cents: int = 0
    event_count: int = 0


@dataclass
class MonthSnapshot:
    month: int
    revenue_cents: int
    cost_cents: int
    profit_cents: int
    cash_cents: int
    availability: float
    p95_ms: float
    sla_penalty_cents: int = 0
    grade: str = "B"


@dataclass
class EventLogEntry:
    log_id: str
    event_id: str
    title: str
    week: int
    day: int
    cause_id: str | None = None
    depth: int = 0
    effects: dict = field(default_factory=dict)
    tag: str = "fault"        # fault | business | finance | team
    choice: str = ""          # 玩家选择的 id（决策事件）
    choice_label: str = ""    # 玩家选择的文案
    narrative: str = ""       # 事件正文，供弹窗回看
    speaker: str = ""
    pending: bool = False     # 还没做决策


@dataclass
class PendingEvent:
    """等待玩家决策的事件。不选就会在推进到下一周时按默认选项结算。"""
    id: str
    event_id: str
    week: int
    day: int
    log_id: str
    default_choice: str = ""


@dataclass
class EventState:
    log: list[EventLogEntry] = field(default_factory=list)
    cooldowns: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    pending: list[PendingEvent] = field(default_factory=list)
    # 上一次「真出了事件」的周：用来拉长间隔，避免一周一个弹窗
    last_event_week: int = 0


@dataclass
class EndingState:
    stage: str = "safe"              # safe | watch | warn | list
    weeks_in_stage: int = 0
    last_score: float = 100.0
    last_breakdown: dict = field(default_factory=dict)
    wave_pressure: float = 0.0
    rescue_goal: str | None = None
    rescue_target: float = 0.0
    rescue_deadline: int = 0
    ended: str | None = None         # funded | layoff | bankrupt
    ended_week: int = 0
    flavor: str = ""                 # 平行结局方向：db | backend | sre | algo | ""
    timeline: list[str] = field(default_factory=list)


@dataclass
class ReviewState:
    """阶段性述职：到 review_week 自动结算一次，只作参考，不结束游戏。

    reports 里存每次评价的完整数据成就快照；milestones 记「A 轮资格 / 融资窗口关闭」
    这类原本会强行结束游戏的节点——现在它们只是履历上的一行字。
    """
    pending: bool = False            # 到点了，等玩家打开评价面板
    continued: bool = False          # 玩家已经选择继续游玩
    reports: list[dict] = field(default_factory=list)
    last: dict = field(default_factory=dict)
    milestones: list[str] = field(default_factory=list)


@dataclass
class TechState:
    """科技树：点「开始读条」后随时间自动推进（每周一次），不花行动力；
    推进速度由玩家的自学精力（player.energy）决定。进度满了之后按难度分流：
    简单模式直接掌握，其余进入考核（失败冷却一周，第二次失败读条清零）。

    knowledge 记录读过的知识卡；mastery 是本局选定的专精（与通才互斥）；
    ap_bonus 是每周行动力的永久加成；active 是正在读条的节点（同时只能有一个）。
    """
    progress: dict[str, int] = field(default_factory=dict)   # node -> 已完成的学时
    unlocked: dict[str, bool] = field(default_factory=dict)
    direction: str = ""              # 当前主攻方向（切换方向要重新解锁）
    knowledge: dict[str, bool] = field(default_factory=dict)  # 知识卡 id -> 已读
    mastery: str = ""                # db | backend | sre | perf | ""
    ap_bonus: int = 0                # 每周行动力加成
    active: str = ""                 # 正在读条的节点 id（"" 表示没在学）
    # node -> {"state": "open"|"cool", "week": 开考周, "until": 可重考周, "fails": 次数}
    exams: dict[str, dict] = field(default_factory=dict)
    exams_passed: int = 0            # 累计通过的真机考核次数（评价面板的数据成就）
    exams_failed: int = 0


@dataclass
class GameState:
    meta: Meta
    company: Company
    db: Db
    cache: Cache
    app: App
    org: Org
    player: Player
    debt: Debt
    history: list[WeekSnapshot] = field(default_factory=list)
    actions: ActionState = field(default_factory=ActionState)
    flags: dict[str, bool] = field(default_factory=dict)
    months: list[MonthSnapshot] = field(default_factory=list)
    events: EventState = field(default_factory=EventState)
    ending: EndingState = field(default_factory=EndingState)
    review: ReviewState = field(default_factory=ReviewState)
    tech: TechState = field(default_factory=TechState)
