# FPL Assistant

A free, personal recommendation dashboard for Fantasy Premier League. Pulls
live data from the official (public, no key needed) FPL API and scores
players on form, upcoming fixture difficulty, underlying stats (xG/xA), and
value for money.

## Run it

All files live in one flat folder (no subfolders) so it's easy to upload
manually to GitHub — `main.py`, `model.py`, `optimizer.py`, `fpl_client.py`,
`chips.py`, `transfers.py` are the backend; `index.html` is the whole
frontend.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Then open `index.html` in a browser (or serve it with
`python3 -m http.server 5500` from this same folder).

## What it does

- **Rankings** — every player scored and ranked. Filter by position, price,
  or a live search box; sort by score, form, value, ownership, ICT, or
  price. Extra stats per player: ownership %, ICT index, price trend
  (rising/falling this gameweek), and a 💎 **Differential** badge for
  lightly-owned players scoring in the top quartile of their position.
- **Suggested Squad** — builds a valid 15-man squad (2/5/5/3, configurable
  budget, max 3 per club), rendered as a real pitch formation with a
  starting XI + captain/vice-captain, plus the bench.
- **My Team** — paste your FPL team ID to see your squad as a pitch
  formation + bench, your last 6 gameweeks' points and rank trend, and
  rule-of-thumb **chip advice** (Wildcard, Free Hit, Bench Boost, Triple
  Captain) based on your squad's availability and upcoming fixtures.
  **Transfer suggestions** weigh each candidate's next 3 fixtures
  explicitly — home or away, and FPL's own per-fixture difficulty rating,
  with home games treated as slightly easier — alongside the model score,
  so a bad run of away fixtures against tough opponents can surface a swap
  even when the raw scores are close (see `transfers.py`).
- **News & Opinion** — live posts pooled from r/FantasyPL and Fantasy
  Football Scout's RSS feed (both free, no API key), for injury chat,
  captaincy polls, and editorial opinion alongside the model's own numbers.
- **Day/night theme toggle** — a floodlit-stadium dark theme and a sunny
  light theme, both built from a shared aerial mown-pitch backdrop (drawn as
  inline SVG line art, not a photo) with goalposts framing the sides of the
  screen on wide displays; your choice is remembered locally.
- **Forum** — a tab that jumps straight to YouTube search results for the
  upcoming gameweek's FPL tips, in a new tab.

## Model (v2)

Weighted blend, each factor normalized 0-1 across all players, plus a flat
set-piece-duty bonus:

| Factor | Weight | Source |
|---|---|---|
| Recent form | 28% | FPL `form` field |
| Fixture ease (next 3 GWs) | 20% | FPL fixture difficulty ratings |
| Availability (injury/suspension-aware) | 18% | FPL `status` code + chance of playing |
| Value (points per £m) | 12% | total points / price |
| Underlying stats (xG/xA per 90) | 12% | FPL expected stats |
| Minutes security | 10% | starts / finished gameweeks |
| Set-piece duty | flat bonus | penalty/corner/free-kick taker order |

Weights live in `model.py` — tune them as you see how recommendations
perform week to week.

## Notes

- Not affiliated with the Premier League or FPL — uses their public API.
- Squad optimizer is a greedy heuristic, not a full ILP solver — good enough
  for a v1, can be swapped for a proper solver (e.g. PuLP) later.
