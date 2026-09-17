"""Transfer suggestions for a manager's weakest squad players.

v2: alongside the model's overall score, this explicitly looks at each
candidate's next 3 fixtures — home or away, and FPL's own fixture
difficulty rating (FDR) per fixture — rather than leaning on a single
opaque composite number. Home fixtures are treated as slightly easier
(home advantage), and a run with several difficulty<=2 games is preferred
over one that merely averages well but hides a couple of very hard
away trips. Kept transparent and rule-based, same spirit as model.py.
"""
from model import PlayerScore, fdr_to_friendliness

HOME_ADVANTAGE = 0.4  # knocked off a home fixture's difficulty before rating it
WINNABLE_FDR = 2  # difficulty at/below this counts as a "winnable" fixture
TOUGH_FDR = 4  # difficulty at/above this counts as a "tough" fixture

PRICE_HEADROOM = 1.5  # max £m a replacement can cost more than the outgoing player
MIN_COMBINED_GAIN = 6.0  # model-score + fixture-adjusted gain needed to suggest a swap
FIXTURE_WEIGHT = 1.3  # how much a better/worse run of fixtures moves the decision


def fixture_run(fixtures_detail: list[dict]) -> dict:
    """Summarize a player's next-3-fixture run: a home/away-aware rating
    (0-10, higher = easier), counts of winnable/tough fixtures, and a
    human-readable summary like "vs BOU (H, FDR2), @ CHE (FDR3)".
    """
    if not fixtures_detail:
        return {"rating": 5.0, "home_count": 0, "winnable_count": 0, "tough_count": 0, "summary": "No fixtures in range"}

    ratings = []
    winnable = tough = home_count = 0
    parts = []
    for fx in fixtures_detail:
        difficulty = fx["difficulty"]
        adjusted = max(1.0, difficulty - HOME_ADVANTAGE) if fx["home"] else difficulty
        ratings.append(fdr_to_friendliness(adjusted))
        if difficulty <= WINNABLE_FDR:
            winnable += 1
        if difficulty >= TOUGH_FDR:
            tough += 1
        if fx["home"]:
            home_count += 1
        parts.append(f"{'vs' if fx['home'] else '@'} {fx['opponent']} ({'H' if fx['home'] else 'A'}, FDR{difficulty})")

    return {
        "rating": round(sum(ratings) / len(ratings), 1),
        "home_count": home_count,
        "winnable_count": winnable,
        "tough_count": tough,
        "summary": ", ".join(parts),
    }


def _reason(out_name: str, out_run: dict, in_name: str, in_run: dict, score_gain: float) -> str:
    bits = [f"model score +{score_gain:.1f}"] if score_gain > 0.5 else []
    if in_run["winnable_count"] > out_run["winnable_count"]:
        bits.append(f"{in_run['winnable_count']} winnable fixture(s) vs {out_run['winnable_count']} for {out_name}")
    if in_run["tough_count"] < out_run["tough_count"]:
        bits.append(f"avoids {out_name}'s {out_run['tough_count']} tough fixture(s)")
    if in_run["home_count"] > out_run["home_count"]:
        bits.append(f"{in_run['home_count']} of the next 3 at home")
    if not bits:
        bits.append("stronger next-3 fixture run")
    return f"{in_name}: " + "; ".join(bits) + "."


def suggest_transfers(my_players: list[dict], all_scores: dict[int, PlayerScore], count: int = 5) -> list[dict]:
    owned_ids = {p["id"] for p in my_players}
    already_suggested: set[int] = set()
    suggestions = []

    for owned in sorted(my_players, key=lambda p: p["score"])[:count]:
        out_run = fixture_run(owned.get("next_fixtures_detail", []))

        pool = [
            p for p in all_scores.values()
            if p.position == owned["position"]
            and p.id not in owned_ids
            and p.id not in already_suggested
            and p.price <= owned["price"] + PRICE_HEADROOM
        ]

        best, best_gain, best_in_run = None, 0.0, None
        for cand in pool:
            in_run = fixture_run(cand.next_fixtures_detail)
            score_gain = cand.score - owned["score"]
            fixture_gain = in_run["rating"] - out_run["rating"]
            combined_gain = score_gain + fixture_gain * FIXTURE_WEIGHT
            if combined_gain > best_gain:
                best, best_gain, best_in_run = cand, combined_gain, in_run

        if best and best_gain >= MIN_COMBINED_GAIN:
            already_suggested.add(best.id)
            score_gain = round(best.score - owned["score"], 1)
            suggestions.append({
                "out": {
                    "name": owned["name"], "score": owned["score"], "price": owned["price"],
                    "fixture_run": out_run,
                },
                "in": {
                    "name": best.name, "score": best.score, "price": best.price,
                    "fixture_run": best_in_run,
                },
                "score_gain": score_gain,
                "combined_gain": round(best_gain, 1),
                "reason": _reason(owned["name"], out_run, best.name, best_in_run, score_gain),
            })

    return suggestions
