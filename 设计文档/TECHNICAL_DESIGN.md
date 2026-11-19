# 技术设计文档（TDD）

| 项 | 内容 |
|---|---|
| 版本 | v0.1 |
| 配套 | [GAME_DESIGN.md](GAME_DESIGN.md) · [SIMULATION_RULES.md](SIMULATION_RULES.md) · [MVP_PLAN.md](MVP_PLAN.md) |
| 原则 | 规则确定、结果可复算、表现层可替换、AI 不参与结算 |

---

## 1. 目标与非目标

**目标**

1. 核心模拟是一个**纯函数**：`step(state, actions) -> state'`。同样的存档 + 同样的操作 = 逐比特相同的结果。
2. 规则与界面彻底解耦。删掉整个 `web/` 目录，剩下的东西能用命令行跑完 6 个月。
3. 技术后果来自**可校准的成本模型 + 真实 SQLite 实验**，不来自大模型。
4. 一个人（你）能维护。没有构建步骤，没有前端框架，没有容器依赖。

**非目标（第一版明确不做）**

- 不做实时多人、不做账号系统、不做云部署。
- 不做任意 Python 代码执行（沙箱成本高、风险大），只做 SQL。
- 不做微服务、不做消息队列、不做 ORM。
- 不做移动端适配（桌面优先，最小宽度 1280）。

---

## 2. 技术选型

| 层 | 选择 | 理由 | 被拒绝的方案 |
|---|---|---|---|
| 语言 | Python 3.11+ | 你已掌握；数值计算和数据处理方便 | — |
| Web | Flask 3.x | 你已经写过 Flask，改造成本最低 | FastAPI（异步对本项目无收益）、Django（太重） |
| 模板 | Jinja2 | Flask 自带，首页一次渲染，面板用 JSON 局部更新 | React/Vue（引入构建链，违背低成本原则） |
| 前端脚本 | 原生 ES Modules | 无构建、无依赖、可直接读 | 任何需要 npm 的框架 |
| 图表 | Chart.js（本地 UMD 文件） | 成熟、体积可接受、API 简单 | ECharts（体积大）、D3（学习成本高） |
| 存档 | SQLite（单文件） | 事务安全、可查询、可导出 | JSON 文件（并发写不安全）、MySQL（部署成本） |
| 实验台 | SQLite（只读副本） | 零部署、真执行、真 EXPLAIN | Docker MySQL（第一版过重） |
| 生产仿真 | 解析成本模型 | 快、确定、可校准、可测试 | 真跑 MySQL（慢、不确定、无法复算） |
| 测试 | pytest | 事实标准 | — |
| 配置/内容 | YAML（查询目录、事件、动作） | 改内容不用改代码 | 硬编码在 Python 里 |

**为什么不用大模型做核心**：不可复现、有延迟和成本、会编造数字。AI 只在"把事实写成人话"这一层出现，而且是可替换、可关闭的。

---

## 3. 分层架构

```
┌─────────────────────────────────────────────────────────────┐
│  web/            Flask 路由 · JSON API · Jinja 模板 · 静态资源 │
├─────────────────────────────────────────────────────────────┤
│  services/       存档 · 回放 · 叙事（模板/AI）· 导出          │
├─────────────────────────────────────────────────────────────┤
│  core/           纯函数，无 IO、无 Flask、无时间、无随机       │
│    model/        状态数据类                                   │
│    rules/        流量/查询/数据库/应用/经济/组织/事件/职业     │
│    engine.py     step() / settle_month() / apply_action()     │
├─────────────────────────────────────────────────────────────┤
│  content/        YAML/JSON：查询目录、事件、动作、文档、校准值 │
├─────────────────────────────────────────────────────────────┤
│  lab/            SQLite 副本生成 · 只读执行 · EXPLAIN          │
└─────────────────────────────────────────────────────────────┘
```

**依赖方向**：`web → services → core`，`lab` 只被 `services` 调用，`core` 不依赖任何东西。这条规则用一条测试守住：`core/` 里 import 了 `flask`/`sqlite3`/`time` 就失败。

---

## 4. 状态模型

### 4.1 顶层结构

```python
@dataclass
class GameState:
    meta: Meta              # 版本、种子、周数、难度
    company: Company        # 现金、用户、收入、成本
    tech: TechState         # 数据库、应用、缓存、查询
    org: OrgState           # 员工、满意度、主管评价
    player: PlayerState     # 技能、精力、绩效、履历
    debt: DebtState         # 技术债条目
    events: EventState      # 活跃事件、历史、冷却
    history: list[WeekSnapshot]   # 每周指标快照（报表用）
```

### 4.2 技术状态（核心）

```python
@dataclass
class Column:
    name: str
    type: str              # "bigint" | "int" | "datetime" | "tinyint" | "decimal"
    cardinality: int       # 不同值个数（统计信息）
    null_ratio: float
    avg_width: int         # 字节

@dataclass
class Table:
    name: str
    rows: int
    row_bytes: int
    fill_factor: float = 0.72
    columns: dict[str, Column] = field(default_factory=dict)
    indexes: dict[str, "Index"] = field(default_factory=dict)
    write_qps: float = 0.0

@dataclass
class Index:
    name: str
    columns: list[str]
    unique: bool = False
    fill_factor: float = 0.90
    bloat: float = 0.0             # 0~0.5，删除造成的空洞
    built: bool = True

@dataclass
class DbState:
    tables: dict[str, Table]
    buffer_pool_gb: float
    disk_capacity_gb: float
    disk_iops_limit: int
    max_connections: int
    version: str                   # "8.0.36"

@dataclass
class CacheState:
    enabled: bool = False
    memory_mb: int = 512
    policies: dict[str, "CachePolicy"] = field(default_factory=dict)

@dataclass
class CachePolicy:
    endpoint: str
    ttl_s: int
    invalidate_on_write: bool = False

@dataclass
class AppState:
    servers: int = 6
    workers_per_server: int = 8
    pool_size: int = 40
    pool_timeout_ms: int = 2000
    request_timeout_ms: int = 1000
```

### 4.3 状态约束

- **所有金额用整数（分）**，不用浮点。
- **所有时间用整数毫秒**（微秒级常数在内部用 float，出口取整）。
- 比率类字段统一 `float`，每步结束 `round(x, 4)`。
- 不允许 `None` 参与算术；缺省值必须有明确语义（如"未知"用 `-1`）。
- 状态里**不存派生值**（p95、命中率、IOPS 都在结算时算出来，存进 `history` 快照，不回写 `tech`）。避免"脏状态"。

---

## 5. 时间与确定性

### 5.1 时间轴

```
周 w = 0, 1, 2, ...
年 = w // 48 + 1
月 = (w % 48) // 4 + 1
月内周 = w % 4 + 1
```

固定 48 周/年、4 周/月。**不实现真实日历**：没有闰年、没有 31 号，少一类 bug。

### 5.2 确定性随机

不用全局 `random`。每次需要随机数时，用 `(种子, 周, 用途)` 派生一个独立流：

```python
# core/rng.py
import hashlib, random

def rng(seed: int, week: int, purpose: str) -> random.Random:
    key = f"{seed}|{week}|{purpose}".encode()
    digest = hashlib.sha256(key).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))
```

好处：

- 加一个新随机事件，不会改变旧事件的随机序列（不会因为改代码导致存档漂移）。
- 同一周同一用途，无论调用多少次，结果一致。
- 可以单独重放某一周的某个用途。

**禁止**：`time.time()`、`datetime.now()`、`random.random()`、`uuid4()`、依赖 `set` 的遍历顺序、依赖 `dict` 插入顺序做数值累加（浮点累加顺序会影响末位）。

### 5.3 纯函数与结算顺序

`step` 内部严格按固定顺序执行，顺序本身就是规则的一部分：

```
P1  基线模拟（流量 / 数据库 / 应用）
P2  事故检测（从指标生成根事件）
P3  链式解析（事件总线，含状态改动）
P4  脏重算（只重算被标记的聚合，最多 1 遍）
P5  组织 / 经济 / 绩效
P6  快照与事件 DAG 落盘
```

P4 是关键：锁表改变了本周错误率，重算后 p95、SLA、工单数当周就变，不用等下周。

每一步只能读"上一步的输出"，不能反过来。P1 的查询成本依赖磁盘排队，用**固定 3 轮定点迭代**解决：先按 0 等待算一遍 IOPS，算出排队延迟，再重算成本（最多 3 轮，或误差 < 1% 停止）。迭代次数固定，结果仍然确定。

> 周内按天分段、事件总线与传播上限见 [DESIGN_ADDENDUM.md](DESIGN_ADDENDUM.md) §2。

---

## 6. 真实实验层（SQLite Lab）

这是"不拿假数据糊弄人"的关键。职责边界必须先说清楚：

| 做什么 | 不做什么 |
|---|---|
| 用真实 SQLite 执行玩家写的 SQL | 不用它给生产仿真提供数值 |
| 展示真实的执行计划、扫描行数、耗时 | 不假装 SQLite 等于 MySQL |
| 让玩家验证自己的心智模型 | 不做校准基准（那是 MySQL 的事） |

### 6.1 副本

- 用与游戏相同的 schema 和数据分布生成 `replica.db`。
- 规模可配：默认 20 万订单（SQLite 上毫秒级响应，但足以体现索引差异）。
- 首次生成后缓存，哈希校验，schema 变了才重建。
- 数据生成器与 `core` 共用同一份 `content/schema.yaml`，避免两边漂移。

### 6.2 安全边界

```python
conn = sqlite3.connect("file:replica.db?mode=ro", uri=True)
conn.execute("PRAGMA query_only = ON")
```

- 只允许单条 `SELECT`（MVP）。用 `sqlite3.complete_statement` 和关键字白名单双重校验。
- 拒绝 `ATTACH`、`DETACH`、`PRAGMA`、`VACUUM`、`load_extension`。
- 用 `set_progress_handler` 限制 VM 步数（如 5,000,000 步），超时中断。
- 结果最多返回 100 行。
- 在子进程里执行（`subprocess` + 超时 3s），即使被绕过也不影响主进程。

### 6.3 输出

```json
{
  "ok": true,
  "columns": ["id", "status", "created_at"],
  "rows": [[...]],
  "row_count": 20,
  "plan": [
    {"id": 2, "parent": 0, "detail": "SEARCH orders USING INDEX idx_user_created (user_id=?)"},
    {"id": 3, "parent": 0, "detail": "USE TEMP B-TREE FOR ORDER BY"}
  ],
  "elapsed_ms": {"median": 0.42, "runs": [0.39, 0.42, 0.51]},
  "warnings": ["查询未使用索引，扫描了全表"]
}
```

`warnings` 由**规则**产生（解析 `plan` 文本），不是模型。

### 6.4 与生产模型的关系

玩家在实验台上验证通过后，点"发布"，把结构化变更写进生产状态：

```json
{"type": "add_index", "table": "orders", "columns": ["user_id", "created_at"]}
{"type": "rewrite_query", "query_id": "admin_daily_report", "sql": "SELECT ... WHERE created_at >= ? AND created_at < ?"}
```

生产侧不执行这条 SQL，而是根据变更类型更新模型参数。**两边的一致性靠"同样的意图、不同的执行"来保证**，并在文档里明确告诉玩家：实验台是 SQLite，生产是 MySQL 语义的模型，结论可迁移，绝对数值不可迁移。

---

## 7. 校准（防止"AI 拍脑袋"）

### 7.1 流程

```
tools/bench_mysql.py
  → 启动 mysql:8.0（Docker，可选）
  → 生成 10万 / 100万 / 500万 三档数据
  → 跑 8 条基准查询 × 5 次取中位数
  → 最小二乘拟合 c_row / c_page / c_io / index_height
  → 写出 content/calibration.json（含机器信息、日期、版本）
```

### 7.2 校准文件

```json
{
  "version": "2026-11-18",
  "environment": {"cpu": "R7-5800H", "disk": "NVMe", "mysql": "8.0.36"},
  "constants": {
    "c_row_us": 0.061,
    "c_page_us": 0.19,
    "c_io_rand_ms": 0.105,
    "c_io_seq_ms": 0.128,
    "index_fanout": 1170
  },
  "fit": {"queries": 8, "median_error_pct": 11.2, "max_error_pct": 34.7}
}
```

### 7.3 守门测试

```python
def test_model_matches_benchmark():
    if not CALIBRATION.exists():
        pytest.skip("未校准，使用默认常数")
    for case in CALIBRATION_BENCHMARKS:
        est = estimate(case.query, case.dataset)
        assert abs(est - case.measured) / case.measured < 0.40
```

误差 40% 是诚实的：游戏不需要精确到毫秒，只需要**方向和数量级正确**。超过 40% 说明模型有结构性错误，必须修，而不是调参掩盖。

---

## 8. 事件引擎

### 8.1 条件用数据描述，不用 `eval`

```yaml
- id: E01
  title: "大屏又卡了"
  priority: 2
  cooldown_weeks: 4
  max_per_game: 3
  when:
    all:
      - {field: "metrics.dashboard_p95_ms", op: "gte", value: 800, weeks: 2}
      - {field: "org.manager.trust", op: "lt", value: 60}
  weight: 1.0
  choices:
    - id: cache
      label: "给大屏加 30 秒缓存"
      cost: {ap: 1, cash: 0}
      effects: [{set: "tech.cache.policies.admin_report", value: {ttl_s: 30, invalidate_on_write: false}}]
    - id: rewrite
      label: "改写 SQL 为区间查询"
      requires: {skill: {sql: 30}}
      cost: {ap: 2}
      effects: [{action: "rewrite_query", query_id: "admin_daily_report"}]
```

### 8.2 判定顺序

1. 构建上下文（把状态拍平成 `{"metrics.dashboard_p95_ms": 852, ...}` 的字典）。
2. 按 `id` 排序遍历候选事件，跳过冷却中/已达上限的。
3. 计算权重：`base × Π(条件权重修正)`。
4. 用 `rng(seed, week, "events")` 抽 1 个根事件（链内子事件不参与抽样，由父事件直接触发）。
5. 根事件进入事件总线，按 `(day, priority, seq)` 解析并传播子事件，深度 ≤ 3、每周 ≤ 6 条。
6. 记录候选列表、权重、命中结果与因果链到 `event_log`。

`event_log` 是给玩家看的"为什么触发"，也是给测试用的断言依据。

---

## 9. AI 叙事层

### 9.1 界面

```python
class NarrativeProvider(Protocol):
    def monthly_report(self, facts: dict) -> str: ...
    def incident_flavor(self, facts: dict) -> str: ...
```

- `TemplateProvider`：默认，纯字符串模板，零成本、零延迟、永远可用。
- `LLMProvider`：可选，需要玩家在设置里填 key 并勾选开启。

### 9.2 事实包

```json
{
  "month": 3,
  "facts": {
    "revenue": {"value": 431200, "unit": "CNY", "label": "本月收入"},
    "p95": {"value": 412, "unit": "ms", "label": "接口 p95"},
    "incidents": ["E01"]
  },
  "allowed_claims": ["revenue_up", "latency_bad", "boss_angry"]
}
```

提示词只允许使用 `facts` 里的数字；生成后用正则提取所有数字，凡是不在 `facts["value"]` 集合里的，直接废弃，回退模板。**AI 永远不能引入新数字。**

### 9.3 成本控制

- 每月最多 1 次（月报），事件文案最多 2 次。
- 超时 8 秒直接放弃。
- 结果按 `(month, template_hash)` 缓存。
- 关闭 AI 时，所有文本由模板生成，游戏完整性不受影响。

---

## 10. 存档、回放、导出

### 10.1 存档

```json
{
  "save_version": 1,
  "state_version": 3,
  "rules_version": "0.1.0",
  "seed": 20261118,
  "week": 12,
  "state": { ... },
  "actions": [ ... ],
  "created_at": "2026-11-18T10:00:00+08:00"
}
```

- SQLite 表 `saves(id, slot, name, json, created_at)`。
- 每次推进自动存"自动存档"槽位。
- 读档时若 `rules_version` 不匹配，提示"规则已更新，旧存档可能表现不同"，但仍允许读取（尽量兼容）。

### 10.2 回放

因为 `step` 是纯函数，只要有 `seed + actions` 就能重放：

```python
def replay(seed, actions, upto_week):
    s = new_game(seed)
    for a in actions:
        if a.week > upto_week: break
        s = step(s, a.actions)
    return s

assert hash_state(replay(...)) == hash_state(loaded_state)
```

这个测试同时验证了"读档不漂移"和"确定性"。

### 10.3 导出

`POST /api/export` 输出：
- `state.json`（完整状态，调试用）
- `report.md`（人话版周报/月报）
- `events.csv`（事件流水）
- `metrics.csv`（每周指标，可用 Excel 画图）

---

## 11. HTTP API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 主界面（Jinja 骨架） |
| GET | `/api/state` | 当前状态 + 本周可用操作 |
| POST | `/api/action` | 提交一个操作 `{type, params}` |
| POST | `/api/advance` | 推进 N 周 `{weeks}`，返回每周快照 |
| POST | `/api/event/<id>/choose` | 事件选项 |
| GET | `/api/panels/<name>` | 面板数据（按需加载） |
| GET | `/api/lab/queries` | 实验台查询列表 |
| POST | `/api/lab/query` | 执行 SQL（只读） |
| POST | `/api/lab/deploy` | 把实验结论发布到生产 |
| POST | `/api/save` / `/api/load` | 存档/读档 |
| GET | `/api/export` | 导出 |
| POST | `/api/narrative/month` | 生成月报文案（可选 AI） |

约定：所有响应 `{"ok": true, "data": ...}` 或 `{"ok": false, "error": {"code", "message"}}`。错误码稳定，前端不靠文案判断。

---

## 12. 前端

### 12.1 结构

```
static/
  css/app.css          # 全部样式，一个文件，CSS 变量
  js/
    main.js            # 启动、路由、事件绑定
    store.js           # 状态缓存 + 订阅
    fmt.js             # 数字/时间/货币格式化
    charts.js          # Chart.js 封装
    panels/
      overview.js
      database.js
      architecture.js
      lab.js
      tasks.js
      team.js
      finance.js
      debt.js
      events.js
      docs.js
      career.js
  vendor/chart.umd.min.js
```

### 12.2 交互

- 页面加载：Jinja 渲出骨架 + 首屏数据（避免白屏）。
- 之后所有更新走 `fetch`，只重渲染当前激活的面板。
- 操作提交后：先乐观更新按钮状态，再以服务端返回的 `state` 为准。
- 无路由框架：`location.hash` 记录当前面板，刷新后保持。

### 12.3 性能

- 首屏 < 300KB（含 Chart.js）。
- 单次操作往返 < 100ms（本地）。
- 图表数据超过 100 点时降采样。

---

## 13. 测试策略

| 层级 | 内容 | 数量（目标） |
|---|---|---|
| 单元 | 排队公式、成本公式、优化器选择、缓存命中 | 25+ |
| 属性 | 不变量（见下） | 15+ |
| 黄金用例 | 固定种子跑 24 周，状态哈希固定 | 3 |
| 回放 | seed + actions 重放 == 存档 | 1 |
| API | Flask test client 冒烟 | 13 |
| 校准 | 与真实 MySQL 基准误差 < 40% | 1（可选跳过） |
| 前端 | 手动清单（MVP 不做 E2E） | — |

### 不变量（属性测试）

```python
# 加索引不会让"匹配该索引"的查询变慢
assert cost_after <= cost_before * 1.0

# 加索引一定不会让磁盘变小
assert disk_after >= disk_before

# 加索引不会让写入变快
assert write_after >= write_before

# 任何情况下现金不会凭空增加（除收入和融资）
# 任何指标不出现 NaN / inf
# 可用性永远在 [0, 1]
# 磁盘使用率单调不减（除非执行清理）
```

### 黄金用例

```python
def test_golden_24_weeks():
    s = new_game(seed=42, difficulty="normal")
    s = run_script(s, SEED_42_ACTIONS)
    assert hash_state(s) == "sha256:9f2c..."
```

改规则导致哈希变化是**预期行为**，此时需要人工确认并更新基线，而不是无脑改测试。

---

## 14. 目录结构

```
junior-engineer/
├── app.py                    # Flask 入口（薄）
├── sim_cli.py                # 无界面运行：new / sim / replay / export
├── core/
│   ├── model/
│   │   ├── state.py
│   │   └── schema.py
│   ├── rules/
│   │   ├── traffic.py
│   │   ├── query.py
│   │   ├── optimizer.py
│   │   ├── storage.py
│   │   ├── cache.py
│   │   ├── pool.py
│   │   ├── app_queue.py
│   │   ├── availability.py
│   │   ├── debt.py
│   │   ├── org.py
│   │   ├── economy.py
│   │   ├── career.py
│   │   └── events.py
│   ├── engine.py
│   └── rng.py
├── content/
│   ├── schema.yaml
│   ├── queries.yaml
│   ├── actions.yaml
│   ├── events.yaml
│   ├── docs/*.md
│   └── calibration.json
├── services/
│   ├── save.py
│   ├── replay.py
│   ├── narrative.py
│   └── export.py
├── lab/
│   ├── replica.py
│   ├── sandbox.py
│   └── explain.py
├── web/
│   ├── routes.py
│   ├── api.py
│   ├── templates/
│   └── static/
├── tools/
│   ├── bench_mysql.py
│   └── refcalc.py
├── tests/
└── saves/
```

---

## 15. 性能与部署

- 单次 `step` < 5ms（24 周完整模拟 < 150ms）。
- 状态 JSON < 300KB。
- 存档 SQLite 单文件，随项目走，不需要服务。
- 运行：`py -3 app.py`，打开 `http://127.0.0.1:5000`。不需要 Docker、不需要 MySQL、不需要联网。
- 若将来接 MySQL 校准，只在 `tools/` 里，不影响运行时。

---

## 16. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 模型太复杂，玩家看不懂数值 | 变成黑箱，失去策略性 | 每个数字可点开看公式；结算明细可展开 |
| 模型太简单，出现单一最优解 | 玩两小时就腻 | 多瓶颈轮换；加入组织与政治约束；定期做"无最优解"实验 |
| 真实实验台与生产模型不一致 | 玩家学了 SQL 但在游戏里"不管用" | UI 明确标注差异；实验台只验证"访问路径"这一层 |
| SQLite 与 MySQL 优化器差异 | 玩家在实验台学到的结论迁移失败 | 实验台只讲通用原理（sargable、最左前缀、覆盖索引）；数值不迁移 |
| 范围膨胀 | 做不完 | MVP 清单写死；任何新增先进"第二版" |
| 确定性被破坏 | 无法复算、无法回放 | hash RNG + 黄金用例 + 禁用时间/全局随机 |
| AI 编造数字 | 破坏可信度 | 事实包 + 数字校验 + 模板回退 |

---

## 17. 关键实现顺序（对应 MVP_PLAN）

1. 数据类 + 新游戏初始化 + 存档（能存能读）
2. 流量 + 查询成本 + 优化器（能在 CLI 里看到 p95）
3. 引擎 step + 月度结算（能跑 24 周）
4. 事件 + 动作（能操作、有后果）
5. API + 前端骨架（能点）
6. 面板 + 图表（能看懂）
7. 实验台（能写 SQL）
8. 测试 + 平衡（能玩）

---

*下一步：等你确认 [MVP_PLAN.md](MVP_PLAN.md) 末尾的 5 个决策点，再开始写代码。*
