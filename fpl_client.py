"""Thin client for the official (unofficial-but-public) Fantasy Premier League
API, plus free community "news & opinions" feeds — Reddit's public JSON
endpoints and Fantasy Football Scout's RSS feed (no API key needed for
either)."""
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any, Optional

import httpx

BASE_URL = "https://fantasy.premierleague.com/api"
REDDIT_BASE_URL = "https://www.reddit.com"
FFSCOUT_RSS_URL = "https://www.fantasyfootballscout.co.uk/feed/"
CACHE_TTL_SECONDS = 15 * 60
NEWS_CACHE_TTL_SECONDS = 10 * 60

_cache: dict[str, tuple[float, Any]] = {}
_text_cache: dict[str, tuple[float, str]] = {}


async def _get_json(url: str, *, ttl: float = CACHE_TTL_SECONDS, headers: Optional[dict] = None) -> Any:
    now = time.time()
    cached = _cache.get(url)
    if cached and now - cached[0] < ttl:
        return cached[1]

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=headers or {"User-Agent": "fpl-assistant/0.1"})
        resp.raise_for_status()
        data = resp.json()

    _cache[url] = (now, data)
    return data


async def _get_text(url: str, *, ttl: float = CACHE_TTL_SECONDS, headers: Optional[dict] = None) -> str:
    now = time.time()
    cached = _text_cache.get(url)
    if cached and now - cached[0] < ttl:
        return cached[1]

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=headers or {"User-Agent": "fpl-assistant/0.1"})
        resp.raise_for_status()
        text = resp.text

    _text_cache[url] = (now, text)
    return text


async def get_bootstrap() -> dict:
    """Players, teams, gameweeks, scoring rules — the core static dataset."""
    return await _get_json(f"{BASE_URL}/bootstrap-static/")


async def get_fixtures(event: Optional[int] = None) -> list:
    url = f"{BASE_URL}/fixtures/"
    if event is not None:
        url += f"?event={event}"
    return await _get_json(url)


async def get_player_summary(player_id: int) -> dict:
    """Per-gameweek history + upcoming fixtures for a single player."""
    return await _get_json(f"{BASE_URL}/element-summary/{player_id}/")


async def get_entry(team_id: int) -> dict:
    """A manager's team info (name, overall rank, etc.)."""
    return await _get_json(f"{BASE_URL}/entry/{team_id}/")


async def get_entry_picks(team_id: int, event: int) -> dict:
    """A manager's 15-man squad for a given gameweek."""
    return await _get_json(f"{BASE_URL}/entry/{team_id}/event/{event}/picks/")


async def get_entry_history(team_id: int) -> dict:
    """A manager's season history: points per gameweek plus chips used —
    used for the "previous gameweek performance" and chip-advice views."""
    return await _get_json(f"{BASE_URL}/entry/{team_id}/history/")


async def get_community_news(limit: int = 20) -> list[dict]:
    """Hot discussion threads from r/FantasyPL — free, no API key, used as a
    lightweight "news & opinions" feed. Reddit requires a descriptive,
    non-default User-Agent or it 429s; requests are cached to stay well
    under that limit. Raises on failure — callers should degrade gracefully
    rather than let a flaky third party take the whole app down.
    """
    data = await _get_json(
        f"{REDDIT_BASE_URL}/r/FantasyPL/hot.json?limit={limit}",
        ttl=NEWS_CACHE_TTL_SECONDS,
        headers={"User-Agent": "fpl-assistant/0.1 (personal FPL dashboard)"},
    )
    posts = []
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        if post.get("stickied"):
            continue
        posts.append({
            "title": post.get("title", ""),
            "url": f"https://reddit.com{post.get('permalink', '')}" if post.get("permalink") else post.get("url"),
            "author": post.get("author", ""),
            "score": post.get("score", 0),
            "num_comments": post.get("num_comments", 0),
            "created_utc": post.get("created_utc", 0),
            "flair": post.get("link_flair_text"),
            "source": "r/FantasyPL",
        })
    return posts


async def get_ffscout_news(limit: int = 10) -> list[dict]:
    """Fantasy Football Scout's public RSS feed — free, no API key. An
    editorial-opinion complement to the r/FantasyPL community feed (team
    news, captaincy picks, transfer analysis). Raises on failure — callers
    should degrade gracefully.
    """
    text = await _get_text(
        FFSCOUT_RSS_URL,
        ttl=NEWS_CACHE_TTL_SECONDS,
        headers={"User-Agent": "fpl-assistant/0.1 (personal FPL dashboard)"},
    )
    root = ET.fromstring(text)
    posts = []
    for item in root.findall("./channel/item")[:limit]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = item.findtext("pubDate")
        created_utc = 0
        if pub_date:
            try:
                created_utc = parsedate_to_datetime(pub_date).timestamp()
            except (TypeError, ValueError):
                created_utc = 0
        creator = item.findtext("{http://purl.org/dc/elements/1.1/}creator") or "Fantasy Football Scout"
        posts.append({
            "title": title,
            "url": link,
            "author": creator,
            "score": None,
            "num_comments": None,
            "created_utc": created_utc,
            "flair": None,
            "source": "Fantasy Football Scout",
        })
    return posts
