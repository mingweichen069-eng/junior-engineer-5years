"""考核：把「读条读到的知识」变成一次真的动手。

四类题，全部可自动判分，不靠选择题蒙：
- code   ：写一段真的能跑的 Python 业务函数，用一组输入输出用例判分（v0.9 主力题型）
- sql    ：在真实 SQLite 副本（20 万订单）上执行，校验执行计划 / 扫描行数 / 禁用的写法
- text   ：DDL、Redis、Nginx 这类「写出来才算会」的配置，用正则校验关键子句
- number ：容量与排队论计算题，允许容差

判题只认「跑得通 + 满足目标」，不认关键字堆砌：
SQL 题一定要真的执行成功，且目标条件由 EXPLAIN QUERY PLAN 判定；
编码题一定要真的调用玩家写的函数，逐个用例比对输出。

难度分层（v0.9）：简单 / 普通共用基础题库；困难 / 老手各有自己的加严变体，
但**任何难度都含编码题**，不会出现「某个难度全是填空选择」。

v1.1 阶段二：公开用例与隐藏回归用例分离。前端只能拿 public_cases；
后端 validate_regression() 同时跑 public_cases + hidden_cases，隐藏用例的
输入 / 期望值 / 异常文本都不进返回明细。执行环境补上墙钟超时、内存峰值、
递归深度与超大常量限制。这是生产回归，不是考试：跑通就部署，跑不通继续查。

v1.1.1：玩家代码不再在主进程 exec。主进程只做 AST 结构 / 能力预检，真正的
加载与调用放进独立子进程（core/rules/exam_worker.py），由父进程墙钟超时兜底；
POSIX 用 RLIMIT_AS / RLIMIT_CPU，Windows 尝试 Job Object 内存上限。这降低了
「恶意代码卡死 Flask」的风险，但仍不是经过审计的 Python 安全沙箱（同用户、
无文件系统隔离），边界见函数注释。
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import logging
import os
import re
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

CONTENT = Path(__file__).resolve().parents[2] / "content"

_CACHE: dict | None = None

# 执行计划的人话标签：报错时不能把正则原样丢给玩家
PLAN_LABEL = {
    "USING\\s+INDEX|COVERING\\s+INDEX": "需要真正用上索引（不能全表扫描）",
    "COVERING\\s+INDEX": "需要覆盖索引（不回表）",
    "TEMP B-TREE": "不要出现额外排序（TEMP B-TREE）",
}


def load(path: Path | None = None) -> dict:
    global _CACHE
    if path is not None:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    if _CACHE is None:
        _CACHE = json.loads((CONTENT / "exams.json").read_text(encoding="utf-8"))
    return _CACHE


def _exam_key(node: dict, state=None) -> str:
    """节点的 exam 字段可以是字符串，也可以是按难度映射的 dict。"""
    raw = node.get("exam") or ""
    if isinstance(raw, dict):
        diff = state.meta.difficulty if state is not None else "normal"
        raw = raw.get(diff) or raw.get("normal") or raw.get("easy") or ""
    return str(raw)


def task_for(node: dict, state=None) -> dict | None:
    """取当前难度下这道题的定义。

    - 节点 exam 支持 ``{"easy":"x","normal":"x","hard":"y","veteran":"z"}``
    - exams.json 的 ``difficulty_variants`` 再按难度整体替换题目
    - 不传 state 时按普通难度（测试夹具/工具脚本的兼容路径）
    """
    key = _exam_key(node, state)
    if not key:
        return None
    data = load()
    if state is not None:
        variants = (data.get("difficulty_variants") or {}).get(state.meta.difficulty) or {}
        key = variants.get(key, key)
    return data["tasks"].get(key)


def public_cases(task: dict | None) -> list[dict]:
    """前端可见的公开用例。兼容只有 cases 的旧题（v0.9 生成器 / 测试夹具）。"""
    if not task:
        return []
    return list(task.get("public_cases") or task.get("cases") or [])


def hidden_cases(task: dict | None) -> list[dict]:
    """只在后端执行的隐藏回归用例：边界条件、异常输入、不同数据规模。"""
    if not task:
        return []
    return list(task.get("hidden_cases") or [])


def regression_cases(task: dict | None) -> list[dict]:
    """判题用的全量用例 = 公开 + 隐藏。前端永远拿不到这个合并结果的内容。"""
    return public_cases(task) + hidden_cases(task)


def public_task(task: dict | None) -> dict | None:
    """给前端的题目：只有公开测试用例，不含隐藏用例、不含参考答案。"""
    if not task:
        return None
    cases = public_cases(task)
    return {
        "kind": task["kind"],
        "title": task.get("title", "考核"),
        "role": task.get("role", ""),
        "brief": task.get("brief", ""),
        "goal": task.get("goal", ""),
        "starter": task.get("starter", ""),
        "hint": task.get("hint", ""),
        "unit": task.get("unit", ""),
        "minutes": task.get("minutes", 8),
        "entry": task.get("entry", ""),
        # 公开用例（输入 + 期望输出），方便玩家自己先跑一遍；
        # 不公开隐藏回归用例，也不公开 solution
        "cases": [
            {"args": c.get("args", []), "kwargs": c.get("kwargs", {}), "expect": c.get("expect")}
            for c in cases
        ],
        "language": "python" if task["kind"] == "code" else "",
    }


# 编码题允许用到的内置函数；刻意不给 open / eval / exec / __import__ / getattr
SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bin": bin, "bool": bool, "chr": chr,
    "dict": dict, "divmod": divmod, "enumerate": enumerate, "filter": filter,
    "float": float, "format": format, "frozenset": frozenset, "hex": hex,
    "int": int, "isinstance": isinstance, "issubclass": issubclass, "len": len,
    "list": list, "map": map, "max": max, "min": min, "oct": oct, "ord": ord,
    "pow": pow, "range": range, "repr": repr, "reversed": reversed,
    "round": round, "set": set, "slice": slice, "sorted": sorted, "str": str,
    "sum": sum, "tuple": tuple, "zip": zip,
}
STEP_LIMIT = 20000

# v1.1 阶段二：生产回归的执行边界。单机轻量沙箱，不引入额外框架；
# 墙钟检查挂在已有的 step tracer 上（每 256 行看一次），用例之间再查一次。
WALL_LIMIT_SEC = 2.0
MEM_LIMIT_BYTES = 24 * 1024 * 1024
RECURSION_LIMIT = 160
MAX_INT_CONST = 1_000_000
TRACE_CHECK_EVERY = 256

# v1.1.1：玩家代码不再在主进程里执行。
# 主进程只做 AST 预检 + 起一个独立 Python 子进程；子进程负责真正的 exec / 调用，
# 由主进程用墙钟超时兜底，超时就终止子进程。这样即使子进程里出现 C 层死循环、
# 超大分配或直接崩溃，Flask 主服务也不会被拖住。
WORKER_MODULE = "core.rules.exam_worker"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXEC_TIMEOUT_GRACE_SEC = 2.0
PROC_MEM_LIMIT_BYTES = 256 * 1024 * 1024   # 子进程地址空间上限（POSIX rlimit / Windows Job Object）
_WIN_JOB_MEMORY = 0x00000100
_WIN_JOB_JOB_MEMORY = 0x00000200
_WIN_JOB_KILL_ON_CLOSE = 0x00002000

# 供诊断 / 测试读取：说明当前平台实际启用了哪些限制，避免把「尽力而为」说成完整沙箱。
SANDBOX_INFO: dict = {
    "mode": "subprocess",
    "worker": WORKER_MODULE,
    "platform": os.name,
    "os_memory_limit": os.name != "nt",   # POSIX 用 RLIMIT_AS；Windows 用 Job Object，附加失败则为 False
    "os_cpu_limit": os.name != "nt",      # POSIX 用 RLIMIT_CPU；Windows 只有墙钟超时
    "timeout_sec": EXEC_TIMEOUT_GRACE_SEC,
}


def human(pat: str) -> str:
    """把校验用的正则还原成能读的样子，别把 /\\s+/ 这种东西丢给玩家。"""
    out = pat
    for a, b in (("\\s+", " "), ("\\s*", ""), ("\\(", "("), ("\\)", ")"), ("\\b", ""), ("\\", "")):
        out = out.replace(a, b)
    return out.strip()


def _norm(text: str) -> str:
    return " ".join(str(text or "").split())


def _regex_ok(patterns: list[str], text: str) -> str:
    """返回第一个没命中的模式（空串表示全命中）。"""
    for pat in patterns:
        if not re.search(pat, text, re.I | re.S):
            return pat
    return ""


def _regex_hit(patterns: list[str], text: str) -> str:
    """返回第一个命中的模式（空串表示都没命中）。"""
    for pat in patterns:
        if re.search(pat, text, re.I | re.S):
            return pat
    return ""


def _check_sql(task: dict, answer: str) -> tuple[bool, str, dict]:
    from lab import sandbox

    detail: dict = {"kind": "sql"}
    res = sandbox.run(answer)
    if not res.get("ok"):
        detail["error"] = res.get("error", "")
        return False, f"SQL 跑不起来：{res.get('error', '未知错误')}", detail

    plan = res.get("plan") or []
    plan_text = " ".join(str(p.get("detail", "")) for p in plan).upper()
    detail.update({
        "plan": plan,
        "warnings": res.get("warnings", []),
        "row_count": res.get("row_count", 0),
        "elapsed_ms": res.get("elapsed_ms", 0),
        "columns": res.get("columns", []),
        "rows": (res.get("rows") or [])[:5],
    })

    checks = task.get("checks") or {}
    sql_text = _norm(answer)
    bad = _regex_hit(checks.get("forbid", []), sql_text)
    if bad:
        return False, f"用到了这次不允许的写法：{human(bad)}", detail
    miss = _regex_ok(checks.get("must", []), sql_text)
    if miss:
        return False, f"还缺关键条件：{human(miss)}", detail
    miss = _regex_ok(checks.get("require_plan", []), plan_text)
    if miss:
        return False, "执行计划没有达到目标：" + PLAN_LABEL.get(miss, "条件不满足"), detail
    bad = _regex_hit(checks.get("forbid_plan", []), plan_text)
    if bad:
        return False, "执行计划里出现了不该有的步骤：" + PLAN_LABEL.get(bad, bad), detail
    limit = checks.get("max_rows")
    if limit is not None and int(res.get("row_count", 0)) > int(limit):
        return False, f"返回了 {res.get('row_count')} 行，超过 {limit} 行：查询没有收敛", detail
    if checks.get("min_rows") is not None and int(res.get("row_count", 0)) < int(checks["min_rows"]):
        return False, f"只返回了 {res.get('row_count')} 行，结果不对", detail
    return True, "通过", detail


def _check_text(task: dict, answer: str) -> tuple[bool, str, dict]:
    checks = task.get("checks") or {}
    text = _norm(answer)
    detail = {"kind": "text", "length": len(text)}
    if len(text) < int(task.get("min_length", 8)):
        return False, "答卷太短了：把完整语句写出来", detail
    bad = _regex_hit(checks.get("forbid", []), text)
    if bad:
        return False, f"出现了不该有的写法：{human(bad)}", detail
    miss = _regex_ok(checks.get("must", []), text)
    if miss:
        return False, f"还缺关键部分：{human(miss)}", detail
    return True, "通过", detail


def _check_number(task: dict, answer: str) -> tuple[bool, str, dict]:
    nums = re.findall(r"-?\d+(?:\.\d+)?", str(answer or ""))
    detail = {"kind": "number", "parsed": nums[-1:]}
    if not nums:
        return False, "没读到数字：直接填数值就行", detail
    # 取最后一个数：玩家常在过程里写中间值，答案一般落在句末
    got = float(nums[-1])
    want = float(task["answer"])
    tol = float(task.get("tolerance", max(0.5, abs(want) * 0.03)))
    detail.update({"answer": want, "tolerance": tol, "got": got, "unit": task.get("unit", "")})
    if abs(got - want) > tol:
        return False, f"算错了：{got:g} 与正确值差得比较多（容差 ±{tol:g}）", detail
    return True, "通过", detail


class _StepLimit(Exception):
    pass


class _WallLimit(Exception):
    pass


class _MemoryLimit(Exception):
    pass


def _canon(v):
    """把返回值归一化，避免 tuple / list 之类只在类型上不同的假失败。"""
    if isinstance(v, (tuple, list)):
        return [_canon(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _canon(x) for k, x in v.items()}
    if isinstance(v, (set, frozenset)):
        return sorted((_canon(x) for x in v), key=repr)
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _safe_repr(v, limit: int = 90) -> str:
    try:
        s = repr(_canon(v))
    except Exception:
        s = "<无法展示>"
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _step_tracer(frame, event, arg):
    if event == "line":
        _step_tracer.count += 1
        if _step_tracer.count > _step_tracer.steps:
            raise _StepLimit()
        if _step_tracer.count % TRACE_CHECK_EVERY == 0:
            if time.monotonic() - _step_tracer.start > _step_tracer.wall:
                raise _WallLimit()
            if _step_tracer.tracing:
                peak = tracemalloc.get_traced_memory()[1]
                if peak > _step_tracer.mem:
                    raise _MemoryLimit()
    return _step_tracer


_step_tracer.count = 0
_step_tracer.start = 0.0
_step_tracer.tracing = False
_step_tracer.steps = STEP_LIMIT
_step_tracer.wall = WALL_LIMIT_SEC
_step_tracer.mem = MEM_LIMIT_BYTES


# ---------------- 代码结构预检查（v1.1.1 P0） ----------------

class _StructureError(Exception):
    def __init__(self, message: str, lineno: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.lineno = lineno


def _fail(node, message: str) -> None:
    raise _StructureError(message, getattr(node, "lineno", 1))


def _is_docstring(stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _literal_ok(node) -> bool:
    """只允许纯字面量结构：不会触发调用、属性访问、名字查找或大块运算。"""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_literal_ok(x) for x in node.elts)
    if isinstance(node, ast.Dict):
        return all(k is None or _literal_ok(k) for k in node.keys) and all(
            _literal_ok(v) for v in node.values
        )
    if isinstance(node, ast.UnaryOp):
        return isinstance(node.op, (ast.UAdd, ast.USub, ast.Invert, ast.Not)) and _literal_ok(
            node.operand
        )
    return False


def _annotation_ok(node) -> bool:
    # Python 3.14 的注解是惰性求值，但这里仍然只放行最简单形式，避免未来语义变化。
    if isinstance(node, (ast.Name, ast.Constant)):
        return True
    if isinstance(node, ast.Subscript):
        return _annotation_ok(node.value) and _annotation_ok(node.slice)
    if isinstance(node, ast.Tuple):
        return all(_annotation_ok(x) for x in node.elts)
    return False


def _check_function(node) -> None:
    if node.decorator_list:
        _fail(node, "不允许使用装饰器：装饰器会在加载阶段执行任意代码")
    defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]
    for default in defaults:
        if not _literal_ok(default):
            _fail(default, "函数默认参数只能是字面量，不能是函数调用或变量")
    args = (
        list(node.args.posonlyargs)
        + list(node.args.args)
        + list(node.args.kwonlyargs)
        + [a for a in (node.args.vararg, node.args.kwarg) if a is not None]
    )
    for arg in args:
        if arg.annotation is not None and not _annotation_ok(arg.annotation):
            _fail(arg.annotation, "类型注解只能是简单名字或字面量")
    if node.returns is not None and not _annotation_ok(node.returns):
        _fail(node.returns, "返回类型注解只能是简单名字或字面量")


def _assignment_ok(node) -> bool:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    if any(not isinstance(t, ast.Name) for t in targets):
        return False
    if isinstance(node, ast.AnnAssign) and node.annotation is not None:
        # 模块层上的注解在部分 Python 版本里是加载期求值的，必须一起检查。
        if not _annotation_ok(node.annotation):
            return False
    value = getattr(node, "value", None)
    return value is None or _literal_ok(value)


def validate_code_structure(code: str) -> tuple[bool, str]:
    """模块结构白名单：顶层只能有文档字符串、函数定义和安全字面量赋值。

    目的不是「过滤关键字」，而是保证 ``exec`` 阶段除了定义函数之外什么都不做：
    没有顶层循环 / 调用 / 分支 / 异常处理 / 上下文管理器，也就没有「限制尚未生效
    就已执行」的窗口。函数体不在这一步限制，它只会在受控的调用阶段执行。

    当前 24 道编码题全部是单函数实现，因此不开放 class 定义：类体在加载阶段
    就会执行，且类的构建依赖 ``__build_class__``（不在受限 builtins 里）。
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as ex:
        return False, f"语法错误：第 {ex.lineno} 行 {ex.msg}"

    blocked = [
        (ast.While, "模块顶层不允许 while 循环；请把循环写进函数里"),
        (ast.For, "模块顶层不允许 for 循环；请把循环写进函数里"),
        (ast.AsyncFor, "模块顶层不允许 async for 循环；请把循环写进函数里"),
        (ast.If, "模块顶层不允许 if 分支；请把逻辑写进函数里"),
        (ast.Try, "模块顶层不允许 try/except；请把异常处理写进函数里"),
        (ast.With, "模块顶层不允许 with 语句；请把上下文管理写进函数里"),
        (ast.AsyncWith, "模块顶层不允许 async with 语句"),
        (ast.Raise, "模块顶层不允许 raise"),
        (ast.Assert, "模块顶层不允许 assert"),
        (ast.Delete, "模块顶层不允许 del"),
        (ast.Import, "模块顶层不允许 import；回归环境不提供任何模块"),
        (ast.ImportFrom, "模块顶层不允许 import；回归环境不提供任何模块"),
        (ast.Global, "模块顶层不允许 global"),
        (ast.Nonlocal, "模块顶层不允许 nonlocal"),
        (ast.AsyncFunctionDef, "模块顶层不允许 async 函数"),
        (ast.ClassDef, "回归环境只支持函数定义，不支持 class"),
    ]
    if hasattr(ast, "Match"):
        blocked.append((ast.Match, "模块顶层不允许 match 分支"))

    try:
        for stmt in tree.body:
            if _is_docstring(stmt):
                continue
            if isinstance(stmt, ast.FunctionDef):
                _check_function(stmt)
                continue
            if isinstance(stmt, ast.Pass):
                continue
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and _assignment_ok(stmt):
                continue
            for typ, message in blocked:
                if isinstance(stmt, typ):
                    _fail(stmt, message)
            if isinstance(stmt, ast.Expr):
                _fail(stmt, "模块顶层不允许直接执行表达式或调用函数；请只定义函数")
            _fail(stmt, f"模块顶层不允许 {type(stmt).__name__}；请只定义函数")
    except _StructureError as ex:
        return False, f"第 {ex.lineno} 行：{ex.message}"
    return True, ""


# ---------------- 隔离执行：主进程只做检查，真正的 exec 在子进程 ----------------

def _base_detail(task: dict | None) -> dict:
    pub = public_cases(task)
    hid = hidden_cases(task)
    return {
        "kind": "code",
        "cases": [],
        "passed": 0,
        "total": len(pub) + len(hid),
        "public": {"total": len(pub), "passed": 0},
        "hidden": {"total": len(hid), "passed": 0},
    }


def _validate_answer(task: dict, text: str) -> tuple[bool, str, dict]:
    """主进程侧的纯检查：长度、关键字黑名单、AST 能力限制、模块结构。

    这些检查不执行玩家代码，因此可以在起子进程之前快速失败。
    子进程里还会再跑一遍（防御性重复），真正隔离的是子进程边界。
    """
    detail = _base_detail(task)
    if len(text) > 6000:
        return False, "答卷太长了（超过 6000 字符）", detail
    checks = task.get("checks") or {}
    bad = _regex_hit(checks.get("forbid", []), text)
    if bad:
        return False, f"用到了不允许的写法：{human(bad)}", detail
    miss = _regex_ok(checks.get("must", []), text)
    if miss:
        return False, f"还缺关键部分：{human(miss)}", detail

    try:
        tree = ast.parse(text)
    except SyntaxError as ex:
        detail["error"] = f"第 {ex.lineno} 行：{ex.msg}"
        return False, f"语法错误：第 {ex.lineno} 行 {ex.msg}", detail

    # 先看模块结构：顶层只允许定义与字面量，这一步能给出最直观的报错。
    ok, why = validate_code_structure(text)
    if not ok:
        detail["error"] = why
        return False, why, detail

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)):
            return False, "回归里不允许 import / global，直接把函数写完就行", detail
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return False, "不允许访问双下划线属性", detail
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            return False, "不允许访问双下划线名字", detail
        # str.format 的格式串可以在 C 层做属性 / 下标访问，是绕过 Attribute 检查的经典通道。
        # 只拦「格式串里出现 __」的用法，普通 "{}".format(x) 不受影响。
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "format":
            fmt = node.func.value
            if isinstance(fmt, ast.Constant) and isinstance(fmt.value, str) \
                    and "__" in fmt.value:
                return False, "不允许通过格式化字符串访问双下划线属性", detail
        if isinstance(node, ast.Constant) and isinstance(node.value, int) \
                and not isinstance(node.value, bool) and abs(node.value) > MAX_INT_CONST:
            return False, "代码里出现了过大的常量（超出回归环境限制）", detail
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow) \
                and isinstance(node.right, ast.Constant) \
                and isinstance(node.right.value, (int, float)) \
                and not isinstance(node.right.value, bool) and node.right.value > 8:
            return False, "代码里出现了过大的幂运算（超出回归环境限制）", detail

    return True, "", detail


def _worker_command() -> list[str]:
    return [sys.executable or "python", "-m", WORKER_MODULE]


def _worker_env() -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + prev if prev else "")
    return env


def _parse_worker_output(stdout: str) -> dict | None:
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and {"ok", "msg", "detail"} <= set(obj):
            return obj
    return None


def _attach_memory_job(pid: int, limit_bytes: int):
    """Windows：用 Job Object 给子进程加地址空间上限；失败则返回 None。

    这不是跨平台能力：Linux/macOS 用 worker 内的 RLIMIT_AS。
    附加失败时 SANDBOX_INFO["os_memory_limit"] 会是 False，调用方应如实记录。
    """
    if os.name != "nt":
        return None
    try:  # pragma: no cover - 仅在 Windows 上执行
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD
        ]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            _WIN_JOB_MEMORY | _WIN_JOB_JOB_MEMORY | _WIN_JOB_KILL_ON_CLOSE
        )
        info.ProcessMemoryLimit = int(limit_bytes)
        info.JobMemoryLimit = int(limit_bytes)
        ok = kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(info), ctypes.sizeof(info)  # JobObjectExtendedLimitInformation
        )
        if not ok:
            kernel32.CloseHandle(job)
            return None
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        handle = kernel32.OpenProcess(0x0100 | 0x0001, False, int(pid))
        if not handle:
            kernel32.CloseHandle(job)
            return None
        ok = kernel32.AssignProcessToJobObject(job, handle)
        kernel32.CloseHandle(handle)
        if not ok:
            kernel32.CloseHandle(job)
            return None
        return job
    except Exception:  # noqa: BLE001
        return None


def _close_job(job) -> None:
    if not job:
        return
    try:  # pragma: no cover - Windows
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle(job)
    except Exception:  # noqa: BLE001
        pass


def _task_payload(task: dict) -> dict:
    """只把判题需要的最小字段送进子进程，不带 solution。"""
    return {
        "kind": task.get("kind"),
        "entry": task.get("entry", ""),
        "checks": task.get("checks") or {},
        "public_cases": public_cases(task),
        "hidden_cases": hidden_cases(task),
        "cases": task.get("cases") or [],
    }


def _execute_isolated(task: dict, text: str, *, hide_hidden: bool = True):
    """在独立 Python 子进程里跑公开 + 隐藏用例，主进程只等结果 / 超时杀进程。"""
    payload = {
        "task": _task_payload(task),
        "answer": text,
        "limits": {
            "steps": STEP_LIMIT,
            "wall": WALL_LIMIT_SEC,
            "mem": MEM_LIMIT_BYTES,
            "recursion": RECURSION_LIMIT,
        },
        "hide_hidden": bool(hide_hidden),
    }
    timeout = max(3.0, min(20.0, float(WALL_LIMIT_SEC) * 4 + EXEC_TIMEOUT_GRACE_SEC))
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(
            _worker_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
            env=_worker_env(),
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
            start_new_session=(os.name != "nt"),
        )
    except Exception as ex:  # noqa: BLE001
        detail = _base_detail(task)
        detail["error"] = f"{type(ex).__name__}: {ex}"
        return False, "回归执行环境启动失败（未执行玩家代码）", detail

    job = _attach_memory_job(proc.pid, PROC_MEM_LIMIT_BYTES) if os.name == "nt" else None
    SANDBOX_INFO["os_memory_limit"] = bool(job) if os.name == "nt" else True
    SANDBOX_INFO["timeout_sec"] = timeout
    try:
        try:
            out, err = proc.communicate(json.dumps(payload, ensure_ascii=False), timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
            detail = _base_detail(task)
            detail["timeout"] = True
            return False, "回归执行超时（可能有死循环或超大计算）；执行进程已被终止", detail
    finally:
        _close_job(job)

    if proc.returncode != 0:
        # 子进程崩溃 / 被系统杀掉：不把 stderr 回给前端（可能夹带玩家代码内容）
        logging.getLogger(__name__).warning(
            "exam worker exited %s: %s", proc.returncode, (err or "")[:400]
        )
        detail = _base_detail(task)
        detail["worker_error"] = f"exit {proc.returncode}"
        return False, "回归执行进程异常退出（已隔离，服务未受影响）", detail

    parsed = _parse_worker_output(out)
    if not parsed:
        detail = _base_detail(task)
        detail["worker_error"] = "invalid worker output"
        return False, "回归执行返回异常（已隔离，服务未受影响）", detail

    ok = bool(parsed.get("ok"))
    msg = str(parsed.get("msg") or "回归未通过")
    detail = parsed.get("detail") if isinstance(parsed.get("detail"), dict) else _base_detail(task)
    return ok, msg, detail


def _check_code(task: dict, answer: str, *, hide_hidden: bool = True) -> tuple[bool, str, dict]:
    """生产回归入口：结构预检 → 子进程执行 → 结果反序列化。

    与 v1.1 的区别：
    - 玩家代码的 ``exec`` 不再发生在 Flask 进程里；
    - 主进程先做纯 AST 检查，非法结构直接拒绝（不会卡住任何进程）；
    - 子进程有墙钟超时，超时 / 崩溃由主进程处理，服务继续响应；
    - 公开用例与隐藏用例都在子进程里跑，隐藏用例明细仍然不回传。
    """
    text = str(answer or "")
    ok, msg, detail = _validate_answer(task, text)
    if not ok:
        return False, msg, detail
    return _execute_isolated(task, text, hide_hidden=hide_hidden)


@contextlib.contextmanager
def _silence():
    """把玩家代码的 stdout/stderr 吞掉，避免污染 worker 的 JSON 输出协议。"""
    buf = io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = buf
    try:
        yield
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def run_code_regression(
    task: dict,
    answer: str,
    limits: dict | None = None,
    *,
    hide_hidden: bool = True,
) -> tuple[bool, str, dict]:
    """真正执行玩家代码的函数：只在子进程（exam_worker）里调用。

    监控从 ``exec`` 之前就开始：settrace / tracemalloc / 递归限制都先于加载阶段
    生效，并在 finally 里恢复；进程本身由主进程超时兜底。
    """
    text = str(answer or "")
    limits = limits or {}
    ok, msg, detail = _validate_answer(task, text)
    if not ok:
        return False, msg, detail

    tree = ast.parse(text)
    entry = task.get("entry", "")
    pub = public_cases(task)
    hid = hidden_cases(task)
    cases = pub + hid
    n_pub = len(pub)
    detail = _base_detail(task)
    ns: dict = {"__builtins__": SAFE_BUILTINS, "__name__": "exam_answer"}

    old_trace = sys.gettrace()
    old_recursion = sys.getrecursionlimit()
    was_tracing = tracemalloc.is_tracing()
    _step_tracer.steps = int(limits.get("steps", STEP_LIMIT))
    _step_tracer.wall = float(limits.get("wall", WALL_LIMIT_SEC))
    _step_tracer.mem = int(limits.get("mem", MEM_LIMIT_BYTES))
    _step_tracer.tracing = True
    if not was_tracing:
        tracemalloc.start()
    try:
        sys.setrecursionlimit(int(limits.get("recursion", RECURSION_LIMIT)))
        # 加载阶段也必须在监控下：tracer 先装，再 exec。
        _step_tracer.count = 0
        _step_tracer.start = time.monotonic()
        sys.settrace(_step_tracer)
        try:
            with _silence():
                exec(compile(tree, "<regression>", "exec"), ns)
        except (_StepLimit, _WallLimit, _MemoryLimit, RecursionError):
            raise
        except Exception as ex:  # noqa: BLE001
            detail["error"] = str(ex)
            return False, f"代码加载失败：{ex}", detail
        finally:
            sys.settrace(old_trace)

        if time.monotonic() - _step_tracer.start > _step_tracer.wall:
            raise _WallLimit()
        if tracemalloc.get_traced_memory()[1] > _step_tracer.mem:
            raise _MemoryLimit()

        fn = ns.get(entry)
        if not callable(fn):
            return False, f"没找到函数 {entry}（注意函数名与缩进）", detail

        for i, case in enumerate(cases, 1):
            is_hidden = i > n_pub
            _step_tracer.count = 0
            _step_tracer.start = time.monotonic()
            sys.settrace(_step_tracer)
            try:
                with _silence():
                    got = fn(*(case.get("args") or []), **(case.get("kwargs") or {}))
                raised = None
            except (_StepLimit, _WallLimit, _MemoryLimit, RecursionError):
                raise
            except Exception as ex:  # noqa: BLE001
                got, raised = None, ex
            finally:
                sys.settrace(old_trace)

            expect = case.get("expect")
            if case.get("expect_raises"):
                ok = raised is not None
            elif raised is not None:
                ok = False
            else:
                ok = _canon(got) == _canon(expect)
            bucket = "hidden" if is_hidden else "public"
            if ok:
                detail["passed"] += 1
                detail[bucket]["passed"] += 1
            if not is_hidden:
                detail["cases"].append({
                    "i": i, "args": _safe_repr(case.get("args")),
                    "expect": _safe_repr("<异常输入>" if case.get("expect_raises") else expect),
                    "got": _safe_repr(got) if raised is None else f"<{type(raised).__name__}>",
                    "ok": bool(ok),
                })
            if not ok:
                if is_hidden and hide_hidden:
                    detail["hidden"]["failed"] = i - n_pub
                    detail.pop("error", None)   # 异常文本可能夹带隐藏输入，一并抹掉
                    return False, (
                        f"第 {i - n_pub} 个隐藏回归用例没过："
                        "去检查边界条件、异常输入和更大数据规模（输入不公开）。"
                    ), detail
                if raised is not None:
                    return False, f"运行时报错：{type(raised).__name__}: {raised}", detail
                return False, (
                    f"第 {i} 个用例没过：输入 {_safe_repr(case.get('args'))}，"
                    f"期望 {_safe_repr(expect)}，得到 {_safe_repr(got)}"
                ), detail
        # 最后一个用例若是纯 C 长调用，line 事件里查不到超时，这里兜底一次
        if time.monotonic() - _step_tracer.start > _step_tracer.wall:
            return False, "回归执行超时（可能有死循环或超大计算）", detail
    except _StepLimit:
        return False, "代码跑了太多步，可能有死循环", detail
    except _WallLimit:
        return False, "回归执行超时（可能有死循环或超大计算）", detail
    except _MemoryLimit:
        return False, "回归占用内存超限，请减小数据规模", detail
    except RecursionError:
        return False, "递归太深，超出回归环境限制", detail
    except Exception as ex:  # noqa: BLE001
        detail["error"] = str(ex)
        return False, f"运行时报错：{type(ex).__name__}: {ex}", detail
    finally:
        sys.settrace(old_trace)
        sys.setrecursionlimit(old_recursion)
        _step_tracer.tracing = False
        if not was_tracing:
            tracemalloc.stop()
    return True, f"通过（{detail['passed']}/{detail['total']} 个用例，其中隐藏 {len(hid)} 个）", detail


def validate(task: dict, answer: str) -> tuple[bool, str, dict]:
    if task is None:
        return False, "这道题没有配置", {}
    kind = task.get("kind")
    if kind == "code":
        # v1.1：即使走旧的 validate 入口，也跑公开 + 隐藏，且隐藏明细脱敏
        return _check_code(task, answer, hide_hidden=True)
    if kind == "sql":
        return _check_sql(task, answer)
    if kind == "text":
        return _check_text(task, answer)
    if kind == "number":
        return _check_number(task, answer)
    return False, f"未知题型 {kind}", {}


def validate_regression(task: dict, answer: str) -> tuple[bool, str, dict]:
    """工程工作台专用：公开 + 隐藏一起跑，隐藏用例绝不通过 API 回传前端。

    与 validate() 的区别不是「换了个函数名」：
    - 只接受 code 类型的生产修复；
    - 用例来自 public_cases + hidden_cases 的真实合并；
    - 返回明细里没有隐藏用例的 args / expect / got / 异常文本；
    - 跑通才允许部署，跑不通只是一次失败发布（由工作台按工程成本结算）。
    """
    if task is None:
        return False, "没有这个修复用例", {}
    if task.get("kind") != "code":
        return False, "生产回归只支持代码修复", {}
    return _check_code(task, answer, hide_hidden=True)


def solution(node: dict, state=None) -> str:
    task = task_for(node, state)
    return (task or {}).get("solution", "")
