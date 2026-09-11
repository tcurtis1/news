import json
import pytest
from starlette.testclient import TestClient

from app.fantasy.db import init_db
from app.fantasy.service import create_league, join_league
from app.fantasy.waivers import get_available_players
from app.main import app


@pytest.fixture(autouse=True)
def isolated_analytics(monkeypatch, tmp_path):
    import app.analytics as analytics_mod
    monkeypatch.setattr(analytics_mod, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(analytics_mod, "STORE_PATH", tmp_path / "analytics.json")


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    test_db = tmp_path / "test_fantasy_s4_routes.db"
    monkeypatch.setattr("app.fantasy.db.DB_PATH", test_db)
    init_db()


def test_waivers_and_free_agency_routes():
    league, t1 = create_league("Route League", "Tony", "Tony Team", max_teams=4)
    _, t2, _ = join_league(league.invite_token, "Alice", "Alice Team")

    client = TestClient(app)
    client.cookies.set("yoyo_fantasy_tokens", json.dumps([t1.manager_token]))

    # 1. GET Waivers page
    res = client.get(f"/sports/fantasy/league/{league.id}/waivers")
    assert res.status_code == 200
    assert "Waiver Wire & Free Agency" in res.text
    assert "Instant Free Agent Pickup" in res.text

    # 2. Get player to add
    avail = get_available_players(league.id, limit=3)
    p1 = avail["players"][0]["player"]["id"]
    p2 = avail["players"][1]["player"]["id"]

    # 3. Add Free Agent
    res = client.post(
        f"/sports/fantasy/api/league/{league.id}/waivers/add-drop",
        json={"add_player_id": p1},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True

    # 4. Add p2 and drop p1
    res = client.post(
        f"/sports/fantasy/api/league/{league.id}/waivers/add-drop",
        json={"add_player_id": p2, "drop_player_id": p1},
    )
    assert res.status_code == 200
    assert res.json()["success"] is True

    # 5. Place waiver claim on dropped p1
    res = client.post(
        f"/sports/fantasy/api/league/{league.id}/waivers/claim",
        json={"add_player_id": p1, "bid_amount": 15},
    )
    assert res.status_code == 200
    assert res.json()["success"] is True

    # 6. Commissioner triggers waiver run
    client.cookies.set("yoyo_fantasy_tokens", json.dumps([league.commissioner_token]))
    res = client.post(f"/sports/fantasy/api/league/{league.id}/waivers/process")
    assert res.status_code == 200
    assert "Waivers processed" in res.json()["message"]


def test_trades_routes_and_machine():
    league, t1 = create_league("Trade Route League", "Tony", "Tony Team", max_teams=4)
    _, t2, _ = join_league(league.invite_token, "Alice", "Alice Team")

    # Give p1 to Tony, p2 to Alice
    avail = get_available_players(league.id, limit=4)
    p1 = avail["players"][0]["player"]["id"]
    p2 = avail["players"][1]["player"]["id"]

    client = TestClient(app)
    client.cookies.set("yoyo_fantasy_tokens", json.dumps([t1.manager_token]))

    client.post(f"/sports/fantasy/api/league/{league.id}/waivers/add-drop", json={"add_player_id": p1})

    # Switch cookie to Alice and add p2
    client.cookies.set("yoyo_fantasy_tokens", json.dumps([t2.manager_token]))
    client.post(f"/sports/fantasy/api/league/{league.id}/waivers/add-drop", json={"add_player_id": p2})

    # 1. GET Trades page
    client.cookies.set("yoyo_fantasy_tokens", json.dumps([t1.manager_token]))
    res = client.get(f"/sports/fantasy/league/{league.id}/trades")
    assert res.status_code == 200
    assert "The Trade Machine" in res.text
    assert "Propose a New Trade" in res.text

    # 2. Propose trade
    res = client.post(
        f"/sports/fantasy/api/league/{league.id}/trades/propose",
        json={
            "recipient_team_id": t2.id,
            "proposer_player_ids": [p1],
            "recipient_player_ids": [p2],
            "note": "Let's do this!",
        },
    )
    assert res.status_code == 200
    trade_id = res.json()["trade_id"]
    assert trade_id is not None

    # 3. Alice responds to trade (rejects it)
    client.cookies.set("yoyo_fantasy_tokens", json.dumps([t2.manager_token]))
    res = client.post(
        f"/sports/fantasy/api/league/{league.id}/trades/respond",
        json={"trade_id": trade_id, "action": "reject"},
    )
    assert res.status_code == 200
    assert "rejected" in res.json()["message"]


def test_activity_wire_view_and_api():
    league, t1 = create_league("Activity League", "Tony", "Tony Team")
    avail = get_available_players(league.id, limit=2)
    p1 = avail["players"][0]["player"]["id"]

    client = TestClient(app)
    client.cookies.set("yoyo_fantasy_tokens", json.dumps([t1.manager_token]))
    client.post(f"/sports/fantasy/api/league/{league.id}/waivers/add-drop", json={"add_player_id": p1})

    # 1. HTML view
    res = client.get(f"/sports/fantasy/league/{league.id}/activity")
    assert res.status_code == 200
    assert "League Transaction Wire" in res.text
    assert "added" in res.text

    # 2. JSON API
    res = client.get(f"/sports/fantasy/api/league/{league.id}/activity")
    assert res.status_code == 200
    txs = res.json()["transactions"]
    assert len(txs) >= 1
    assert txs[0]["type"] in ("free_agent_add", "add_drop")
