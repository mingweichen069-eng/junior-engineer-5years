# 代码审阅与协作建议

> 目的：在动手写游戏之前，先搞清楚你现在的水平，好决定"代码该写到什么程度、注释写到什么程度、哪些东西我应该替你搭好"。
> 我读了 `E:\pycharm文件`（主要）和 `E:\PythonProject`（辅助），共 9 个 Python 文件、5 个模板、1 个 Dockerfile 和 git 历史。

---

## 1. 一句话结论

**你已经越过了"跟着教程抄"的阶段，能独立把一个想法做成一个分层、能跑、有数据库的 Web 应用；但还没有进入"工程"阶段——没有测试、没有依赖管理、没有错误处理，而且有几处 bug 说明部分路径还没被真正跑通过。**

用游戏的话说：你是 **Layer 2 熟练、Layer 3 入门、Layer 4 有潜力但缺练** 的玩家。这也是为什么我在设计里专门加了"老手模式"——你不该被迫从头点按钮。

---

## 2. 能力画像

### 2.1 已经具备的

| 能力 | 证据 | 评价 |
|---|---|---|
| Python 基础 | `entity.py` 的类、`to_dict/from_dict`、`JsonManager` 泛型式复用 | 扎实，能写出可读的代码 |
| 分层意识 | `entity / dao / model / app` 四层分离，`dao/__init__.py` 统一导出 | **超出初学者平均水平**，很多人写到第三个项目才有这个意识 |
| Flask CRUD | 路由、模板、表单、重定向、`url_for` | 够用，能独立完成 |
| MySQL | 建表、外键、`ON DELETE CASCADE`、`DictCursor`、参数化查询 | 基础扎实，且**没有拼接 SQL**，这点很好 |
| 文件/编码 | `encoding="utf-8"`、`ensure_ascii=False`、`pathlib`、`tempfile` | 有正确的习惯 |
| 容器概念 | `judge_runner.py` 里 `--network none`、`--memory 128m`、`--pids-limit`、`:ro` 挂载 | **这是亮点**。你知道"跑别人的代码要隔离"，这个直觉比语法重要 |
| 工程直觉 | git 提交信息、"数据库化之前完整版本"这种留后路的做法 | 有版本意识 |

### 2.2 还缺的

| 缺口 | 证据 | 影响 |
|---|---|---|
| 自动化测试 | 全项目 0 个测试文件 | 上面几个 bug 本来 10 行测试就能拦住 |
| 依赖管理 | 没有 `requirements.txt` / `pyproject.toml` | 换台机器就跑不起来 |
| 连接管理 | `database.py` 全局单连接，每个 DAO 方法自己 `commit` | 并发下会出错，见 §3 |
| 错误处理 | 路由里没有 try/except，DB 挂了直接 500 白页 | 用户体验和维护性 |
| 索引/性能 | 代码里没有任何索引设计、EXPLAIN、慢查询相关 | 正好是这个游戏要教的东西 |
| 前端 | 模板无 CSS、无布局、无交互 | 不影响学习，但限制了成品感 |
| 类型注解 / dataclass | 全是手写 `__init__` + `to_dict` | `entity.py` 里 188 行，用 dataclass 能压到 40 行 |

---

## 3. 具体问题（按严重程度）

### 🔴 严重

**3.1 数据库 root 密码明文写死，且已进入 git 历史**

```python
# database.py:3
return pymysql.connect(host="localhost", user="root", passwd="20040518mw", ...)
```

这个文件在 git 里（`git log -- database.py` 有记录，提交 `d097b84` 之后一直存在）。即使你现在删掉，历史里仍然有。

**建议**：立刻改掉本地 MySQL 的 root 密码（这串看起来像生日，很可能你别的账号也在用）。以后用环境变量：

```python
import os
conn = pymysql.connect(
    host=os.getenv("DB_HOST", "127.0.0.1"),
    user=os.getenv("DB_USER", "app"),
    password=os.environ["DB_PASSWORD"],   # 不给默认值，缺失就报错
    database=os.getenv("DB_NAME", "leetcode_system"),
)
```

**3.2 建表语句和 INSERT 语句对不上**

```python
# database.py:10-17
CREATE TABLE problem(
    problem_id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(100), difficulty VARCHAR(20),
    SN INT, tags VARCHAR(100)
)   -- 只有 5 列

# dao/problem_dao.py:13
INSERT INTO problem(title,difficulty,SN,tags,correct_code,correct_description)
VALUES(...)   -- 插 6 列
```

`correct_code` 和 `correct_description` 在 `CREATE TABLE` 里不存在。这段代码要么从没在干净库上跑过，要么你手工 `ALTER` 过而没同步回 `create_tables`。两种都是隐患。

**3.3 全局连接 + 多线程**

```python
# database.py:35
conn = get_conn()          # import 时就连
# 之后所有 DAO 共用一个 conn
```

Flask 开发服务器默认 `threaded=True`，两个请求同时用一个 pymysql 连接，会出现 `Packet sequence number wrong` 或事务互相污染。而且连接只连不关，MySQL 8 小时空闲会断，之后所有请求报 `MySQL server has gone away`。

**建议**：每个请求一个连接（`before_request` / `teardown_request`），或直接用连接池。

### 🟡 中等

**3.4 大小写陷阱**

```python
# app.py:41
return render_template("check.html", problem=problem)
```

实际文件是 `templates/Check.html`。Windows 不区分大小写，**Linux 会 500**。你已经在用 Docker（Linux），这个坑迟早踩。

**3.5 `load_save` 的键名对不上**

```python
# E:\PythonProject\app.py:37
name, info["stu_class"], info["stu_score"]
# 但 to_dict 存的是 "class" / "score"
```

读档时如果文件存在，会 `KeyError`。因为 `except` 只捕获了 `FileNotFoundError`，所以这个错误会直接冒出来。

**3.6 `JsonManager` 的隐式假设**

`save` 里靠 `isinstance(value, list)` 判断结构，`load` 里假设 key 一定能 `int()`。现在能用，但加一个新类型就会静默出错。

### 🟢 小问题

- `app.py:78` `debug=True`：开发可以，别带到任何能被访问的环境。
- 没有 `requirements.txt`；`pymysql`、`flask` 版本未锁定。
- `docker_demo/Dockerfile` 里 `COPY user_code.py`，但 `runner.py` 实际是把代码写到临时目录再挂载——Dockerfile 和 runner 的逻辑已经脱节。
- 模板里 `<!DOCTYPE html>` 下面没有 `<html>`，浏览器能容错，但不规范。

---

## 4. 亮点（不是客套）

`judge_runner.py` 这一段，含金量比整个 CRUD 项目都高：

```python
command = ["docker","run","--rm","-i",
           "--network","none","--memory","128m","--cpus","0.5",
           "--pids-limit","64","-v",f"{temp_dir}:/app:ro", ...]
```

你知道要断网、限内存、限 CPU、限进程数、只读挂载、超时、区分 `returncode 124/125`、从 stdout 里抽结构化结果。这是**系统设计**的直觉，不是教程能教出来的。很多人写三年 CRUD 都不会想到 `--pids-limit`。

这个能力对本项目很关键：游戏里的 SQL 实验台要做的正是同一件事（只读、限步数、子进程隔离、结构化输出）。你已经证明你能理解这套东西。

---

## 5. 这对游戏开发意味着什么

| 决定 | 原因 |
|---|---|
| 我会把 `core/` 写成纯函数 + 完整类型注解 | 你可以逐函数读，不需要在脑子里跑整个应用 |
| 每个里程碑都带测试，且测试本身就是文档 | 补上你最大的缺口，同时让你敢改 |
| 不用 React / 不用构建工具 | 你的前端经验为零，Jinja + 原生 JS 学习曲线最平 |
| 存档用 SQLite 而不是 JSON | 你已经有 pymysql 经验，迁移成本低；且能避开你 `JsonManager` 里的坑 |
| 规则常数集中在 `constants.py` | 方便你自己调参、做实验 |
| 不引入 ORM | 你现在的 SQL 水平正好够用，ORM 会遮住你要学的东西 |

**你需要补的三件事**（都很小，但对这个项目很关键）：

1. **pytest**：会写 `assert f(2) == 4` 就够了，剩下的边做边学。
2. **`@dataclass`**：`entity.py` 那 188 行，用 dataclass 大概 40 行，而且自带 `==`、`repr`，测试好用。
3. **连接的生命周期**：理解"连接是有状态的资源，不是随手 new 的东西"。这是从"能跑"到"能扛"的分水岭。

---

## 6. 我建议你先做的三件小事

在游戏开工前，如果愿意花 1 小时：

1. **改掉那个 MySQL root 密码**，并给项目建一个专用账号（只授权 `leetcode_system` 库）。
2. **补一个 `requirements.txt`**：`flask`、`pymysql`、`pytest`。
3. **写第一个测试**：

```python
# tests/test_smoke.py
from app import app

def test_index_ok():
    client = app.test_client()
    assert client.get("/").status_code == 200
```

跑通 `pytest` 一次，你就有了"改代码不慌"的底气。

---

## 7. 你在游戏里大概是什么水平

按 [GAME_DESIGN.md](GAME_DESIGN.md) 的四层模型：

| 层 | 你的状态 |
|---|---|
| L1 自然语言目标 | 没问题，你已经在做"把需求变成表结构"的事 |
| L2 组件操作 | 熟练。加索引、加缓存对你不是黑箱 |
| L3 参数与结构 | 入门。你知道索引是什么，但 `(a,b)` 和 `(b,a)` 的区别、TTL 与一致性的取舍，需要练 |
| L4 真实 SQL | 有基础。你能写 CRUD，但 `EXPLAIN`、`rows examined`、`type=ALL` 这套诊断语言还没建立 |

**建议开局选"老手模式"**：跳过引导，SQL 技能初始 55，直接面对真实问题。你会在一两个小时内碰到自己不会的东西（大概率是连接池和缓存一致性），那才是这个游戏对你真正有价值的部分。

---

*配套：[GAME_DESIGN.md](GAME_DESIGN.md) · [TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md) · [SIMULATION_RULES.md](SIMULATION_RULES.md) · [MVP_PLAN.md](MVP_PLAN.md)*
