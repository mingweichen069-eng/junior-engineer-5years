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
"""

from __future__ import annotations

import ast
import json
import re
import sys
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


def public_task(task: dict | None) -> dict | None:
    """给前端的题目：含公开测试用例，但不含参考答案。"""
    if not task:
        return None
    cases = task.get("cases") or []
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
        # 编码题公开用例（输入 + 期望输出），方便玩家自己先跑一遍；不公开 solution
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


def _canon(v):
    """把返回值归一化，避免 tuple / list 之类只在类型上不同的假失败。"""
    if isinstance(v, (tuple, list)):
        return [_canon(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _canon(x) for k, x in v.items()}
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
    return _step_tracer


_step_tracer.count = 0


def _check_code(task: dict, answer: str) -> tuple[bool, str, dict]:
    """编码题：真的执行玩家写的函数，逐个用例比对输出。

    安全边界：AST 白名单（禁 import / global / 双下划线）、受限 builtins、
    执行步数上限（防死循环）。这是本地单机判题，不做网络/文件访问。
    """
    text = str(answer or "")
    detail: dict = {"kind": "code", "cases": [], "passed": 0, "total": 0}
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
            return False, "考核里不允许 import / global，直接把函数写完就行", detail
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return False, "不允许访问双下划线属性", detail
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            return False, "不允许访问双下划线名字", detail

    ns: dict = {"__builtins__": SAFE_BUILTINS, "__name__": "exam_answer"}
    try:
        exec(compile(tree, "<exam>", "exec"), ns)
    except Exception as ex:  # noqa: BLE001
        detail["error"] = str(ex)
        return False, f"代码加载失败：{ex}", detail
    entry = task.get("entry", "")
    fn = ns.get(entry)
    if not callable(fn):
        return False, f"没找到函数 {entry}（注意函数名与缩进）", detail

    cases = task.get("cases") or []
    detail["total"] = len(cases)
    old_trace = sys.gettrace()
    try:
        for i, case in enumerate(cases, 1):
            _step_tracer.count = 0
            sys.settrace(_step_tracer)
            try:
                got = fn(*(case.get("args") or []), **(case.get("kwargs") or {}))
            finally:
                sys.settrace(old_trace)
            expect = case.get("expect")
            ok = _canon(got) == _canon(expect)
            detail["cases"].append({
                "i": i, "args": _safe_repr(case.get("args")),
                "expect": _safe_repr(expect), "got": _safe_repr(got), "ok": bool(ok),
            })
            if ok:
                detail["passed"] += 1
            else:
                return False, (
                    f"第 {i} 个用例没过：输入 {_safe_repr(case.get('args'))}，"
                    f"期望 {_safe_repr(expect)}，得到 {_safe_repr(got)}"
                ), detail
    except _StepLimit:
        return False, "代码跑了太多步，可能有死循环", detail
    except Exception as ex:  # noqa: BLE001
        detail["error"] = str(ex)
        return False, f"运行时报错：{type(ex).__name__}: {ex}", detail
    finally:
        sys.settrace(old_trace)
    return True, f"通过（{detail['passed']}/{detail['total']} 个用例）", detail


def validate(task: dict, answer: str) -> tuple[bool, str, dict]:
    if task is None:
        return False, "这道题没有配置", {}
    kind = task.get("kind")
    if kind == "code":
        return _check_code(task, answer)
    if kind == "sql":
        return _check_sql(task, answer)
    if kind == "text":
        return _check_text(task, answer)
    if kind == "number":
        return _check_number(task, answer)
    return False, f"未知题型 {kind}", {}


def solution(node: dict, state=None) -> str:
    task = task_for(node, state)
    return (task or {}).get("solution", "")
