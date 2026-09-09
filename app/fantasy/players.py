"""NFL player directory and projections for Fantasy Football."""

from __future__ import annotations

from typing import Dict, List, Optional
from app.fantasy.models import Player

# Seed catalog of top fantasy-relevant NFL players for 2026 season.
_SEED_PLAYERS: List[Dict] = [
    # Top QBs
    {"id": "nfl-3918298", "name": "Josh Allen", "pos": "QB", "team": "BUF", "bye": 12, "adp": 22.0, "proj": 380.0},
    {"id": "nfl-4040715", "name": "Jalen Hurts", "pos": "QB", "team": "PHI", "bye": 5, "adp": 28.0, "proj": 365.0},
    {"id": "nfl-3139477", "name": "Patrick Mahomes", "pos": "QB", "team": "KC", "bye": 6, "adp": 35.0, "proj": 360.0},
    {"id": "nfl-3916387", "name": "Lamar Jackson", "pos": "QB", "team": "BAL", "bye": 14, "adp": 32.0, "proj": 370.0},
    {"id": "nfl-4426348", "name": "C.J. Stroud", "pos": "QB", "team": "HOU", "bye": 14, "adp": 45.0, "proj": 340.0},
    {"id": "nfl-4241479", "name": "Joe Burrow", "pos": "QB", "team": "CIN", "bye": 12, "adp": 50.0, "proj": 335.0},
    {"id": "nfl-4431611", "name": "Jayden Daniels", "pos": "QB", "team": "WAS", "bye": 14, "adp": 60.0, "proj": 325.0},
    {"id": "nfl-4432577", "name": "Caleb Williams", "pos": "QB", "team": "CHI", "bye": 7, "adp": 75.0, "proj": 310.0},
    {"id": "nfl-4361741", "name": "Jordan Love", "pos": "QB", "team": "GB", "bye": 10, "adp": 70.0, "proj": 320.0},
    {"id": "nfl-4242335", "name": "Brock Purdy", "pos": "QB", "team": "SF", "bye": 9, "adp": 85.0, "proj": 305.0},
    {"id": "nfl-4038941", "name": "Kyler Murray", "pos": "QB", "team": "ARI", "bye": 11, "adp": 68.0, "proj": 320.0},
    {"id": "nfl-3052587", "name": "Dak Prescott", "pos": "QB", "team": "DAL", "bye": 7, "adp": 78.0, "proj": 315.0},
    {"id": "nfl-4362887", "name": "Tua Tagovailoa", "pos": "QB", "team": "MIA", "bye": 6, "adp": 95.0, "proj": 290.0},
    {"id": "nfl-4040715", "name": "Trevor Lawrence", "pos": "QB", "team": "JAX", "bye": 12, "adp": 105.0, "proj": 285.0},
    {"id": "nfl-4360310", "name": "Justin Herbert", "pos": "QB", "team": "LAC", "bye": 5, "adp": 110.0, "proj": 280.0},

    # Top RBs
    {"id": "nfl-3117251", "name": "Christian McCaffrey", "pos": "RB", "team": "SF", "bye": 9, "adp": 1.5, "proj": 320.0},
    {"id": "nfl-4430737", "name": "Bijan Robinson", "pos": "RB", "team": "ATL", "bye": 12, "adp": 5.0, "proj": 290.0},
    {"id": "nfl-4429013", "name": "Breece Hall", "pos": "RB", "team": "NYJ", "bye": 12, "adp": 6.0, "proj": 285.0},
    {"id": "nfl-4426515", "name": "Jahmyr Gibbs", "pos": "RB", "team": "DET", "bye": 5, "adp": 12.0, "proj": 260.0},
    {"id": "nfl-3929630", "name": "Saquon Barkley", "pos": "RB", "team": "PHI", "bye": 5, "adp": 9.0, "proj": 275.0},
    {"id": "nfl-4258173", "name": "Jonathan Taylor", "pos": "RB", "team": "IND", "bye": 14, "adp": 15.0, "proj": 255.0},
    {"id": "nfl-3046779", "name": "Derrick Henry", "pos": "RB", "team": "BAL", "bye": 14, "adp": 18.0, "proj": 250.0},
    {"id": "nfl-4241457", "name": "Travis Etienne Jr.", "pos": "RB", "team": "JAX", "bye": 12, "adp": 24.0, "proj": 240.0},
    {"id": "nfl-4374302", "name": "Kyren Williams", "pos": "RB", "team": "LAR", "bye": 6, "adp": 20.0, "proj": 245.0},
    {"id": "nfl-4429795", "name": "De'Von Achane", "pos": "RB", "team": "MIA", "bye": 6, "adp": 22.0, "proj": 235.0},
    {"id": "nfl-4035687", "name": "Josh Jacobs", "pos": "RB", "team": "GB", "bye": 10, "adp": 30.0, "proj": 230.0},
    {"id": "nfl-4361529", "name": "Kenneth Walker III", "pos": "RB", "team": "SEA", "bye": 10, "adp": 42.0, "proj": 220.0},
    {"id": "nfl-4361408", "name": "James Cook", "pos": "RB", "team": "BUF", "bye": 12, "adp": 40.0, "proj": 225.0},
    {"id": "nfl-3042519", "name": "Alvin Kamara", "pos": "RB", "team": "NO", "bye": 12, "adp": 38.0, "proj": 228.0},
    {"id": "nfl-3128720", "name": "Joe Mixon", "pos": "RB", "team": "HOU", "bye": 14, "adp": 48.0, "proj": 215.0},
    {"id": "nfl-4361777", "name": "Isiah Pacheco", "pos": "RB", "team": "KC", "bye": 6, "adp": 36.0, "proj": 220.0},
    {"id": "nfl-4361307", "name": "Rachaad White", "pos": "RB", "team": "TB", "bye": 11, "adp": 44.0, "proj": 210.0},
    {"id": "nfl-4241474", "name": "D'Andre Swift", "pos": "RB", "team": "CHI", "bye": 7, "adp": 65.0, "proj": 200.0},

    # Top WRs
    {"id": "nfl-4262921", "name": "CeeDee Lamb", "pos": "WR", "team": "DAL", "bye": 7, "adp": 2.0, "proj": 330.0},
    {"id": "nfl-3116365", "name": "Tyreek Hill", "pos": "WR", "team": "MIA", "bye": 6, "adp": 3.0, "proj": 325.0},
    {"id": "nfl-4262921", "name": "Justin Jefferson", "pos": "WR", "team": "MIN", "bye": 6, "adp": 4.0, "proj": 320.0},
    {"id": "nfl-4362628", "name": "Amon-Ra St. Brown", "pos": "WR", "team": "DET", "bye": 5, "adp": 7.0, "proj": 305.0},
    {"id": "nfl-4241389", "name": "Ja'Marr Chase", "pos": "WR", "team": "CIN", "bye": 12, "adp": 8.0, "proj": 300.0},
    {"id": "nfl-4047646", "name": "A.J. Brown", "pos": "WR", "team": "PHI", "bye": 5, "adp": 10.0, "proj": 280.0},
    {"id": "nfl-4372016", "name": "Garrett Wilson", "pos": "WR", "team": "NYJ", "bye": 12, "adp": 13.0, "proj": 270.0},
    {"id": "nfl-4429022", "name": "Puka Nacua", "pos": "WR", "team": "LAR", "bye": 6, "adp": 14.0, "proj": 265.0},
    {"id": "nfl-4432773", "name": "Marvin Harrison Jr.", "pos": "WR", "team": "ARI", "bye": 11, "adp": 16.0, "proj": 255.0},
    {"id": "nfl-4372016", "name": "Chris Olave", "pos": "WR", "team": "NO", "bye": 12, "adp": 26.0, "proj": 245.0},
    {"id": "nfl-4361579", "name": "Drake London", "pos": "WR", "team": "ATL", "bye": 12, "adp": 25.0, "proj": 250.0},
    {"id": "nfl-3915416", "name": "DJ Moore", "pos": "WR", "team": "CHI", "bye": 7, "adp": 34.0, "proj": 240.0},
    {"id": "nfl-4038933", "name": "DK Metcalf", "pos": "WR", "team": "SEA", "bye": 10, "adp": 37.0, "proj": 235.0},
    {"id": "nfl-3932905", "name": "Deebo Samuel", "pos": "WR", "team": "SF", "bye": 9, "adp": 33.0, "proj": 238.0},
    {"id": "nfl-4241463", "name": "Brandon Aiyuk", "pos": "WR", "team": "SF", "bye": 9, "adp": 31.0, "proj": 242.0},
    {"id": "nfl-2976212", "name": "Mike Evans", "pos": "WR", "team": "TB", "bye": 11, "adp": 29.0, "proj": 245.0},
    {"id": "nfl-4241389", "name": "Nico Collins", "pos": "WR", "team": "HOU", "bye": 14, "adp": 27.0, "proj": 248.0},
    {"id": "nfl-4430027", "name": "Malik Nabers", "pos": "WR", "team": "NYG", "bye": 11, "adp": 39.0, "proj": 230.0},
    {"id": "nfl-4240586", "name": "Michael Pittman Jr.", "pos": "WR", "team": "IND", "bye": 14, "adp": 41.0, "proj": 230.0},
    {"id": "nfl-4241372", "name": "Tee Higgins", "pos": "WR", "team": "CIN", "bye": 12, "adp": 46.0, "proj": 215.0},
    {"id": "nfl-4361370", "name": "George Pickens", "pos": "WR", "team": "PIT", "bye": 9, "adp": 52.0, "proj": 210.0},
    {"id": "nfl-4426515", "name": "Zay Flowers", "pos": "WR", "team": "BAL", "bye": 14, "adp": 54.0, "proj": 212.0},
    {"id": "nfl-4426354", "name": "Tank Dell", "pos": "WR", "team": "HOU", "bye": 14, "adp": 56.0, "proj": 208.0},
    {"id": "nfl-4241389", "name": "Jaylen Waddle", "pos": "WR", "team": "MIA", "bye": 6, "adp": 38.0, "proj": 225.0},

    # Top TEs
    {"id": "nfl-4432708", "name": "Sam LaPorta", "pos": "TE", "team": "DET", "bye": 5, "adp": 27.0, "proj": 210.0},
    {"id": "nfl-15847", "name": "Travis Kelce", "pos": "TE", "team": "KC", "bye": 6, "adp": 29.0, "proj": 215.0},
    {"id": "nfl-4361579", "name": "Trey McBride", "pos": "TE", "team": "ARI", "bye": 11, "adp": 43.0, "proj": 195.0},
    {"id": "nfl-3914856", "name": "Mark Andrews", "pos": "TE", "team": "BAL", "bye": 14, "adp": 49.0, "proj": 190.0},
    {"id": "nfl-4432773", "name": "Brock Bowers", "pos": "TE", "team": "LV", "bye": 10, "adp": 58.0, "proj": 180.0},
    {"id": "nfl-3051389", "name": "George Kittle", "pos": "TE", "team": "SF", "bye": 9, "adp": 62.0, "proj": 185.0},
    {"id": "nfl-4426515", "name": "Dalton Kincaid", "pos": "TE", "team": "BUF", "bye": 12, "adp": 55.0, "proj": 185.0},
    {"id": "nfl-4241479", "name": "Kyle Pitts", "pos": "TE", "team": "ATL", "bye": 12, "adp": 64.0, "proj": 175.0},
    {"id": "nfl-4040715", "name": "Dallas Goedert", "pos": "TE", "team": "PHI", "bye": 5, "adp": 98.0, "proj": 150.0},
    {"id": "nfl-3915416", "name": "David Njoku", "pos": "TE", "team": "CLE", "bye": 10, "adp": 88.0, "proj": 160.0},
    {"id": "nfl-4361529", "name": "Jake Ferguson", "pos": "TE", "team": "DAL", "bye": 7, "adp": 82.0, "proj": 165.0},

    # Top Kickers
    {"id": "nfl-15683", "name": "Justin Tucker", "pos": "K", "team": "BAL", "bye": 14, "adp": 130.0, "proj": 140.0},
    {"id": "nfl-4040715", "name": "Brandon Aubrey", "pos": "K", "team": "DAL", "bye": 7, "adp": 125.0, "proj": 145.0},
    {"id": "nfl-3051389", "name": "Harrison Butker", "pos": "K", "team": "KC", "bye": 6, "adp": 132.0, "proj": 138.0},
    {"id": "nfl-4361579", "name": "Cameron Dicker", "pos": "K", "team": "LAC", "bye": 5, "adp": 140.0, "proj": 130.0},
    {"id": "nfl-3914856", "name": "Jake Moody", "pos": "K", "team": "SF", "bye": 9, "adp": 142.0, "proj": 132.0},
    {"id": "nfl-4038933", "name": "Evan McPherson", "pos": "K", "team": "CIN", "bye": 12, "adp": 145.0, "proj": 128.0},
    {"id": "nfl-4241389", "name": "Tyler Bass", "pos": "K", "team": "BUF", "bye": 12, "adp": 148.0, "proj": 125.0},

    # Top Defenses (DST)
    {"id": "dst-bal", "name": "Ravens D/ST", "pos": "DST", "team": "BAL", "bye": 14, "adp": 120.0, "proj": 135.0},
    {"id": "dst-sf", "name": "49ers D/ST", "pos": "DST", "team": "SF", "bye": 9, "adp": 122.0, "proj": 130.0},
    {"id": "dst-nyj", "name": "Jets D/ST", "pos": "DST", "team": "NYJ", "bye": 12, "adp": 124.0, "proj": 128.0},
    {"id": "dst-dal", "name": "Cowboys D/ST", "pos": "DST", "team": "DAL", "bye": 7, "adp": 126.0, "proj": 125.0},
    {"id": "dst-cle", "name": "Browns D/ST", "pos": "DST", "team": "CLE", "bye": 10, "adp": 128.0, "proj": 122.0},
    {"id": "dst-kc", "name": "Chiefs D/ST", "pos": "DST", "team": "KC", "bye": 6, "adp": 134.0, "proj": 118.0},
    {"id": "dst-buf", "name": "Bills D/ST", "pos": "DST", "team": "BUF", "bye": 12, "adp": 136.0, "proj": 116.0},
    {"id": "dst-pit", "name": "Steelers D/ST", "pos": "DST", "team": "PIT", "bye": 9, "adp": 138.0, "proj": 114.0},
]


def load_seed_players() -> List[Player]:
    players: List[Player] = []
    for item in _SEED_PLAYERS:
        pid = item["id"]
        # Standard ESPN athlete headshot URL pattern
        headshot = f"https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/{pid.replace('nfl-', '')}.png&w=96&h=70&cb=1" if not pid.startswith("dst-") else None
        players.append(Player(
            id=pid,
            name=item["name"],
            position=item["pos"],
            nfl_team=item["team"],
            bye_week=item["bye"],
            adp=item.get("adp", 999.0),
            projected_points=item.get("proj", 0.0),
            status="ACT",
            headshot_url=headshot,
        ))
    return sorted(players, key=lambda p: (p.adp, -p.projected_points))
