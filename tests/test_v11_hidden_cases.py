"""v1.1 阶段二回归：公开测试用例与隐藏回归用例真正分离。

对应任务书：
  三、P0 公开测试用例漏洞（前端可见样例 == 后端验证样例）
  九、9 公开测试通过但隐藏测试失败时，不能部署代码
  十、每个阶段写测试 / 跑 pytest / 检查旧功能回归

原则：不删旧测试、不硬编码判题结果、不重新引入科技树 / 考试解锁。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import new_game  # noqa: E402
from core.rules import exam as E  # noqa: E402
from core.rules import workbench  # noqa: E402

CASE_ID = "ex_fn_idempo"

# 只把公开样例抄成 if/return：公开能过，隐藏必挂。
# 这是任务书点名的漏洞——"玩家可能针对公开样例硬编码答案"。
HARDCODED = '''
def dedup_keys(events):
    if events == [["a", 1], ["b", 2], ["a", 3]]:
        return [["a", 1], ["b", 2]]
    if events == [["x", 1], ["x", 2], ["y", 3]]:
        return [["x", 1], ["y", 3]]
    if events == []:
        return []
    return []
'''


def code_tasks():
    return {k: v for k, v in E.load()["tasks"].items() if v.get("kind") == "code"}


def legacy_task(entry="f", cases=None):
    return {
        "kind": "code", "entry": entry, "checks": {},
        "public_cases": cases or [{"args": [1], "expect": 1}],
        "hidden_cases": [],
    }


# ---------- 题库分离 ----------

def test_all_code_tasks_split_public_and_hidden():
    tasks = code_tasks()
    assert len(tasks) == 24
    raises = 0
    for k, t in tasks.items():
        assert "cases" not in t, f"{k} 仍残留旧 cases 字段"
        pub, hid = t["public_cases"], t["hidden_cases"]
        assert pub and len(hid) >= 2, k
        assert t.get("solution"), f"{k} 参考实现要保留，供生成器 / 平衡脚本验证"
        pkeys = {
            json.dumps([c.get("args"), c.get("kwargs") or {}], ensure_ascii=False, sort_keys=True)
            for c in pub
        }
        for c in hid:
            key = json.dumps([c.get("args"), c.get("kwargs") or {}], ensure_ascii=False, sort_keys=True)
            assert key not in pkeys, f"{k} 隐藏用例与公开用例重复"
            assert "expect" in c or c.get("expect_raises"), f"{k} 隐藏用例缺少判定目标"
            assert c.get("note"), f"{k} 隐藏用例应说明覆盖哪类边界"
        raises += sum(1 for c in hid if c.get("expect_raises"))
    assert raises >= 5, "必须真的包含异常输入用例，而不是只换了个字段名"


def test_reference_solutions_pass_public_and_hidden():
    """参考实现必须真的跑通全部隐藏用例，不是把结果写死成通过。"""
    for k, t in code_tasks().items():
        ok, msg, det = E.validate_regression(t, t["solution"])
        assert ok, f"{k}: {msg}"
        assert det["public"]["passed"] == det["public"]["total"]
        assert det["hidden"]["passed"] == det["hidden"]["total"]
        assert det["hidden"]["total"] >= 2


# ---------- 硬编码公开样例必须被拦 ----------

def test_hardcoded_public_answer_fails_hidden_regression():
    t = code_tasks()[CASE_ID]
    ok, msg, det = E.validate_regression(t, HARDCODED)
    assert not ok
    assert det["public"]["passed"] == det["public"]["total"]   # 公开全过
    assert det["hidden"]["failed"] is not None                 # 挂在隐藏
    assert "隐藏" in msg and "输入不公开" in msg


def test_hidden_inputs_never_appear_in_detail_or_message():
    t = code_tasks()[CASE_ID]
    ok, msg, det = E.validate_regression(t, HARDCODED)
    blob = msg + json.dumps(det, ensure_ascii=False)
    for needle in ('"a", 3], ["b", 2], ["a", 1', '"expect_raises"',
                   '"hidden_cases"', '"solution"'):
        assert needle not in blob, f"隐藏用例泄露：{needle}"
    assert len(det["cases"]) == det["public"]["total"]        # 明细只含公开
    assert all(c["i"] <= det["public"]["total"] for c in det["cases"])


def test_public_task_has_no_hidden_or_solution():
    t = code_tasks()[CASE_ID]
    p = E.public_task(t)
    # public_task 只透出 args / kwargs / expect 三个字段，不改写内容、不夹带隐藏
    assert len(p["cases"]) == len(t["public_cases"])
    for a, b in zip(p["cases"], t["public_cases"]):
        assert a["args"] == b.get("args") and a["expect"] == b.get("expect")
    blob = json.dumps(p, ensure_ascii=False)
    assert "hidden_cases" not in blob and '"solution"' not in blob
    assert '"expect_raises"' not in blob


# ---------- 前端 / API 拿不到隐藏用例 ----------

def test_cases_for_exposes_only_public_cases():
    st = new_game(seed=301)
    rows = workbench.cases_for(st)
    tasks = code_tasks()
    assert {r["id"] for r in rows} == set(tasks)
    for r in rows:
        assert "hidden_cases" not in r and "solution" not in r
        assert r["hidden_count"] >= 2
        assert r["cases"] == tasks[r["id"]]["public_cases"]


def test_api_state_does_not_expose_hidden_cases(client):
    client.post("/api/new", json={"seed": 305, "difficulty": "normal"})
    blob = json.dumps(client.get("/api/state").get_json(), ensure_ascii=False)
    assert "hidden_cases" not in blob
    assert '"expect_raises"' not in blob
    assert '"solution"' not in blob
    t = code_tasks()[CASE_ID]
    assert json.dumps(t["hidden_cases"][1]["args"], ensure_ascii=False) not in blob


# ---------- 部署闭环：跑不通就不部署 ----------

def test_deploy_fix_rejects_hardcoded_answer_as_failed_release():
    st = new_game(seed=302)
    ap0 = st.player.ap
    r = workbench.deploy_fix(st, CASE_ID, HARDCODED)
    assert not r["ok"]
    assert CASE_ID not in st.assets.fixes
    assert r["charged"] is True and st.player.ap < ap0    # 失败发布照样付成本
    assert st.lab.deployments[-1]["ok"] is False
    assert r["detail"]["hidden"]["failed"]


def test_deploy_fix_deploys_real_solution_after_hidden_pass():
    st = new_game(seed=303)
    sol = code_tasks()[CASE_ID]["solution"]
    r = workbench.deploy_fix(st, CASE_ID, sol)
    assert r["ok"], r["message"]
    assert CASE_ID in st.assets.fixes
    assert r["detail"]["hidden"]["passed"] == r["detail"]["hidden"]["total"]


def test_api_deploy_fix_does_not_leak_hidden(client):
    client.post("/api/new", json={"seed": 304, "difficulty": "normal"})
    r = client.post("/api/workbench/deploy-fix",
                    json={"case_id": CASE_ID, "code": HARDCODED}).get_json()
    assert r["ok"] is False and r["detail"]["charged"] is True
    blob = json.dumps(r, ensure_ascii=False)
    t = code_tasks()[CASE_ID]
    for needle in (json.dumps(t["hidden_cases"][1]["args"], ensure_ascii=False),
                   '"hidden_cases"', '"expect_raises"', '"solution"'):
        assert needle not in blob, f"API 泄露隐藏用例：{needle}"


# ---------- 执行环境：超时 / 内存 / 递归 / AST 隔离 ----------

def test_step_limit_stops_infinite_loop():
    t = legacy_task()
    ok, msg, det = E.validate_regression(t, "def f(x):\n    while True:\n        pass")
    assert not ok and ("死循环" in msg or "超时" in msg)


def test_wall_clock_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(E, "WALL_LIMIT_SEC", 0.0)
    t = legacy_task()
    ok, msg, det = E.validate_regression(t, "def f(x):\n    return x")
    assert not ok and "超时" in msg


def test_memory_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(E, "MEM_LIMIT_BYTES", 1)
    t = legacy_task(cases=[{"args": [1], "expect": 499500}])
    code = "def f(x):\n    total = 0\n    for i in range(1000):\n        total += i\n    return total"
    ok, msg, det = E.validate_regression(t, code)
    assert not ok and "内存" in msg


def test_recursion_limit_is_enforced():
    t = legacy_task()
    ok, msg, det = E.validate_regression(t, "def f(x):\n    return f(x)")
    assert not ok and ("递归" in msg or "运行时报错" in msg)


def test_huge_constant_and_power_are_rejected():
    t = legacy_task()
    for code in ("def f(x):\n    return list(range(1000000000))",
                 "def f(x):\n    return list(range(10**9))"):
        ok, msg, det = E.validate_regression(t, code)
        assert not ok and "过大的" in msg


def test_forbidden_imports_still_blocked():
    t = legacy_task()
    ok, msg, det = E.validate_regression(t, "import os\ndef f(x):\n    return x")
    assert not ok and "import" in msg


# ---------- 旧功能回归：只有 cases 的旧题仍可判 ----------

def test_legacy_task_with_only_cases_still_validates():
    t = {
        "kind": "code", "entry": "f", "checks": {},
        "cases": [{"args": [2], "expect": 4}, {"args": [3], "expect": 9}],
        "solution": "def f(x):\n    return x*x",
    }
    ok, msg, det = E.validate(t, t["solution"])
    assert ok, msg
    assert det["public"]["passed"] == 2 and det["hidden"]["total"] == 0
