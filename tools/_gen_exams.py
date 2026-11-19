# -*- coding: utf-8 -*-
"""一次性生成 v0.9 题库：以编码题为主 + 难度分层变体。生成后自带判题自测。"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.rules import exam as E  # noqa: E402

P = Path(__file__).resolve().parents[1] / "content" / "exams.json"
d = json.loads(P.read_text(encoding="utf-8"))
T = d["tasks"]

# v1.1 阶段二起，题库拆成 public_cases / hidden_cases；本脚本是 v0.9 的一次性出题器，
# CODE() 会把同名题重写成只有 cases，重跑会把隐藏回归用例整批冲掉。检测到新格式就退出。
if any("hidden_cases" in t for t in T.values() if isinstance(t, dict)):
    print("跳过：content/exams.json 已是 v1.1 格式（含 hidden_cases）。")
    print("本脚本仅用于 v0.9 历史出题，不要用它覆盖现有题库。")
    sys.exit(0)


def CODE(key, title, role, brief, goal, starter, entry, cases, solution, hint, minutes=10):
    T[key] = {
        "kind": "code", "title": title, "role": role, "brief": brief, "goal": goal,
        "starter": starter, "entry": entry, "cases": cases,
        "checks": {"must": [r"def\s+" + re.escape(entry)], "forbid": []},
        "hint": hint, "solution": solution, "minutes": minutes,
    }


# ---------------- 基础题库 A（简单 / 普通 共用）----------------
CODE("ex_fn_paginate", "「我的订单」下一页参数", "后端 · 列表接口",
     "列表页改成游标分页了。前端传 user_id 和上一页最后一条的 created_at，你要生成给 DAO 的查询参数。",
     "实现 next_page_args(user_id, last_created_at, limit=20)：返回 dict，含 user_id 与 limit；只有传了 cursor（非 None）才额外带 cursor 键。首页不要出现 cursor。",
     "def next_page_args(user_id, last_created_at, limit=20):\n    # TODO\n    pass",
     "next_page_args",
     [{"args": [12345, None, 20], "expect": {"user_id": 12345, "limit": 20}},
      {"args": [12345, "2023-12-15 12:00:00", 20], "expect": {"user_id": 12345, "cursor": "2023-12-15 12:00:00", "limit": 20}},
      {"args": [7, "x", 5], "expect": {"user_id": 7, "cursor": "x", "limit": 5}}],
     "def next_page_args(user_id, last_created_at, limit=20):\n    out = {\"user_id\": user_id, \"limit\": limit}\n    if last_created_at is not None:\n        out[\"cursor\"] = last_created_at\n    return out",
     "首页没有游标，就不要把 cursor: null 塞进查询里：DAO 会把它当成一个真的条件。", 10)

CODE("ex_fn_idempo", "支付回调幂等去重", "后端 · 交易链路",
     "支付回调会重试，同一个幂等键可能连着来好几次。先到的那条才算数。",
     "实现 dedup_keys(events)：events 是 [[key, value], ...]，按 key 保留第一次出现的记录，保持原顺序，返回 [[key, value], ...]。",
     "def dedup_keys(events):\n    # TODO\n    pass",
     "dedup_keys",
     [{"args": [[["a", 1], ["b", 2], ["a", 3]]], "expect": [["a", 1], ["b", 2]]},
      {"args": [[["x", 1], ["x", 2], ["y", 3]]], "expect": [["x", 1], ["y", 3]]},
      {"args": [[]], "expect": []}],
     "def dedup_keys(events):\n    seen = set()\n    out = []\n    for key, value in events:\n        if key in seen:\n            continue\n        seen.add(key)\n        out.append([key, value])\n    return out",
     "一个 set 记下见过的 key 就够了；注意保留第一次的顺序，不要用 dict 覆盖成最后一次。", 10)

CODE("ex_fn_order_state", "订单状态机的流转判定", "后端 · 业务函数开发",
     "订单状态：0 新建 / 1 已支付 / 2 已发货 / 3 已完成 / 4 已取消。状态不能乱跳，客服改单前必须过这一层。",
     "实现 can_transition(current, target)：只允许 0→1→2→3 顺序流转；4（取消）只允许从 0 或 1 过去；原地不动一律拒绝。返回 bool。",
     "def can_transition(current, target):\n    # TODO\n    pass",
     "can_transition",
     [{"args": [0, 1], "expect": True}, {"args": [1, 2], "expect": True},
      {"args": [2, 3], "expect": True}, {"args": [3, 1], "expect": False},
      {"args": [0, 4], "expect": True}, {"args": [2, 4], "expect": False},
      {"args": [1, 1], "expect": False}],
     "def can_transition(current, target):\n    nxt = {0: 1, 1: 2, 2: 3}\n    if target == 4:\n        return current in (0, 1)\n    return nxt.get(current) == target",
     "先把「正常下一步」写成一张映射表，取消作为一条单独规则；别用一堆 if 漏掉 3→4。", 12)

CODE("ex_fn_retry", "退避重试间隔", "后端 · 下游调用",
     "下游偶尔超时。立刻重试会把人家打得更死，要按尝试次数指数退避，并且封顶。",
     "实现 backoff_ms(attempt, base=100, cap=2000)：返回 base × 2^attempt，但不超过 cap。attempt 从 0 开始。",
     "def backoff_ms(attempt, base=100, cap=2000):\n    # TODO\n    pass",
     "backoff_ms",
     [{"args": [0], "expect": 100}, {"args": [3], "expect": 800},
      {"args": [10], "expect": 2000}, {"args": [2, 50, 300], "expect": 200}],
     "def backoff_ms(attempt, base=100, cap=2000):\n    return min(cap, base * (2 ** attempt))",
     "先算指数再取 min，不要先截断再乘；cap 是绝对上限。", 8)

CODE("ex_fn_circuit", "熔断器状态判定", "SRE · 治理",
     "熔断器的状态不该靠一堆散落的 if。把判定收成一个纯函数，测试才好写。",
     "实现 breaker_state(failures, threshold=5, recovering=False)：failures >= threshold 返回 'open'；recovering 为真且未熔断返回 'half_open'；failures > 0 返回 'watch'；否则 'closed'。",
     "def breaker_state(failures, threshold=5, recovering=False):\n    # TODO\n    pass",
     "breaker_state",
     [{"args": [0], "expect": "closed"}, {"args": [2], "expect": "watch"},
      {"args": [5], "expect": "open"}, {"args": [9], "expect": "open"},
      {"args": [3, 5, True], "expect": "half_open"}, {"args": [0, 5, True], "expect": "half_open"}],
     "def breaker_state(failures, threshold=5, recovering=False):\n    if failures >= threshold:\n        return \"open\"\n    if recovering:\n        return \"half_open\"\n    if failures > 0:\n        return \"watch\"\n    return \"closed\"",
     "判断顺序很重要：熔断（open）优先于半开（half_open），否则阈值到了还会放流量进去。", 10)

CODE("ex_fn_sli", "剩余错误预算", "SRE · SLO",
     "本月 SLO 是 99.9%。老板问「还能再出几次事」，得给一个数。",
     "实现 error_budget_left(total, bad, slo=0.999)：允许的错误数 = total × (1-slo)，返回 (允许 - bad) / 允许，保留 4 位小数，最低 0；允许数为 0 时返回 1.0。",
     "def error_budget_left(total, bad, slo=0.999):\n    # TODO\n    pass",
     "error_budget_left",
     [{"args": [100000, 30], "expect": 0.7}, {"args": [100000, 100], "expect": 0.0},
      {"args": [100000, 0], "expect": 1.0}, {"args": [1000, 0, 0.99], "expect": 1.0}],
     "def error_budget_left(total, bad, slo=0.999):\n    allowed = total * (1 - slo)\n    if allowed <= 0:\n        return 1.0\n    return round(max(0.0, allowed - bad) / allowed, 4)",
     "预算是「允许的错误数」而不是「请求数」；用完就是 0，不要给负数。", 10)

CODE("ex_fn_cost", "要加几台机器", "性能 · 容量",
     "峰值 850 QPS，单机按 120 QPS 算，还要留 30% 安全余量。采购要整数台。",
     "实现 required_servers(peak_qps, per_server=120, safety=1.3)：返回 ceil(peak_qps × safety / per_server)，peak_qps 为 0 时返回 0。不能用 import。",
     "def required_servers(peak_qps, per_server=120, safety=1.3):\n    # TODO\n    pass",
     "required_servers",
     [{"args": [850, 120, 1.3], "expect": 10}, {"args": [240, 120, 1.0], "expect": 2},
      {"args": [0, 120, 1.0], "expect": 0}, {"args": [100, 100, 1.0], "expect": 1}],
     "def required_servers(peak_qps, per_server=120, safety=1.3):\n    target = peak_qps * safety\n    if target <= 0:\n        return 0\n    n = int(target // per_server)\n    if n * per_server < target:\n        n += 1\n    return n",
     "不能用 math.ceil：先整除，再看余数是不是还要补一台。", 10)

CODE("ex_fn_lru", "LRU 缓存命中次数", "算法 · 缓存",
     "热点 key 的缓存容量有限。先按 LRU 模拟一遍，看看这套容量值不值。",
     "实现 lru_hits(keys, capacity)：按 LRU（最久未用的先淘汰）模拟访问，返回命中次数。capacity 至少为 1。",
     "def lru_hits(keys, capacity):\n    # TODO\n    pass",
     "lru_hits",
     [{"args": [[1, 2, 1, 3, 2], 2], "expect": 1},
      {"args": [[1, 1, 1], 1], "expect": 2},
      {"args": [[1, 2, 3], 3], "expect": 0},
      {"args": [[1, 2, 3, 1, 4], 2], "expect": 0}],
     "def lru_hits(keys, capacity):\n    cache = []\n    hits = 0\n    for k in keys:\n        if k in cache:\n            hits += 1\n            cache.remove(k)\n            cache.append(k)\n        else:\n            if len(cache) >= capacity:\n                cache.pop(0)\n            cache.append(k)\n    return hits",
     "命中之后要把它挪到队尾（最近使用）；淘汰永远从队头拿最久没用的那个。", 12)

CODE("ex_fn_topk", "最大的 K 个数", "算法 · 排行榜",
     "运营要「金额最高的前 K 单」。别把 20 万行全拉到应用里排。",
     "实现 top_k(nums, k)：返回最大的 k 个数，按降序；k 超过长度就返回全部降序；空列表返回空列表。",
     "def top_k(nums, k):\n    # TODO\n    pass",
     "top_k",
     [{"args": [[3, 1, 4, 1, 5, 9, 2], 3], "expect": [9, 5, 4]},
      {"args": [[1, 2], 5], "expect": [2, 1]},
      {"args": [[], 3], "expect": []},
      {"args": [[5, 5, 3], 2], "expect": [5, 5]}],
     "def top_k(nums, k):\n    return sorted(nums, reverse=True)[:k]",
     "重复值要保留（不是去重后的 TopK）；切片超过长度会自动截断。", 8)

CODE("ex_fn_merge", "合并重叠区间", "算法 · 告警收敛",
     "同一时刻涌进来一堆重叠的告警时间窗，值班同学只想看几段真正的故障区间。",
     "实现 merge_intervals(intervals)：输入 [[start,end], ...]，合并所有重叠/相接（s <= 上一段 end）的区间，返回合并后的 [[start,end], ...]，按 start 升序。空输入返回空列表。",
     "def merge_intervals(intervals):\n    # TODO\n    pass",
     "merge_intervals",
     [{"args": [[[1, 3], [2, 6], [8, 10], [15, 18]]], "expect": [[1, 6], [8, 10], [15, 18]]},
      {"args": [[[1, 4], [4, 5]]], "expect": [[1, 5]]},
      {"args": [[]], "expect": []},
      {"args": [[[1, 2]]], "expect": [[1, 2]]}],
     "def merge_intervals(intervals):\n    if not intervals:\n        return []\n    xs = sorted([list(i) for i in intervals])\n    out = [xs[0]]\n    for s, e in xs[1:]:\n        if s <= out[-1][1]:\n            out[-1][1] = max(out[-1][1], e)\n        else:\n            out.append([s, e])\n    return out",
     "先按 start 排序，再线性扫一遍；相接（s == 上一段 end）也算重叠。", 12)

CODE("ex_fn_hotkey", "找出热点 key", "算法 · 流量治理",
     "大促时少数几个 key 被打爆了。先把出现次数达到阈值的 key 找出来，才好做本地缓存。",
     "实现 hot_keys(keys, threshold=3)：返回出现次数 >= threshold 的 key 列表，按 key 升序；没有就返回空列表。",
     "def hot_keys(keys, threshold=3):\n    # TODO\n    pass",
     "hot_keys",
     [{"args": [["a", "b", "a", "a", "c", "a"]], "expect": ["a"]},
      {"args": [["x", "y", "x", "y"]], "expect": []},
      {"args": [["a", "a", "b", "b", "a", "b"], 3], "expect": ["a", "b"]},
      {"args": [[]], "expect": []}],
     "def hot_keys(keys, threshold=3):\n    counts = {}\n    for k in keys:\n        counts[k] = counts.get(k, 0) + 1\n    return sorted([k for k, n in counts.items() if n >= threshold])",
     "一次遍历计数，再筛 + 排序；别用嵌套循环数重复。", 9)

CODE("ex_fn_interval_cover", "故障总覆盖时长", "算法 · 复盘",
     "合并完告警窗口，还要回答老板「到底一共断了多久」。",
     "实现 covered_length(intervals)：先按 merge_intervals 的规则合并重叠区间，再返回所有合并后区间长度之和（end - start，不是闭区间 +1）。",
     "def covered_length(intervals):\n    # TODO\n    pass",
     "covered_length",
     [{"args": [[[1, 3], [2, 6], [8, 10]]], "expect": 7},
      {"args": [[[1, 4], [4, 5]]], "expect": 4},
      {"args": [[]], "expect": 0},
      {"args": [[[0, 10], [3, 5]]], "expect": 10}],
     "def covered_length(intervals):\n    if not intervals:\n        return 0\n    xs = sorted([list(i) for i in intervals])\n    out = [xs[0]]\n    for s, e in xs[1:]:\n        if s <= out[-1][1]:\n            out[-1][1] = max(out[-1][1], e)\n        else:\n            out.append([s, e])\n    total = 0\n    for s, e in out:\n        total += e - s\n    return total",
     "先合并再求和，直接把原区间长度相加会重复计算重叠部分。", 12)

# ---------------- 困难 B 套：加严变体 ----------------
CODE("ex_fn_paginate_h", "双向游标 + has_more", "后端 · 困难",
     "困难难度：列表要支持往前翻，还要告诉前端还有没有下一页。",
     "实现 page_args(user_id, cursor, direction='next', limit=20, has_more=False)：返回 dict 恒含 user_id/limit/direction/has_more；cursor 非空才带 cursor；direction 只能是 'next' 或 'prev'，其他值一律按 'next'。",
     "def page_args(user_id, cursor, direction='next', limit=20, has_more=False):\n    # TODO\n    pass",
     "page_args",
     [{"args": [1, None], "expect": {"user_id": 1, "limit": 20, "direction": "next", "has_more": False}},
      {"args": [1, "c", "prev", 10, True], "expect": {"user_id": 1, "cursor": "c", "limit": 10, "direction": "prev", "has_more": True}},
      {"args": [2, "x", "bad", 5, False], "expect": {"user_id": 2, "cursor": "x", "limit": 5, "direction": "next", "has_more": False}}],
     "def page_args(user_id, cursor, direction='next', limit=20, has_more=False):\n    out = {\"user_id\": user_id, \"limit\": limit,\n           \"direction\": direction if direction in (\"next\", \"prev\") else \"next\",\n           \"has_more\": bool(has_more)}\n    if cursor is not None:\n        out[\"cursor\"] = cursor\n    return out",
     "非法 direction 要兜底成 next，别把脏参数透传给 DAO。", 12)

CODE("ex_fn_order_state_h", "状态机 + 退款分支", "后端 · 困难",
     "困难难度：已支付但未发货可以退款（状态 5），已完成也能申请售后（状态 6）。",
     "实现 next_states(current)：返回该状态允许跳转到的状态列表（升序）。规则：0→[1,4]，1→[2,4,5]，2→[3,5]，3→[6]，4/5/6 是终态→[]。",
     "def next_states(current):\n    # TODO\n    pass",
     "next_states",
     [{"args": [0], "expect": [1, 4]}, {"args": [1], "expect": [2, 4, 5]},
      {"args": [2], "expect": [3, 5]}, {"args": [3], "expect": [6]},
      {"args": [4], "expect": []}, {"args": [5], "expect": []}, {"args": [9], "expect": []}],
     "def next_states(current):\n    table = {0: [1, 4], 1: [2, 4, 5], 2: [3, 5], 3: [6]}\n    return sorted(table.get(current, []))",
     "终态和未知状态都返回空列表，别让状态机自己造出一个不存在的状态。", 12)

CODE("ex_fn_circuit_h", "带冷却计数的熔断", "SRE · 困难",
     "困难难度：熔断后要等 cooldown 个时间片才允许半开。",
     "实现 breaker_state(failures, open_ticks, threshold=5, cooldown=3)：failures>=threshold 且 open_ticks < cooldown → 'open'；failures>=threshold 且 open_ticks >= cooldown → 'half_open'；failures>0 → 'watch'；否则 'closed'。",
     "def breaker_state(failures, open_ticks, threshold=5, cooldown=3):\n    # TODO\n    pass",
     "breaker_state",
     [{"args": [0, 0], "expect": "closed"}, {"args": [2, 0], "expect": "watch"},
      {"args": [5, 0], "expect": "open"}, {"args": [5, 2], "expect": "open"},
      {"args": [5, 3], "expect": "half_open"}, {"args": [7, 9, 5, 1], "expect": "half_open"}],
     "def breaker_state(failures, open_ticks, threshold=5, cooldown=3):\n    if failures >= threshold:\n        return \"open\" if open_ticks < cooldown else \"half_open\"\n    if failures > 0:\n        return \"watch\"\n    return \"closed\"",
     "冷却时间是「已经开了多久」，不是「还要等多久」。", 12)

CODE("ex_fn_lru_h", "LRU 最终状态", "算法 · 困难",
     "困难难度：不只要命中次数，还要给出缓存里最后剩下什么。",
     "实现 lru_state(keys, capacity)：返回 [hits, cache]，cache 按「最久未用 → 最近使用」排列，元素是 key。capacity 至少 1。",
     "def lru_state(keys, capacity):\n    # TODO\n    pass",
     "lru_state",
     [{"args": [[1, 2, 1, 3, 2], 2], "expect": [1, [3, 2]]},
      {"args": [[1, 1, 1], 1], "expect": [2, [1]]},
      {"args": [[], 2], "expect": [0, []]},
      {"args": [[1, 2, 3, 1], 3], "expect": [1, [2, 3, 1]]}],
     "def lru_state(keys, capacity):\n    cache = []\n    hits = 0\n    for k in keys:\n        if k in cache:\n            hits += 1\n            cache.remove(k)\n            cache.append(k)\n        else:\n            if len(cache) >= capacity:\n                cache.pop(0)\n            cache.append(k)\n    return [hits, cache]",
     "返回的是两个值组成的列表；命中后记得把 key 挪到队尾。", 12)

CODE("ex_fn_topk_h", "去重后的 TopK", "算法 · 困难",
     "困难难度：同一个商品的重复曝光只算一次。",
     "实现 top_k_unique(nums, k)：先去重，再返回最大的 k 个数（降序）；k 超过去重后数量就返回全部。",
     "def top_k_unique(nums, k):\n    # TODO\n    pass",
     "top_k_unique",
     [{"args": [[3, 1, 4, 1, 5, 9, 2], 3], "expect": [9, 5, 4]},
      {"args": [[5, 5, 3], 2], "expect": [5, 3]},
      {"args": [[1, 2], 5], "expect": [2, 1]},
      {"args": [[], 3], "expect": []}],
     "def top_k_unique(nums, k):\n    return sorted(set(nums), reverse=True)[:k]",
     "set 去重会丢顺序，但后面反正要降序排，没问题。", 9)

CODE("ex_fn_sli_h", "滚动窗口错误率", "SRE · 困难",
     "困难难度：按最近 N 个时间片算错误率，超预算就报警。",
     "实现 window_error_rate(samples, window=3, budget=0.01)：samples 是 [[total, bad], ...]，只取最后 window 片，返回 [rate, over]；rate = 总 bad / 总 total 保留 4 位小数，总 total 为 0 时 rate=0.0；over 为 rate 是否严格大于 budget 的 bool。",
     "def window_error_rate(samples, window=3, budget=0.01):\n    # TODO\n    pass",
     "window_error_rate",
     [{"args": [[[1000, 2], [1000, 3], [1000, 5]]], "expect": [0.0033, False]},
      {"args": [[[1000, 20], [1000, 20]]], "expect": [0.02, True]},
      {"args": [[]], "expect": [0.0, False]},
      {"args": [[[100, 1], [100, 1], [100, 1], [100, 1]], 2], "expect": [0.01, False]}],
     "def window_error_rate(samples, window=3, budget=0.01):\n    recent = samples[-window:]\n    total = sum(s[0] for s in recent)\n    bad = sum(s[1] for s in recent)\n    if total == 0:\n        return [0.0, False]\n    rate = round(bad / total, 4)\n    return [rate, rate > budget]",
     "取最后 window 片再求和；rate 严格大于 budget 才算超（等于不算）。", 12)

# ---------------- 老手 C 套：更硬的变体 ----------------
CODE("ex_fn_paginate_v", "游标签名与越界保护", "后端 · 老手",
     "老手难度：cursor 要带签名，limit 要夹在 1~50，脏参数一律打回。",
     "实现 build_query(user_id, cursor=None, limit=20, sign='s')：limit 夹到 [1,50]；返回 dict 含 user_id/limit；cursor 非空时带 cursor 与 sign，否则都不带。",
     "def build_query(user_id, cursor=None, limit=20, sign='s'):\n    # TODO\n    pass",
     "build_query",
     [{"args": [1], "expect": {"user_id": 1, "limit": 20}},
      {"args": [1, "c", 999, "k"], "expect": {"user_id": 1, "limit": 50, "cursor": "c", "sign": "k"}},
      {"args": [2, "x", 0], "expect": {"user_id": 2, "limit": 1, "cursor": "x", "sign": "s"}},
      {"args": [3, None, -5], "expect": {"user_id": 3, "limit": 1}}],
     "def build_query(user_id, cursor=None, limit=20, sign='s'):\n    lim = max(1, min(50, limit))\n    out = {\"user_id\": user_id, \"limit\": lim}\n    if cursor is not None:\n        out[\"cursor\"] = cursor\n        out[\"sign\"] = sign\n    return out",
     "先夹 limit，再决定带不带 cursor/sign；两个字段要同进同出。", 13)

CODE("ex_fn_order_state_v", "状态机 + 合法路径校验", "后端 · 老手",
     "老手难度：给一条状态变更历史，判断整条路径是否合法（含取消/退款/售后分支）。",
     "实现 valid_path(states)：states 是状态列表。合法转移：0→1/4，1→2/4/5，2→3/5，3→6，4/5/6 终态；第一个状态必须是 0；单元素 [0] 合法。返回 bool。",
     "def valid_path(states):\n    # TODO\n    pass",
     "valid_path",
     [{"args": [[0, 1, 2, 3]], "expect": True}, {"args": [[0]], "expect": True},
      {"args": [[0, 4]], "expect": True}, {"args": [[0, 2]], "expect": False},
      {"args": [[0, 1, 5]], "expect": True}, {"args": [[1, 2, 3]], "expect": False},
      {"args": [[]], "expect": False}],
     "def valid_path(states):\n    if not states or states[0] != 0:\n        return False\n    table = {0: [1, 4], 1: [2, 4, 5], 2: [3, 5], 3: [6]}\n    for a, b in zip(states, states[1:]):\n        if b not in table.get(a, []):\n            return False\n    return True",
     "空路径不合法；用 zip 逐对检查，别只看首尾。", 14)

CODE("ex_fn_lru_v", "LRU 命中明细", "算法 · 老手",
     "老手难度：给出每一次访问是命中还是未命中，以及最终缓存。",
     "实现 lru_trace(keys, capacity)：返回 [flags, cache]，flags 是与 keys 等长的 0/1 列表（1=命中），cache 按最久未用→最近使用排列。",
     "def lru_trace(keys, capacity):\n    # TODO\n    pass",
     "lru_trace",
     [{"args": [[1, 2, 1, 3], 2], "expect": [[0, 0, 1, 0], [1, 3]]},
      {"args": [[1, 1, 1], 1], "expect": [[0, 1, 1], [1]]},
      {"args": [[], 2], "expect": [[], []]},
      {"args": [[1, 2, 3], 2], "expect": [[0, 0, 0], [2, 3]]}],
     "def lru_trace(keys, capacity):\n    cache = []\n    flags = []\n    for k in keys:\n        if k in cache:\n            flags.append(1)\n            cache.remove(k)\n            cache.append(k)\n        else:\n            flags.append(0)\n            if len(cache) >= capacity:\n                cache.pop(0)\n            cache.append(k)\n    return [flags, cache]",
     "flags 与输入等长；淘汰后新 key 放队尾。", 13)

CODE("ex_fn_topk_v", "稳定 TopK（同值保序）", "算法 · 老手",
     "老手难度：金额相同时，先出现的订单排在前面。",
     "实现 stable_top_k(pairs, k)：pairs 是 [[id, amount], ...]，按 amount 降序、amount 相同按 id 升序，取前 k 个，返回 id 列表。",
     "def stable_top_k(pairs, k):\n    # TODO\n    pass",
     "stable_top_k",
     [{"args": [[[3, 50], [1, 50], [2, 90]], 2], "expect": [2, 1]},
      {"args": [[[5, 10], [4, 10], [1, 10]], 3], "expect": [1, 4, 5]},
      {"args": [[[1, 1]], 5], "expect": [1]},
      {"args": [[], 2], "expect": []}],
     "def stable_top_k(pairs, k):\n    xs = sorted(pairs, key=lambda p: (-p[1], p[0]))\n    return [p[0] for p in xs[:k]]",
     "排序键写成 (-amount, id)，一个 sorted 就够。", 11)

CODE("ex_fn_merge_v", "合并后最大空闲间隙", "算法 · 老手",
     "老手难度：合并完故障窗口，还要找出最长的一段「没出事」的时间。",
     "实现 largest_gap(intervals, horizon)：先合并重叠区间（同 merge_intervals），再计算 [0, horizon] 内未被覆盖的最长连续长度。输入区间都落在 [0, horizon] 内。",
     "def largest_gap(intervals, horizon):\n    # TODO\n    pass",
     "largest_gap",
     [{"args": [[[1, 3], [2, 6], [8, 10]], 12], "expect": 2},
      {"args": [[[0, 12]], 12], "expect": 0},
      {"args": [[], 12], "expect": 12},
      {"args": [[[2, 4], [9, 10]], 12], "expect": 5}],
     "def largest_gap(intervals, horizon):\n    xs = sorted([list(i) for i in intervals])\n    out = []\n    for s, e in xs:\n        if out and s <= out[-1][1]:\n            out[-1][1] = max(out[-1][1], e)\n        else:\n            out.append([s, e])\n    best, cur = 0, 0\n    for s, e in out:\n        if s > cur:\n            best = max(best, s - cur)\n        cur = max(cur, e)\n    return max(best, horizon - cur)",
     "别忘了最后一段（末尾区间到 horizon），也别忘了开头那一段。", 14)

CODE("ex_fn_hotkey_v", "TopN 热点及占比", "算法 · 老手",
     "老手难度：给出访问次数最多的 N 个 key，以及它们合起来占总请求的比例。",
     "实现 hot_report(keys, n=2)：返回 [top, share]。top 是出现次数最多的 n 个 key（次数降序，次数相同按 key 升序）组成的列表；share 是这些 key 的总次数 / 总请求数，保留 4 位小数。空输入返回 [[], 0.0]。",
     "def hot_report(keys, n=2):\n    # TODO\n    pass",
     "hot_report",
     [{"args": [["a", "b", "a", "c", "a"]], "expect": [["a", "b"], 0.8]},
      {"args": [["x", "x", "y", "y"], 1], "expect": [["x"], 0.5]},
      {"args": [[]], "expect": [[], 0.0]},
      {"args": [["a", "b", "c"], 5], "expect": [["a", "b", "c"], 1.0]}],
     "def hot_report(keys, n=2):\n    if not keys:\n        return [[], 0.0]\n    counts = {}\n    for k in keys:\n        counts[k] = counts.get(k, 0) + 1\n    order = sorted(counts, key=lambda k: (-counts[k], k))\n    top = order[:n]\n    share = round(sum(counts[k] for k in top) / len(keys), 4)\n    return [top, share]",
     "次数相同按 key 升序；share 的分母是总请求数，不是去重后的 key 数。", 13)

# SQL / number 的加严变体（保证任何难度都不是纯填空）
T["ex_fn_sql_h"] = {
    "kind": "sql", "title": "覆盖索引（困难加严）", "role": "数据库 · 困难",
    "brief": "困难难度：不仅要走索引，还要零回表，且不允许任何额外排序。",
    "goal": "查询 user_id = 12345 的订单，按时间倒序取 20 条；执行计划必须出现 COVERING INDEX，且不能出现 TEMP B-TREE。",
    "starter": T["ex_covering"]["starter"],
    "checks": {"must": ["where\\s+user_id\\s*=", "order\\s+by\\s+created_at\\s+desc", "limit\\s+20"],
               "forbid": ["select\\s+\\*"],
               "require_plan": ["COVERING\\s+INDEX"], "forbid_plan": ["TEMP B-TREE"], "max_rows": 20},
    "hint": "只取索引里已有的列：user_id 与 created_at。多取一列就要回表。",
    "solution": T["ex_covering"]["solution"], "minutes": 10,
}
T["ex_fn_sql_v"] = {
    "kind": "sql", "title": "范围查询（老手加严）", "role": "数据库 · 老手",
    "brief": "老手难度：区间条件 + 索引下推 + 禁止额外排序，一条都不能少。",
    "goal": "取 2023-12-01 一天的订单，执行计划必须用上索引且不出现 TEMP B-TREE。",
    "starter": T["ex_range_rewrite"]["starter"],
    "checks": {"must": ["created_at\\s*>=\\s*'", "created_at\\s*<\\s*'", "limit\\s+500"],
               "forbid": ["date\\s*\\("],
               "require_plan": ["USING\\s+INDEX|COVERING\\s+INDEX"],
               "forbid_plan": ["TEMP B-TREE"], "max_rows": 500},
    "hint": "函数包住索引列就走不了索引；写成半开区间 [当天, 次日)。",
    "solution": T["ex_range_rewrite"]["solution"], "minutes": 10,
}
T["ex_number_h"] = {
    "kind": "number", "title": "写入放大（困难加严）", "role": "容量 · 困难",
    "brief": "订单表每秒写 90 行，现有 3 个二级索引，再加 1 个；另外心跳每秒还有 60 次元数据写。",
    "goal": "这张表每秒总共多少次随机写 IO？（取整）",
    "starter": "", "checks": {}, "answer": 420, "tolerance": 10, "unit": "IOPS",
    "hint": "4 个二级索引 × 90 = 360，再加 60 次元数据写 = 420。",
    "solution": "4 × 90 + 60 = 420 IOPS", "minutes": 6,
}
T["ex_number_v"] = {
    "kind": "number", "title": "峰值容量（老手加严）", "role": "容量 · 老手",
    "brief": "本周峰值 850 QPS，月增长 6%，按复利算两个月后；安全余量 1.3，单机 120 QPS。",
    "goal": "两个月后需要多少台机器？（向上取整）",
    "starter": "", "checks": {}, "answer": 11, "tolerance": 1, "unit": "台",
    "hint": "850 × 1.06² ≈ 955；× 1.3 ≈ 1241；/ 120 ≈ 10.3，向上取整 11。",
    "solution": "850 × 1.06² × 1.3 / 120 ≈ 10.3 → 11 台", "minutes": 7,
}

d["difficulty_variants"] = {
    "hard": {
        "ex_fn_paginate": "ex_fn_paginate_h",
        "ex_fn_order_state": "ex_fn_order_state_h",
        "ex_fn_circuit": "ex_fn_circuit_h",
        "ex_fn_lru": "ex_fn_lru_h",
        "ex_fn_topk": "ex_fn_topk_h",
        "ex_fn_sli": "ex_fn_sli_h",
        "ex_covering": "ex_fn_sql_h",
        "ex_pool_math": "ex_number_h",
    },
    "veteran": {
        "ex_fn_paginate": "ex_fn_paginate_v",
        "ex_fn_order_state": "ex_fn_order_state_v",
        "ex_fn_lru": "ex_fn_lru_v",
        "ex_fn_topk": "ex_fn_topk_v",
        "ex_fn_merge": "ex_fn_merge_v",
        "ex_fn_hotkey": "ex_fn_hotkey_v",
        "ex_range_rewrite": "ex_fn_sql_v",
        "ex_capacity_math": "ex_number_v",
        "ex_fn_circuit": "ex_fn_circuit_h",
    },
}
d["difficulty_sets"] = {
    "easy": {"label": "基础题库", "note": "与普通难度同题：以真库 SQL 与业务函数为主，配置 / 计算题只作补充。"},
    "normal": {"label": "基础题库", "note": "与简单难度同题，但必须交卷才能掌握（简单模式读满即得）。"},
    "hard": {"label": "加严题库 B", "note": "新增 6 道加严编码题 + 加严覆盖索引题 + 加严容量题；每个专精仍是编码考核。"},
    "veteran": {"label": "加严题库 C", "note": "新增 7 道老手编码题 + 加严范围查询题 + 加严峰值容量题，题目与困难不同。"},
}
d["version"] = 2
d["intro"] = ("考核以真实动手为主：编码题会真的执行你写的函数并跑用例，SQL 会在 20 万订单副本上跑 EXPLAIN；"
              "配置题与计算题只作补充。简单/普通共用基础题库，困难/老手各有加严变体，任何难度都不会只剩填空。")

P.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

# ---------------- 自测：参考解必须过、起步代码必须不过 ----------------
bad = []
for k, t in T.items():
    if t["kind"] not in ("code", "sql", "number"):
        continue
    ok, msg, det = E.validate(t, t.get("solution", ""))
    if not ok:
        bad.append((k, "solution 没过: " + msg))
        continue
    starter = t.get("starter", "")
    if starter.strip():
        ok2, _, _ = E.validate(t, starter)
        if ok2:
            bad.append((k, "starter 直接通过了"))
print("tasks", len(T),
      "code", sum(1 for t in T.values() if t["kind"] == "code"),
      "sql", sum(1 for t in T.values() if t["kind"] == "sql"),
      "text", sum(1 for t in T.values() if t["kind"] == "text"),
      "number", sum(1 for t in T.values() if t["kind"] == "number"))
print("PROBLEMS:", bad if bad else "无")
if bad:
    sys.exit(1)
