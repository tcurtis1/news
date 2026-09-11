import pytest

from app.fantasy.db import init_db
from app.fantasy.draft import get_team_roster
from app.fantasy.service import create_league, join_league
from app.fantasy.trades import (
    get_league_trades,
    get_trade_details,
    propose_trade,
    respond_to_trade,
)
from app.fantasy.waivers import add_drop_free_agent, get_available_players


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    test_db = tmp_path / "test_fantasy_trades.db"
    monkeypatch.setattr("app.fantasy.db.DB_PATH", test_db)
    init_db()


def test_propose_and_cancel_trade():
    league, t1 = create_league("Trade League", "Tony", "Tony Team", max_teams=4)
    _, t2, _ = join_league(league.invite_token, "Alice", "Alice Team")

    avail = get_available_players(league.id, limit=5)
    p1 = avail["players"][0]["player"]["id"]
    p2 = avail["players"][1]["player"]["id"]

    # Give p1 to Tony, p2 to Alice
    add_drop_free_agent(league.id, t1.id, p1, None, t1.manager_token)
    add_drop_free_agent(league.id, t2.id, p2, None, t2.manager_token)

    # Tony proposes trading p1 for Alice's p2
    ok, msg, trade_id = propose_trade(
        league_id=league.id,
        proposer_team_id=t1.id,
        recipient_team_id=t2.id,
        proposer_player_ids=[p1],
        recipient_player_ids=[p2],
        note="Fair deal?",
        actor_token=t1.manager_token,
    )
    assert ok is True
    assert trade_id is not None

    trades = get_league_trades(league.id)
    assert len(trades) == 1
    assert trades[0].status == "proposed"
    assert len(trades[0].proposer_sends) == 1
    assert len(trades[0].recipient_sends) == 1

    # Tony cancels trade
    ok, msg = respond_to_trade(league.id, trade_id, "cancel", t1.manager_token)
    assert ok is True
    assert "cancelled" in msg

    tr_after = get_trade_details(trade_id)
    assert tr_after.status == "cancelled"


def test_propose_and_accept_instant_trade():
    league, t1 = create_league("Instant Trade League", "Tony", "Tony Team", max_teams=4)
    _, t2, _ = join_league(league.invite_token, "Alice", "Alice Team")

    # Set trade review to 0 for instant execution
    from app.fantasy.service import update_league_settings
    update_league_settings(league.id, league.commissioner_token, {"trade_review_hours": 0})

    avail = get_available_players(league.id, limit=5)
    p1 = avail["players"][0]["player"]["id"]
    p2 = avail["players"][1]["player"]["id"]

    add_drop_free_agent(league.id, t1.id, p1, None, t1.manager_token)
    add_drop_free_agent(league.id, t2.id, p2, None, t2.manager_token)

    ok, _, trade_id = propose_trade(
        league_id=league.id,
        proposer_team_id=t1.id,
        recipient_team_id=t2.id,
        proposer_player_ids=[p1],
        recipient_player_ids=[p2],
        actor_token=t1.manager_token,
    )
    assert ok is True

    # Alice accepts trade
    ok, msg = respond_to_trade(league.id, trade_id, "accept", t2.manager_token)
    assert ok is True
    assert "TRADE COMPLETED" in msg

    # Verify rosters swapped: Tony now has p2, Alice has p1
    tony_roster = get_team_roster(t1.id)
    alice_roster = get_team_roster(t2.id)

    assert any(rp["player_id"] == p2 for rp in tony_roster)
    assert not any(rp["player_id"] == p1 for rp in tony_roster)

    assert any(rp["player_id"] == p1 for rp in alice_roster)
    assert not any(rp["player_id"] == p2 for rp in alice_roster)


def test_commissioner_veto():
    league, t1 = create_league("Veto League", "Tony", "Tony Team", max_teams=4)
    _, t2, _ = join_league(league.invite_token, "Alice", "Alice Team")

    avail = get_available_players(league.id, limit=5)
    p1 = avail["players"][0]["player"]["id"]
    p2 = avail["players"][1]["player"]["id"]

    add_drop_free_agent(league.id, t1.id, p1, None, t1.manager_token)
    add_drop_free_agent(league.id, t2.id, p2, None, t2.manager_token)

    ok, _, trade_id = propose_trade(league.id, t1.id, t2.id, [p1], [p2], actor_token=t1.manager_token)
    assert ok is True

    # Commissioner vetos trade
    ok, msg = respond_to_trade(league.id, trade_id, "veto", league.commissioner_token)
    assert ok is True
    assert "vetoed" in msg

    tr = get_trade_details(trade_id)
    assert tr.status == "vetoed"
