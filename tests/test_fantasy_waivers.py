import pytest
from datetime import datetime, timedelta, timezone

from app.fantasy.db import init_db
from app.fantasy.models import LeagueStatus
from app.fantasy.service import create_league, join_league
from app.fantasy.waivers import (
    add_drop_free_agent,
    cancel_waiver_claim,
    get_available_players,
    get_league_transactions,
    get_team_waiver_claims,
    process_waivers,
    submit_waiver_claim,
)


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    test_db = tmp_path / "test_fantasy_waivers.db"
    monkeypatch.setattr("app.fantasy.db.DB_PATH", test_db)
    init_db()


def test_free_agent_instant_add_and_drop():
    league, t1 = create_league("Waiver League", "Tony", "Tony Team", max_teams=4)
    _, t2, _ = join_league(league.invite_token, "Alice", "Alice Team")

    # Get available players
    avail = get_available_players(league.id, limit=10)
    assert len(avail["players"]) > 0
    p1 = avail["players"][0]["player"]
    p2 = avail["players"][1]["player"]

    # Tony adds p1 as free agent
    ok, msg = add_drop_free_agent(league.id, t1.id, p1["id"], None, t1.manager_token)
    assert ok is True
    assert p1["name"] in msg

    # Now p1 is owned, shouldn't appear in available players
    avail_after = get_available_players(league.id, limit=10)
    avail_ids = [item["player"]["id"] for item in avail_after["players"]]
    assert p1["id"] not in avail_ids

    # Tony adds p2 and drops p1
    ok, msg = add_drop_free_agent(league.id, t1.id, p2["id"], p1["id"], t1.manager_token)
    assert ok is True
    assert "dropped" in msg

    # p1 should now be on WAIVERS (since dropped)
    avail_p1 = get_available_players(league.id, query=p1["name"])
    found = next((item for item in avail_p1["players"] if item["player"]["id"] == p1["id"]), None)
    assert found is not None
    assert found["status"] == "WAIVERS"

    # Alice tries to instantly add p1 as free agent -> should fail because on waivers
    ok, err = add_drop_free_agent(league.id, t2.id, p1["id"], None, t2.manager_token)
    assert ok is False
    assert "waivers" in err.lower()


def test_faab_waiver_claim_and_processing():
    league, t1 = create_league("FAAB League", "Tony", "Tony Team", waiver_type="faab", faab_budget=100)
    _, t2, _ = join_league(league.invite_token, "Alice", "Alice Team")

    avail = get_available_players(league.id, limit=5)
    target_player = avail["players"][0]["player"]
    target_id = target_player["id"]

    # Tony bids $25
    ok, msg = submit_waiver_claim(league.id, t1.id, target_id, None, bid_amount=25, actor_token=t1.manager_token)
    assert ok is True

    # Alice bids $40
    ok, msg = submit_waiver_claim(league.id, t2.id, target_id, None, bid_amount=40, actor_token=t2.manager_token)
    assert ok is True

    # Check Alice pending claims
    claims = get_team_waiver_claims(league.id, t2.id)
    assert len(claims) == 1
    assert claims[0].bid_amount == 40

    # Process waivers
    ok, summary_msg, data = process_waivers(league.id, league.commissioner_token)
    assert ok is True
    assert data["awarded_count"] == 1
    assert data["failed_count"] == 1

    # Alice should have won because $40 > $25
    from app.fantasy.draft import get_team_roster
    alice_roster = get_team_roster(t2.id)
    assert any(rp["player_id"] == target_id for rp in alice_roster)

    # Alice FAAB deducted from 100 to 60
    from app.fantasy.service import get_league
    l_updated = get_league(league.id)
    t2_updated = next(t for t in l_updated.teams if t.id == t2.id)
    assert t2_updated.faab_balance == 60


def test_rolling_waiver_priority_reset():
    league, t1 = create_league("Rolling League", "Tony", "Tony Team", waiver_type="rolling")
    _, t2, _ = join_league(league.invite_token, "Alice", "Alice Team")

    avail = get_available_players(league.id, limit=5)
    target_id = avail["players"][0]["player"]["id"]

    # In creation order, Tony has waiver_priority=1, Alice has waiver_priority=2
    # Both submit waiver claims
    submit_waiver_claim(league.id, t1.id, target_id, None, priority=1, actor_token=t1.manager_token)
    submit_waiver_claim(league.id, t2.id, target_id, None, priority=1, actor_token=t2.manager_token)

    ok, _, data = process_waivers(league.id)
    assert ok is True
    assert data["awarded_count"] == 1

    # Tony should win because priority 1 < 2
    from app.fantasy.draft import get_team_roster
    tony_roster = get_team_roster(t1.id)
    assert any(rp["player_id"] == target_id for rp in tony_roster)

    # Tony should now be moved to last priority (priority 2), Alice moves to priority 1
    from app.fantasy.service import get_league
    l_updated = get_league(league.id)
    t1_up = next(t for t in l_updated.teams if t.id == t1.id)
    t2_up = next(t for t in l_updated.teams if t.id == t2.id)
    assert t1_up.waiver_priority == 2
    assert t2_up.waiver_priority == 1


def test_claim_cancellation_and_transactions():
    league, t1 = create_league("Cancel League", "Tony", "Tony Team")
    avail = get_available_players(league.id, limit=2)
    p1 = avail["players"][0]["player"]["id"]

    submit_waiver_claim(league.id, t1.id, p1, None, bid_amount=10, actor_token=t1.manager_token)
    claims = get_team_waiver_claims(league.id, t1.id, status="pending")
    assert len(claims) == 1

    # Cancel claim
    ok, msg = cancel_waiver_claim(league.id, t1.id, claims[0].id, t1.manager_token)
    assert ok is True

    pending_after = get_team_waiver_claims(league.id, t1.id, status="pending")
    assert len(pending_after) == 0

    # Add a free agent to generate a transaction
    add_drop_free_agent(league.id, t1.id, p1, None, t1.manager_token)
    txs = get_league_transactions(league.id)
    assert len(txs) >= 1
    assert txs[0].type in ("free_agent_add", "add_drop")
