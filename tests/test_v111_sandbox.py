"""v1.1.1 回归：玩家代码执行安全。

覆盖任务书要求的场景：
1 正常函数通过；2 顶层无限循环被拒；3 顶层无限递归被拒；
4 函数内死循环超时；5 函数内无限递归受限；6 大量内存分配受限；
7 异常后监控状态清理；8 隐藏用例不泄漏；9 连续提交不串状态；
10 并发请求互不阻塞；11 子进程崩溃主进程存活；12 Windows 子进程隔离可用。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.rules import exam as E  # noqa: E402


def make_task(entry="f", public=None, hidden=None):
    return {
        "kind": "code",
        "entry": entry,
        "checks": {},
        "public_cases": public or [{"args": [1], "expect": 2}],
        "hidden_cases": hidden or [],
    }


def run(code: str, task=None):
    started = time.monotonic()
    ok, msg, detail = E.validate_regression(task or make_task(), code)
    return ok, msg, detail, time.monotonic() - started


# ---------- 1. 正常代码 ----------

def test_normal_function_passes_and_is_actually_executed():
    ok, msg, detail, dt = run("def f(x):\n    return x + 1")
    assert ok, msg
    assert detail["public"]["passed"] == 1
    assert dt < 5


def test_module_docstring_is_allowed():
    code = '"""模块说明"""\n\ndef f(x):\n    return x + 1'
    ok, msg, detail, _ = run(code)
    assert ok, msg


def test_top_level_literal_constants_and_annotations_are_allowed():
    code = (
        '"""doc"""\n'
        "LIMIT = 10\n"
        "NAMES: list = ['a', 'b']\n"
        "\n"
        "def f(x: int) -> int:\n"
        "    return x + 1 if x < LIMIT else LIMIT\n"
    )
    ok, msg, detail, _ = run(code)
    assert ok, msg


def test_annotated_assignment_with_call_is_rejected():
    code = "VALUE: int = len('abc')\n\ndef f(x):\n    return x"
    ok, msg, _detail, _dt = run(code)
    assert not ok
    assert "顶层" in msg or "注解" in msg


def test_format_string_reflection_is_blocked_but_normal_format_is_allowed():
    bad = "def f(x):\n    return '{0.__class__}'.format(x)"
    ok, msg, _detail, _dt = run(bad)
    assert not ok and "格式化字符串" in msg

    good = "def f(x):\n    return '{}'.format(x)"
    ok2, msg2, _detail2, _dt2 = run(good, make_task(public=[{"args": [7], "expect": "7"}]))
    assert ok2, msg2


# ---------- 2/3. 顶层任意执行被结构预检拦住 ----------

def test_top_level_while_true_is_rejected_without_hanging():
    code = "def f(x):\n    return x + 1\n\nwhile True:\n    pass"
    ok, msg, detail, dt = run(code)
    assert not ok
    assert "顶层" in msg and "while" in msg
    assert dt < 1.5, "结构检查必须是纯 AST，不能真的执行代码"


def test_top_level_recursion_or_call_is_rejected():
    for code in (
        "def f(x):\n    return f(x)\n\nf(1)",
        "def f(x):\n    return x\n\nresult = f(1)",
        'if __name__ == "__main__":\n    pass',
    ):
        ok, msg, detail, dt = run(code)
        assert not ok, code
        assert "顶层" in msg, (code, msg)
        assert dt < 1.5


def test_top_level_decorator_default_and_class_body_are_rejected():
    bad_codes = [
        "@print\ndef f(x):\n    return x",                       # 装饰器
        "def f(x=len('abc')):\n    return x",                    # 默认参数调用
        "class C:\n    value = len('abc')",                      # 类体调用
        "x = [i for i in range(10)]",                            # 顶层推导式
        "def f(x):\n    return x\n\nraise SystemExit",           # 顶层 raise
    ]
    for code in bad_codes:
        ok, msg, _det, _dt = run(code)
        assert not ok, code


# ---------- 4/5. 函数内的死循环 / 无限递归 ----------

def test_function_infinite_loop_times_out_and_returns():
    ok, msg, detail, dt = run("def f(x):\n    while True:\n        pass")
    assert not ok
    assert "死循环" in msg or "超时" in msg
    assert dt < 8


def test_function_infinite_recursion_is_limited():
    ok, msg, detail, dt = run("def f(x):\n    return f(x)")
    assert not ok
    assert ("递归" in msg) or ("超时" in msg) or ("运行时报错" in msg)
    assert dt < 8


# ---------- 6. 内存 ----------

def test_large_memory_allocation_is_limited_without_killing_parent():
    code = (
        "def f(x):\n"
        "    data = []\n"
        "    while len(data) < 400:\n"
        "        data.append(bytearray(1024 * 1024))\n"
        "    return len(data)"
    )
    ok, msg, detail, dt = run(code)
    assert not ok
    assert dt < 10


def test_memory_limit_detail_is_reported():
    # 直接验证 tracemalloc 检查本身：把上限调到 1 字节，任何分配都应被拦。
    old = E.MEM_LIMIT_BYTES
    try:
        E.MEM_LIMIT_BYTES = 1
        ok, msg, _detail, _dt = run("def f(x):\n    return x + 1")
        assert not ok and "内存" in msg
    finally:
        E.MEM_LIMIT_BYTES = old


# ---------- 7. 执行后监控状态清理 ----------

def test_parent_monitoring_state_is_restored_after_failure_and_success():
    trace_before = sys.gettrace()
    recursion_before = sys.getrecursionlimit()
    run("def f(x):\n    raise ValueError('boom')")
    run("def f(x):\n    return x + 1")
    assert sys.gettrace() is trace_before
    assert sys.getrecursionlimit() == recursion_before


def test_runtime_error_does_not_poison_next_submission():
    ok_bad, _msg, _detail, _dt = run("def f(x):\n    return 1 / 0")
    good, msg, detail, _dt = run("def f(x):\n    return x + 1")
    assert not ok_bad
    assert good, msg
    assert detail["public"]["passed"] == 1


# ---------- 8. 隐藏用例不泄漏 ----------

def test_hidden_case_failure_does_not_leak_inputs():
    tasks = {k: v for k, v in E.load()["tasks"].items() if v.get("kind") == "code"}
    task = tasks["ex_fn_idempo"]
    hardcoded = (
        "def dedup_keys(events):\n"
        "    if events == [['a', 1], ['b', 2], ['a', 3]]:\n"
        "        return [['a', 1], ['b', 2]]\n"
        "    return []\n"
    )
    ok, msg, detail = E.validate_regression(task, hardcoded)
    assert not ok
    blob = msg + json.dumps(detail, ensure_ascii=False)
    for hidden in task["hidden_cases"]:
        needle = json.dumps(hidden.get("args"), ensure_ascii=False)
        assert needle not in blob, f"隐藏输入泄漏：{needle}"
    assert '"solution"' not in blob


# ---------- 9. 连续提交 ----------

def test_consecutive_submissions_are_independent():
    first, _m1, _d1, _t1 = run("def f(x):\n    while True:\n        pass")
    assert not first
    second, msg, detail, _t2 = run("def f(x):\n    return x + 1")
    assert second, msg
    third, msg2, detail2, _t3 = run("def f(x):\n    return x * 2", make_task(public=[{"args": [3], "expect": 6}]))
    assert third, msg2
    assert detail2["public"]["passed"] == 1


# ---------- 10. 并发 ----------

def test_concurrent_requests_do_not_block_each_other():
    slow = "def f(x):\n    while True:\n        pass"
    fast = "def f(x):\n    return x + 1"

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        slow_future = pool.submit(E.validate_regression, make_task(), slow)
        time.sleep(0.05)
        fast_future = pool.submit(E.validate_regression, make_task(), fast)
        fast_ok, fast_msg, _fast_detail = fast_future.result(timeout=6)
        fast_dt = time.monotonic() - started
        slow_ok, _slow_msg, _slow_detail = slow_future.result(timeout=10)
    assert fast_ok, fast_msg
    assert fast_dt < 3, "快速请求不应等慢请求超时"
    assert not slow_ok


# ---------- 11. 子进程崩溃 ----------

def test_worker_crash_does_not_break_parent(monkeypatch):
    monkeypatch.setattr(
        E, "_worker_command", lambda: [sys.executable, "-c", "import sys; sys.exit(3)"]
    )
    ok, msg, detail = E.validate_regression(make_task(), "def f(x):\n    return x + 1")
    assert not ok
    assert "异常退出" in msg and "服务未受影响" in msg
    # 崩溃后下一次正常提交仍然可用（monkeypatch 在测试结束后恢复）
    monkeypatch.undo()
    ok2, msg2, _detail2 = E.validate_regression(make_task(), "def f(x):\n    return x + 1")
    assert ok2, msg2


# ---------- 12. 平台 / 子进程隔离 ----------

def test_sandbox_uses_subprocess_isolation():
    assert E.SANDBOX_INFO["mode"] == "subprocess"
    assert E.SANDBOX_INFO["worker"] == "core.rules.exam_worker"
    assert "os_memory_limit" in E.SANDBOX_INFO


def test_worker_module_is_importable_on_this_platform():
    proc = subprocess.run(
        [sys.executable, "-c", "import core.rules.exam_worker; print('ok')"],
        cwd=str(E.PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


def test_worker_does_not_emit_non_json_on_normal_path():
    # 直接启动 worker，喂一个合法任务，stdout 必须只有一行 JSON。
    payload = {
        "task": {
            "kind": "code", "entry": "f", "checks": {},
            "public_cases": [{"args": [1], "expect": 2}], "hidden_cases": [],
        },
        "answer": "def f(x):\n    return x + 1",
        "limits": {"steps": 20000, "wall": 2.0, "mem": 24 * 1024 * 1024, "recursion": 160},
        "hide_hidden": True,
    }
    proc = subprocess.run(
        [sys.executable, "-m", E.WORKER_MODULE],
        cwd=str(E.PROJECT_ROOT),
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, proc.stdout
    assert json.loads(lines[0])["ok"] is True


def test_windows_platform_note_is_honest():
    # Windows 没有 POSIX rlimit；Job Object 附加失败时必须如实报告 False。
    if os.name == "nt":
        assert isinstance(E.SANDBOX_INFO["os_cpu_limit"], bool)
        assert isinstance(E.SANDBOX_INFO["os_memory_limit"], bool)
    else:
        assert E.SANDBOX_INFO["os_memory_limit"] is True
