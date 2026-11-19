"""隔离执行进程：由 ``core.rules.exam._execute_isolated`` 启动。

协议：
- stdin：一行 JSON，包含 task / answer / limits / hide_hidden；
- stdout：一行 JSON，包含 ok / msg / detail；
- 玩家代码的 stdout / stderr 在 ``exam.run_code_regression`` 里被重定向丢弃，
  不会污染这里的协议输出。

这个进程只做「加载 + 调用」；主进程负责超时与杀进程。
"""

from __future__ import annotations

import json
import sys

FALLBACK_MEM = 256 * 1024 * 1024


def _apply_posix_limits(mem_bytes: int, wall_sec: float) -> None:
    """POSIX：尽量用操作系统级 rlimit 限制地址空间与 CPU 时间。

    Windows 没有等价 rlimit；那边由父进程的 Job Object（若可用）+ 墙钟超时兜底。
    失败时静默继续——这只是纵深防御，不是唯一防线。
    """
    try:
        import resource  # noqa: PLC0415
    except ImportError:
        return
    # macOS 的虚拟地址空间预留远大于常驻内存，RLIMIT_AS 容易把解释器自己拦死；
    # 只在 Linux 上启用地址空间上限，其他 POSIX 平台靠 tracemalloc + 墙钟超时。
    if sys.platform.startswith("linux"):
        try:
            resource.setrlimit(resource.RLIMIT_AS, (int(mem_bytes), int(mem_bytes)))
        except (ValueError, OSError):
            pass
    try:
        cpu = max(2, int(wall_sec) + 2)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    except (ValueError, OSError):
        pass


def main() -> None:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        sys.stdout.write(
            json.dumps({"ok": False, "msg": "执行参数损坏", "detail": {}}, ensure_ascii=False)
        )
        sys.stdout.write("\n")
        sys.stdout.flush()
        return

    limits = payload.get("limits") or {}
    try:
        mem_limit = max(FALLBACK_MEM, int(limits.get("mem") or 0) * 4)
    except (TypeError, ValueError):
        mem_limit = FALLBACK_MEM
    try:
        wall = float(limits.get("wall") or 2.0)
    except (TypeError, ValueError):
        wall = 2.0
    _apply_posix_limits(mem_limit, wall)

    from core.rules import exam  # noqa: PLC0415  （先设 rlimit，再加载执行器）

    ok, msg, detail = exam.run_code_regression(
        payload.get("task") or {},
        payload.get("answer", ""),
        limits,
        hide_hidden=bool(payload.get("hide_hidden", True)),
    )
    sys.stdout.write(
        json.dumps({"ok": bool(ok), "msg": str(msg), "detail": detail}, ensure_ascii=False, default=str)
    )
    sys.stdout.write("\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
