"""确定性随机：按 (seed, week, purpose) 派生独立随机流。"""

from __future__ import annotations

import hashlib
import random


def rng(seed: int, week: int, purpose: str) -> random.Random:
    key = f"{seed}|{week}|{purpose}".encode()
    digest = hashlib.sha256(key).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))
