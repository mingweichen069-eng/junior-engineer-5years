"""无界面运行：新游戏、推进、查看指标。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.engine import new_game, step
from core.model.serde import to_jsonable
from core.rules import actions as action_rules
from core.rules.evaluate import evaluate_peak
from services import save as saves

ROOT = Path(__file__).resolve().parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _money(cents: int) -> str:
    return f"¥{cents / 100:,.0f}"


def _load(args) -> "object":
    st = saves.load_game(args.slot)
    if st is None:
        st = new_game(seed=args.seed, difficulty=args.difficulty)
        saves.save_game(args.slot, st, "初始存档")
    return st


def cmd_new(args) -> None:
    st = new_game(seed=args.seed, difficulty=args.difficulty)
    saves.save_game(args.slot, st, "初始存档")
    print(f"新游戏：seed={st.meta.seed} difficulty={st.meta.difficulty} slot={args.slot}")
    print(f"现金 {_money(st.company.cash_cents)}  注册 {st.company.registered:,}  周 {st.meta.week}")
    print(f"表：{', '.join(f'{t.name}({t.rows:,})' for t in st.db.tables.values())}")


def cmd_sim(args) -> None:
    st = _load(args)
    print(f"{'周':>3} {'峰值QPS':>8} {'p95ms':>7} {'大屏ms':>7} {'订单ms':>7} {'IOPS':>6} {'IO%':>5} {'池ρ':>5} {'可用性':>7} {'事件':>4} {'现金':>10}")
    for _ in range(args.weeks):
        m = step(st)
        dash = m.by_id("q_dashboard")
        orders = m.by_id("q_my_orders")
        print(
            f"{m.week:>3} {m.peak_qps:>8.1f} {m.p95_ms:>7.0f} "
            f"{(dash.time_ms if dash else 0):>7.0f} {(orders.time_ms if orders else 0):>7.0f} "
            f"{m.io_iops:>6.0f} {m.io_util * 100:>4.0f}% {m.pool_rho:>5.2f} "
            f"{m.availability * 100:>6.2f}% {st.history[-1].event_count:>4} "
            f"{_money(st.company.cash_cents):>10}"
        )
    saves.save_game(args.slot, st, "自动存档")


def cmd_preview(args) -> None:
    st = _load(args)
    m = evaluate_peak(st)
    print(f"第 {m.week} 周   DAU {m.dau:,.0f}   峰值 {m.peak_qps:.1f} QPS")
    print(f"Buffer pool 命中 {m.buffer_hit:.3f}   磁盘 {m.disk_used_gb:.2f} GB   IOPS {m.io_iops:.0f} ({m.io_util*100:.0f}%)")
    print(f"{'查询':<12}{'QPS':>7}{'耗时ms':>9}{'扫描行':>10}{'路径':>12}")
    for q in m.queries:
        print(f"{q.name:<12}{q.qps:>7.2f}{q.time_ms:>9.1f}{q.rows:>10,}{q.path:>12}")
    print(f"\n连接池 ρ={m.pool_rho:.2f} 等待 p95={m.pool_wait_ms:.1f}ms")
    print(f"API p95={m.p95_ms:.0f}ms  ρ={m.api_rho:.2f}  错误率={m.error_rate*100:.2f}%  可用性={m.availability*100:.2f}%")


def cmd_show(args) -> None:
    st = _load(args)
    print(f"第 {st.meta.week} 周 / {st.meta.difficulty} / seed {st.meta.seed}")
    print(f"现金 {_money(st.company.cash_cents)}  注册 {st.company.registered:,}  AP {st.player.ap}")
    for t in st.db.tables.values():
        idx = ", ".join(t.indexes) or "无"
        print(f"  {t.name:<12} rows={t.rows:>10,}  indexes={idx}")
    print(f"应用 {st.app.servers} 台 × {st.app.workers_per_server} worker，连接池 {st.app.pool_size}")
    print(f"缓存 {'开' if st.cache.enabled else '关'}，策略 {list(st.cache.policies) or '无'}")
    if st.actions.pending:
        print(f"进行中：{[(p.action_id, f'W{p.due_week}') for p in st.actions.pending]}")


def cmd_act(args) -> None:
    st = _load(args)
    catalog = action_rules.load_actions()
    if args.list or not args.action:
        print(f"本周 AP {st.player.ap}  现金 {_money(st.company.cash_cents)}")
        for a in catalog:
            ok, why = action_rules.check(st, a)
            mark = "可做" if ok else f"不可：{why}"
            print(f"  {a['id']:<24} AP{a['ap']}   {a['name']:<28} {mark}")
        return
    ok, msg = action_rules.start(st, args.action, catalog)
    print(msg)
    if ok:
        saves.save_game(args.slot, st, "自动存档")
        print(f"剩余 AP {st.player.ap}  现金 {_money(st.company.cash_cents)}")
        if st.actions.pending:
            print(f"预计 W{st.actions.pending[-1].due_week} 生效")


def cmd_month(args) -> None:
    st = _load(args)
    if not st.months:
        print("还没有月报（每 4 周结算一次）")
        return
    print(f"{'月':>3} {'收入':>10} {'成本':>10} {'利润':>10} {'可用性':>7} {'p95':>6} {'绩效':>4} {'赔偿':>8} {'现金':>10}")
    for m in st.months:
        print(
            f"{m.month:>3} {_money(m.revenue_cents):>10} {_money(m.cost_cents):>10} "
            f"{_money(m.profit_cents):>10} {m.availability * 100:>6.2f}% {m.p95_ms:>6.0f} "
            f"{m.grade:>4} {_money(m.sla_penalty_cents):>8} {_money(m.cash_cents):>10}"
        )


def cmd_export(args) -> None:
    st = _load(args)
    out = ROOT / "saves" / f"export-{args.slot}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        json.dumps({"meta": {"seed": st.meta.seed, "week": st.meta.week}, "state": to_jsonable(st)},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"已导出 {out}")


def cmd_events(args) -> None:
    st = _load(args)
    if not st.events.log:
        print("还没有触发过事件")
        return
    for e in st.events.log[-args.last:]:
        indent = "  " * e.depth
        arrow = "└ " if e.cause_id else ""
        eff = "  ".join(f"{k}={v}" for k, v in e.effects.items())
        print(f"{indent}{arrow}W{e.week}D{e.day}  {e.title}  [{eff}]")


def main() -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--slot", default="dev")
    common.add_argument("--seed", type=int, default=20261118)
    common.add_argument("--difficulty", default="normal", choices=["easy", "normal", "hard", "veteran"])

    p = argparse.ArgumentParser(prog="sim_cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("new", parents=[common]).set_defaults(func=cmd_new)
    s = sub.add_parser("sim", parents=[common])
    s.add_argument("--weeks", type=int, default=4)
    s.set_defaults(func=cmd_sim)
    sub.add_parser("preview", parents=[common]).set_defaults(func=cmd_preview)
    sub.add_parser("show", parents=[common]).set_defaults(func=cmd_show)
    a = sub.add_parser("act", parents=[common])
    a.add_argument("action", nargs="?")
    a.add_argument("--list", action="store_true")
    a.set_defaults(func=cmd_act)
    sub.add_parser("month", parents=[common]).set_defaults(func=cmd_month)
    e = sub.add_parser("events", parents=[common])
    e.add_argument("--last", type=int, default=20)
    e.set_defaults(func=cmd_events)
    sub.add_parser("export", parents=[common]).set_defaults(func=cmd_export)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
