import asyncio
from dataclasses import asdict
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

import chips
import fpl_client
import transfers
from model import score_players
from optimizer import build_squad

app = FastAPI(title="FPL Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _current_event(bootstrap: dict) -> int:
    for e in bootstrap["events"]:
        if e["is_next"]:
            return e["id"]
    for e in bootstrap["events"]:
        if e["is_current"]:
            return e["id"] + 1
    return 1


@app.get("/api/health")
async def health():
    return {"status": "ok"}


SORT_KEYS = {
    "score": lambda p: p.score,
    "form": lambda p: p.form,
    "value": lambda p: p.points_per_million,
    "ownership": lambda p: p.selected_by_percent,
    "total_points": lambda p: p.total_points,
    "ict": lambda p: p.ict_index,
    "price": lambda p: p.price,
}


@app.get("/api/rankings")
async def rankings(
    position: Optional[str] = Query(None, description="GKP, DEF, MID, FWD"),
    limit: int = 30,
    max_price: Optional[float] = None,
    search: Optional[str] = None,
    sort_by: str = "score",
    differential_only: bool = False,
):
    bootstrap = await fpl_client.get_bootstrap()
    fixtures = await fpl_client.get_fixtures()
    players = score_players(bootstrap, fixtures)

    if position:
        players = [p for p in players if p.position == position.upper()]
    if max_price is not None:
        players = [p for p in players if p.price <= max_price]
    if search:
        q = search.strip().lower()
        players = [p for p in players if q in p.name.lower() or q in p.team.lower()]
    if differential_only:
        players = [p for p in players if p.differential]

    key_fn = SORT_KEYS.get(sort_by, SORT_KEYS["score"])
    players.sort(key=key_fn, reverse=True)

    return {
        "gameweek": _current_event(bootstrap),
        "count": len(players[:limit]),
        "players": [asdict(p) for p in players[:limit]],
    }


NEWS_SOURCES = [
    ("r/FantasyPL", fpl_client.get_community_news),
    ("Fantasy Football Scout", fpl_client.get_ffscout_news),
]


@app.get("/api/news")
async def news(limit: int = 20):
    """Community news & opinion, pooled from multiple free sources (Reddit's
    r/FantasyPL + Fantasy Football Scout's RSS feed). Best-effort per
    source: if one is unreachable or rate-limits us, the other still comes
    through rather than failing the whole request.
    """
    results = await asyncio.gather(
        *(fetch(limit=limit) for _, fetch in NEWS_SOURCES),
        return_exceptions=True,
    )

    posts, sources_ok, sources_failed = [], [], []
    for (label, _), result in zip(NEWS_SOURCES, results):
        if isinstance(result, Exception):
            sources_failed.append(label)
        else:
            posts.extend(result)
            sources_ok.append(label)

    posts.sort(key=lambda p: p.get("created_utc") or 0, reverse=True)

    return {
        "posts": posts,
        "sources": sources_ok,
        "failed_sources": sources_failed,
        "error": "No news sources available right now." if not sources_ok else None,
    }


@app.get("/api/squad/suggested")
async def suggested_squad(budget: float = 100.0):
    bootstrap = await fpl_client.get_bootstrap()
    fixtures = await fpl_client.get_fixtures()
    players = score_players(bootstrap, fixtures)
    result = build_squad(players, budget=budget)

    return {
        "gameweek": _current_event(bootstrap),
        "total_cost": result["total_cost"],
        "budget_remaining": result["budget_remaining"],
        "captain": asdict(result["captain"]) if result["captain"] else None,
        "vice_captain": asdict(result["vice_captain"]) if result["vice_captain"] else None,
        "starting_xi": [asdict(p) for p in result["starting_xi"]],
        "bench": [asdict(p) for p in result["bench"]],
    }


@app.get("/api/my-team/{team_id}")
async def my_team(team_id: int):
    bootstrap = await fpl_client.get_bootstrap()
    fixtures = await fpl_client.get_fixtures()
    all_scores = {p.id: p for p in score_players(bootstrap, fixtures)}

    try:
        entry = await fpl_client.get_entry(team_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Team not found. Check your FPL team ID.")

    event = _current_event(bootstrap) - 1
    if event < 1:
        event = 1

    try:
        picks_data = await fpl_client.get_entry_picks(team_id, event)
    except Exception:
        raise HTTPException(status_code=404, detail=f"No picks found for gameweek {event} yet.")

    my_players = []
    for pick in picks_data.get("picks", []):
        pid = pick["element"]
        p = all_scores.get(pid)
        if p:
            my_players.append({
                **asdict(p),
                "is_captain": pick.get("is_captain", False),
                "is_vice_captain": pick.get("is_vice_captain", False),
                "multiplier": pick.get("multiplier", 1),
            })

    my_players.sort(key=lambda p: p["score"], reverse=True)

    try:
        history = await fpl_client.get_entry_history(team_id)
    except Exception:
        history = {"current": [], "chips": []}

    recent_gameweeks = [
        {
            "event": h["event"],
            "points": h.get("points"),
            "points_on_bench": h.get("points_on_bench"),
            "overall_rank": h.get("overall_rank"),
            "transfers": h.get("event_transfers"),
            "transfer_cost": h.get("event_transfers_cost"),
            "team_value": round((h.get("value") or 0) / 10.0, 1),
        }
        for h in history.get("current", [])[-6:]
    ]
    chip_suggestions = chips.suggest_chips(my_players, history.get("chips", []), _current_event(bootstrap))

    # Transfer suggestions: for the manager's weakest players, weigh both the
    # model score and each candidate's next-3-fixture run (home/away-aware
    # FDR) rather than score alone — see transfers.py.
    suggestions = transfers.suggest_transfers(my_players, all_scores)

    return {
        "team_name": entry.get("name"),
        "manager_name": f"{entry.get('player_first_name', '')} {entry.get('player_last_name', '')}".strip(),
        "overall_rank": entry.get("summary_overall_rank"),
        "gameweek_used": event,
        "squad": my_players,
        "transfer_suggestions": suggestions,
        "recent_gameweeks": recent_gameweeks,
        "chip_suggestions": chip_suggestions,
    }
