from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import save as saves  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(saves, "DB_PATH", tmp_path / "test.db")
    import web.app as webapp

    monkeypatch.setattr(webapp, "CURRENT_SLOT", "test")
    app = webapp.create_app()
    app.testing = True
    return app.test_client()


def test_index_page(client) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "初级工程师：五年" in r.get_data(as_text=True)


def test_state_shape(client) -> None:
    j = client.get("/api/state").get_json()
    assert j["ok"] is True
    d = j["data"]
    for key in ("week", "cash", "ap", "metrics", "queries", "actions", "events", "months"):
        assert key in d
    assert d["week"] == 1
    # 没开监控：查询明细被遮住（信息是资源）
    dash = d["queries"][1]
    assert dash["hidden"] is True and dash["ms"] is None
    assert d["metrics"]["p95"] > 0
    assert any(a["ready"] for a in d["actions"])


def test_action_and_advance(client) -> None:
    cap = client.get("/api/state").get_json()["data"]["ap_cap"]
    j = client.post("/api/action", json={"id": "cache_dashboard"}).get_json()
    assert j["ok"] is True
    assert j["data"]["ap"] == cap - 3
    assert "q_dashboard" in j["data"]["cache"]["policies"]

    j = client.post("/api/advance", json={"weeks": 1}).get_json()
    assert j["ok"] is True
    assert j["data"]["week"] == 2
    assert j["data"]["ap"] == j["data"]["ap_cap"]


def test_save_and_load(client) -> None:
    client.post("/api/advance", json={"weeks": 1})
    assert client.post("/api/save", json={"slot": "x"}).get_json()["ok"]
    j = client.post("/api/load", json={"slot": "x"}).get_json()
    assert j["ok"] and j["data"]["week"] == 2
