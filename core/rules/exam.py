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
"""

from __future__ import annotations

import ast
import json
import re
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
        if _step_tracer.count > STEP_LIMIT:
            raise _StepLimit()
        if _step_tracer.count % TRACE_CHECK_EVERY == 0:
            if time.monotonic() - _step_tracer.start > WALL_LIMIT_SEC:
                raise _WallLimit()
            if _step_tracer.tracing:
                peak = tracemalloc.get_traced_memory()[1]
                if peak > MEM_LIMIT_BYTES:
                    raise _MemoryLimit()
    return _step_tracer


_step_tracer.count = 0
_step_tracer.start = 0.0
_step_tracer.tracing = False


def _check_code(task: dict, answer: str, *, hide_hidden: bool = True) -> tuple[bool, str, dict]:
    """生产回归：真的执行玩家写的函数，公开 + 隐藏用例逐个比对。

    安全边界（进程内轻量沙箱，不联网 / 不读文件，不引入额外框架）：
    - AST 白名单：禁 import / global / 双下划线 / 超大整数常量；
    - 受限 builtins（没有 open / eval / exec / __import__）；
    - 执行步数上限 + 墙钟超时（防死循环）；
    - tracemalloc 峰值 + 受限递归深度（防内存 / 栈爆掉）。

    hide_hidden=True 时，隐藏用例的输入、期望值、实际值和异常文本都不进
    返回明细，只给「第几例没过」和统计 —— API 因此不会把隐藏用例送回前端。
    """
    text = str(answer or "")
    pub = public_cases(task)
    hid = hidden_cases(task)
    cases = pub + hid
    n_pub = len(pub)
    detail: dict = {
        "kind": "code", "cases": [], "passed": 0, "total": len(cases),
        "public": {"total": n_pub, "passed": 0},
        "hidden": {"total": len(hid), "passed": 0},
    }
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
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)):
            return False, "回归里不允许 import / global，直接把函数写完就行", detail
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return False, "不允许访问双下划线属性", detail
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            return False, "不允许访问双下划线名字", detail
        if isinstance(node, ast.Constant) and isinstance(node.value, int) \
                and not isinstance(node.value, bool) and abs(node.value) > MAX_INT_CONST:
            return False, "代码里出现了过大的常量（超出回归环境限制）", detail
        # 10**9 这类幂运算会绕过上面的常量检查，却在 C 层一次性吃掉几个 G
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow) \
                and isinstance(node.right, ast.Constant) \
                and isinstance(node.right.value, (int, float)) \
                and not isinstance(node.right.value, bool) and node.right.value > 8:
            return False, "代码里出现了过大的幂运算（超出回归环境限制）", detail

    ns: dict = {"__builtins__": SAFE_BUILTINS, "__name__": "exam_answer"}
    try:
        exec(compile(tree, "<regression>", "exec"), ns)
    except Exception as ex:  # noqa: BLE001
        detail["error"] = str(ex)
        return False, f"代码加载失败：{ex}", detail
    entry = task.get("entry", "")
    fn = ns.get(entry)
    if not callable(fn):
        return False, f"没找到函数 {entry}（注意函数名与缩进）", detail

    old_trace = sys.gettrace()
    old_recursion = sys.getrecursionlimit()
    was_tracing = tracemalloc.is_tracing()
    if not was_tracing:
        tracemalloc.start()
    _step_tracer.tracing = True
    try:
        sys.setrecursionlimit(RECURSION_LIMIT)
        for i, case in enumerate(cases, 1):
            is_hidden = i > n_pub
            _step_tracer.count = 0
            _step_tracer.start = time.monotonic()
            sys.settrace(_step_tracer)
            try:
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
        if time.monotonic() - _step_tracer.start > WALL_LIMIT_SEC:
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
