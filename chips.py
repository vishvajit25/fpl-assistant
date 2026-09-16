"""Lightweight, transparent chip-usage advice for a manager's squad.

Not a solver — a set of rule-of-thumb heuristics (unavailable players,
fixture ease, bench quality) a manager can sanity-check against their own
judgement. Deliberately simple and explainable rather than a black box,
matching the spirit of the player-scoring model in model.py.
"""
from typing import Optional

CHIP_NAMES = {
    "wildcard": "Wildcard",
    "3xc": "Triple Captain",
    "bboost": "Bench Boost",
    "freehit": "Free Hit",
}

# Since 2023/24, each chip can be played twice a season — once before this
# gameweek, once after — so a chip used in the first half is available again
# in the second.
HALF_SPLIT_EVENT = 19


def _half(event: int) -> int:
    return 1 if event <= HALF_SPLIT_EVENT else 2


def _used_this_half(chip_key: str, chips_used: list[dict], current_event: int) -> Optional[int]:
    current_half = _half(current_event)
    for c in chips_used:
        if c.get("name") == chip_key and _half(c.get("event", 1)) == current_half:
            return c.get("event")
    return None


def _avg(values: list[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def suggest_chips(squad: list[dict], chips_used: list[dict], current_event: int) -> list[dict]:
    """squad: my-team player dicts (score/status/availability/fixture_friendliness/
    next_fixtures/multiplier — multiplier 0 means benched)."""
    starting = [p for p in squad if p.get("multiplier", 1) > 0]
    bench = [p for p in squad if p.get("multiplier", 1) == 0]

    unavailable = [p for p in squad if p.get("status") in ("d", "i", "s", "u") or p.get("availability", 1) < 0.5]
    blank = [p for p in squad if not p.get("next_fixtures")]
    starting_friendliness = _avg(p.get("fixture_friendliness", 5) for p in starting)
    bench_score = _avg(p.get("score", 0) for p in bench)
    bench_friendliness = _avg(p.get("fixture_friendliness", 5) for p in bench)

    top = max(squad, key=lambda p: p.get("score", 0)) if squad else None
    rest_scores = sorted((p.get("score", 0) for p in squad if p is not top), reverse=True)
    score_gap = (top["score"] - rest_scores[0]) if top and rest_scores else 0

    results = []
    for key, label in CHIP_NAMES.items():
        used_event = _used_this_half(key, chips_used, current_event)
        if used_event:
            results.append({
                "chip": label, "available": False, "recommended": False,
                "reason": f"Already used this half of the season (Gameweek {used_event}).",
            })
            continue

        recommended, reason = False, "No strong signal this gameweek — save it for now."

        if key == "wildcard":
            if len(unavailable) >= 3:
                names = ", ".join(p["name"] for p in unavailable[:4])
                recommended = True
                reason = f"{len(unavailable)} players are doubtful, injured, or suspended ({names}) — worth a squad refresh."
            elif starting_friendliness < 4.5:
                recommended = True
                reason = f"Tough run of fixtures for your starting XI (avg {starting_friendliness:.1f}/10 over the next few gameweeks)."

        elif key == "freehit":
            if len(blank) >= 3:
                names = ", ".join(p["name"] for p in blank[:4])
                recommended = True
                reason = f"{len(blank)} players have no fixture in the lookahead window ({names}) — a one-week raid could cover the gap."

        elif key == "bboost":
            if bench_score >= 45 and bench_friendliness >= 6:
                recommended = True
                reason = f"Bench is in good shape (avg score {bench_score:.0f}, avg fixture ease {bench_friendliness:.1f}/10)."

        elif key == "3xc":
            if top and top.get("fixture_friendliness", 0) >= 8 and score_gap > 15:
                recommended = True
                reason = f"{top['name']} has a very favourable fixture and is well clear of the rest of your squad on model score."

        results.append({"chip": label, "available": True, "recommended": recommended, "reason": reason})

    return results
