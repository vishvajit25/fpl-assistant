"""Player scoring model.

v2: a weighted blend of recent form, underlying performance (xG/xA vs actual
returns), upcoming fixture difficulty, value for money, injury-severity-aware
availability, and minutes security — plus a flat set-piece-duty bonus.
Deliberately simple and transparent rather than a black-box ML model, so the
weights can be tuned by hand as we see how recommendations play out.
"""
from dataclasses import dataclass, field
from typing import Optional

POSITION_MAP = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

PLAYER_PHOTO_BASE = "https://resources.premierleague.com/premierleague/photos/players/110x140"

# FPL's own availability status codes.
# a = available, d = doubtful, i = injured, s = suspended, u = unavailable (e.g. on loan elsewhere), n = not in squad.
UNAVAILABLE_STATUSES = {"i", "s", "u", "n"}


# Fixture difficulty ratings (FDR) from the API run 1 (easiest) to 5 (hardest).
# Convert to a 0-10 "friendliness" score, higher = easier fixture.
def fdr_to_friendliness(fdr: int) -> float:
    return (5 - fdr) * 2.5


@dataclass
class PlayerScore:
    id: int
    name: str
    team: str
    position: str
    price: float
    form: float
    total_points: int
    points_per_million: float
    availability: float  # 0-1, injury/suspension-severity-aware
    status: str  # a/d/i/s/u/n
    news: str
    next_fixtures: list[str]
    next_fixtures_detail: list[dict]  # [{opponent, home, difficulty}, ...] for the transfer/chip advisors
    fixture_friendliness: float  # avg over next N fixtures, 0-10
    xg_involvement_per90: float
    overperformance: float  # actual goal involvements minus xG involvements (recent form riding luck vs due a regression)
    starts_ratio: float  # fraction of the season's finished gameweeks the player started, 0-1
    set_piece_duty: list[str]  # e.g. ["Penalties", "Corners"]
    set_piece_bonus: float
    photo_url: str
    selected_by_percent: float  # ownership, 0-100
    ict_index: float  # FPL's Influence/Creativity/Threat composite
    bonus: int  # season bonus points total
    clean_sheets: int
    goals_conceded: int
    xgc_per90: float  # expected goals conceded per 90 (defensive stat, DEF/GKP)
    price_trend: float  # £m price change this gameweek (+ rising, - falling)
    net_transfers_event: int  # transfers in minus transfers out this gameweek
    differential: bool  # low-owned (<10%) player scoring in the top quartile of their position
    score: float = 0.0


WEIGHTS = {
    "form": 0.28,
    "fixture": 0.20,
    "value": 0.12,
    "underlying": 0.12,
    "availability": 0.18,
    "minutes_security": 0.10,
}


def _normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def _availability(el: dict) -> float:
    """0-1 availability score, driven primarily by FPL's status code rather
    than chance_of_playing alone — a null chance_of_playing_next_round means
    "fully fit" for an available player, but also occurs for a long-term
    injury that hasn't had a percentage assigned yet, so status must gate it.
    """
    status = el.get("status") or "a"
    chance = el.get("chance_of_playing_next_round")

    if status in UNAVAILABLE_STATUSES:
        return 0.0
    if status == "d":
        return (chance / 100.0) if chance is not None else 0.5
    # status == "a" (available)
    return 1.0 if chance is None else chance / 100.0


def _set_piece_duty(el: dict) -> tuple[list[str], float]:
    tags = []
    bonus = 0.0
    pens = el.get("penalties_order")
    corners = el.get("corners_and_indirect_freekicks_order")
    freekicks = el.get("direct_freekicks_order")

    if pens == 1:
        tags.append("Penalties")
        bonus += 6.0
    elif pens == 2:
        bonus += 2.0

    if corners == 1:
        tags.append("Corners")
        bonus += 3.0

    if freekicks == 1:
        tags.append("Free-kicks")
        bonus += 3.0

    return tags, bonus


def build_team_lookup(bootstrap: dict) -> dict[int, str]:
    return {t["id"]: t["short_name"] for t in bootstrap["teams"]}


def build_fixture_difficulty(fixtures: list, team_lookup: dict[int, str], upcoming_gw_count: int = 3) -> dict[int, dict]:
    """For each team, the next N fixtures' opponents and difficulty."""
    unplayed = [f for f in fixtures if not f["finished"] and f.get("event")]
    unplayed.sort(key=lambda f: f["event"])

    per_team: dict[int, list] = {tid: [] for tid in team_lookup}
    for f in unplayed:
        home, away = f["team_h"], f["team_a"]
        if len(per_team.get(home, [])) < upcoming_gw_count:
            per_team[home].append({
                "opponent": team_lookup.get(away, "?"),
                "home": True,
                "difficulty": f["team_h_difficulty"],
            })
        if len(per_team.get(away, [])) < upcoming_gw_count:
            per_team[away].append({
                "opponent": team_lookup.get(home, "?"),
                "home": False,
                "difficulty": f["team_a_difficulty"],
            })

    result = {}
    for tid, fx in per_team.items():
        if fx:
            avg_friendliness = sum(fdr_to_friendliness(f["difficulty"]) for f in fx) / len(fx)
        else:
            avg_friendliness = 5.0
        labels = [f"{'vs' if f['home'] else '@'}{f['opponent']}({f['difficulty']})" for f in fx]
        result[tid] = {"friendliness": avg_friendliness, "labels": labels, "fixtures": fx}
    return result


def score_players(bootstrap: dict, fixtures: list, upcoming_gw_count: int = 3) -> list[PlayerScore]:
    team_lookup = build_team_lookup(bootstrap)
    fixture_info = build_fixture_difficulty(fixtures, team_lookup, upcoming_gw_count)

    finished_events = sum(1 for e in bootstrap["events"] if e["finished"])
    # Guard against gameweek 1, before any events are marked finished.
    games_played_denominator = max(finished_events, 1)

    elements = bootstrap["elements"]
    raw = []
    for el in elements:
        if el.get("minutes", 0) <= 0 and el.get("chance_of_playing_next_round") in (0, None):
            # Skip players with zero minutes and no signal they're about to play
            # (keeps the list useful rather than cluttered with unused squad players).
            if el.get("total_points", 0) == 0:
                continue

        team_id = el["team"]
        price = el["now_cost"] / 10.0
        total_points = el["total_points"]
        form = float(el.get("form") or 0.0)
        ppm = round(total_points / price, 2) if price else 0.0

        availability = _availability(el)

        minutes = el.get("minutes") or 0
        per90_factor = (90.0 / minutes) if minutes >= 90 else 0.0
        xgi = float(el.get("expected_goal_involvements") or 0.0)
        xgi_per90 = xgi * per90_factor if per90_factor else 0.0

        goals = el.get("goals_scored", 0)
        assists = el.get("assists", 0)
        actual_involvements = goals + assists
        overperformance = actual_involvements - xgi

        starts = el.get("starts") or 0
        starts_ratio = min(starts / games_played_denominator, 1.0)

        set_piece_tags, set_piece_bonus = _set_piece_duty(el)
        photo_url = f"{PLAYER_PHOTO_BASE}/p{el['code']}.png"

        fx = fixture_info.get(team_id, {"friendliness": 5.0, "labels": [], "fixtures": []})

        xgc = float(el.get("expected_goals_conceded") or 0.0)
        xgc_per90 = xgc * per90_factor if per90_factor else 0.0

        raw.append({
            "id": el["id"],
            "name": el["web_name"],
            "team": team_lookup.get(team_id, "?"),
            "position": POSITION_MAP.get(el["element_type"], "?"),
            "price": price,
            "form": form,
            "total_points": total_points,
            "points_per_million": ppm,
            "availability": availability,
            "status": el.get("status") or "a",
            "news": el.get("news") or "",
            "next_fixtures": fx["labels"],
            "next_fixtures_detail": fx["fixtures"],
            "fixture_friendliness": fx["friendliness"],
            "xg_involvement_per90": round(xgi_per90, 2),
            "overperformance": round(overperformance, 2),
            "starts_ratio": round(starts_ratio, 2),
            "set_piece_duty": set_piece_tags,
            "set_piece_bonus": set_piece_bonus,
            "photo_url": photo_url,
            "selected_by_percent": float(el.get("selected_by_percent") or 0.0),
            "ict_index": float(el.get("ict_index") or 0.0),
            "bonus": el.get("bonus", 0),
            "clean_sheets": el.get("clean_sheets", 0),
            "goals_conceded": el.get("goals_conceded", 0),
            "xgc_per90": round(xgc_per90, 2),
            "price_trend": round(el.get("cost_change_event", 0) / 10.0, 1),
            "net_transfers_event": el.get("transfers_in_event", 0) - el.get("transfers_out_event", 0),
        })

    forms = _normalize([p["form"] for p in raw])
    fixtures_n = _normalize([p["fixture_friendliness"] for p in raw])
    values = _normalize([p["points_per_million"] for p in raw])
    underlying = _normalize([p["xg_involvement_per90"] for p in raw])

    scores_by_position: dict[str, list[float]] = {}
    prelim = []
    for p, f_n, fx_n, v_n, u_n in zip(raw, forms, fixtures_n, values, underlying):
        composite = (
            WEIGHTS["form"] * f_n
            + WEIGHTS["fixture"] * fx_n
            + WEIGHTS["value"] * v_n
            + WEIGHTS["underlying"] * u_n
            + WEIGHTS["availability"] * p["availability"]
            + WEIGHTS["minutes_security"] * p["starts_ratio"]
        )
        final_score = round(composite * 100 + p["set_piece_bonus"], 1)
        prelim.append((p, final_score))
        scores_by_position.setdefault(p["position"], []).append(final_score)

    # A "differential" is a lightly-owned player (<10% selected) scoring in
    # the top quartile of their own position — i.e. a plausible punt that
    # most rivals' teams won't already have.
    top_quartile_by_position = {
        pos: sorted(scores)[int(len(scores) * 0.75)] if scores else 0.0
        for pos, scores in scores_by_position.items()
    }

    scored = []
    for p, final_score in prelim:
        differential = (
            p["selected_by_percent"] < 10.0
            and final_score >= top_quartile_by_position.get(p["position"], 0.0)
        )
        scored.append(PlayerScore(score=final_score, differential=differential, **p))

    scored.sort(key=lambda p: p.score, reverse=True)
    return scored
