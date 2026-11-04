# Analysis guide

Instructions for the twice-daily in-depth analysis of **Conorrhea** in the **No Fun League** (Sleeper user `Mhutch2446`).
The analyst (Claude, running as a scheduled task) follows this file exactly. Edit it to change how reports are written.

## 1. Inputs

- `data/latest.json` — written every ~5 minutes by the monitor: roster, this week's lineup (from the week's matchup),
  Sleeper projections for the next 4 weeks, injury statuses, waiver rankings, K/DEF streams, FAAB left, league settings.
- `data/latest.json` → `overview` — standings, power ranks, projected points per week for every team, this week's
  matchup (projections and win chance), simulated playoff odds, and `last_week` (your result with each player's points).
- `data/REPORT.md` — the same data as a readable table.
- The web — current news. **Search before stating anything about a player's health, role or availability.**

If `latest.json` is more than 2 hours old, say so at the top of the report; the monitor may be failing.

## 2. Method

Projections are a starting point, not the answer. For every lineup decision that is close or uncertain:

1. **Check the news, with dates.** Injury designations, practice participation (DNP / limited / full), coach quotes,
   beat-reporter notes. Sleeper's status can be stale (for example, still "Out" from a past week). Prefer the newest dated source.
2. **Estimate the chance each player plays**, as a percentage, and say what it's based on. Label these as estimates.
3. **Look at role, not just name.** Snap share, targets, carries, red-zone work, committee splits, recent trend (last 3–4 games).
4. **Look at the game.** Opponent's points allowed to the position, Vegas total and spread / likely game script,
   weather for outdoor games, quarterback changes.
5. **Think in outcomes.** Floor, median, ceiling. Expected points = chance of playing × median if he plays.
   Say when a higher-floor or higher-ceiling option is better given the matchup (as an underdog, ceiling matters more).
6. **Plan around kickoff times.** Note each player's game time (Eastern). Flag late games (Sunday/Monday night)
   where a late scratch would leave no healthy bench player still unlocked, and name the pivot player and the deadline.
7. **Be decisive.** Give a clear recommendation and a confidence level (High / Medium / Low). When the call depends on
   news still to come, say exactly what news flips it.

Waivers: use the monitor's rankings as the shortlist, then check news on each. Recommend a FAAB bid range when the league
uses FAAB. Name the drop and why. Don't recommend drops of players with a clear path back to value without saying so.

## 3. Report format

Save to `reports/<season>/week-<WW>/<YYYY-MM-DD>-<am|pm>.md` (Mountain time date; `am` before noon, else `pm`).
Use exactly these sections, in this order:

```
# Week <W> · <Weekday Mon D> <AM|PM> report
_Conorrhea · No Fun League · record <W-L> · data as of <time from latest.json, Mountain time>_

## Bottom line
- 3–5 bullets: the exact moves to make now, and what to watch for.

## Recommended lineup
| Slot | Player | Game (ET) | Proj | Plays? | Confidence | Why |

## This week's matchup
Opponent, both projected scores (adjusted for your own injury calls), win chance, the opponent's key players and their
injury news. As a clear underdog (win chance under ~35%), favor higher-ceiling starters; as a clear favorite, favor floor.

## Key decisions
### <Decision, e.g. "WR2: Evans or Washington">
Options, news (dated), chance of playing, role/matchup, floor–ceiling, recommendation, what would change it.

## Injury watch
| Player | Status | Latest news (date) | Chance to play | Next update |

## Waivers & FAAB
| Add | Drop | Bid | Why |
(or "No adds worth making" with the reason)

## Trades
The monitor's trade engine (`trades` in latest.json) proposes deals that raise your projected starting points and also
help the other team. Vet the top 2–3 with news (injuries, role changes, the partner's needs and record). For each:
endorse, adjust (say what to change), or reject (say why). Skip the section if nothing is worth sending.

## Kicker & defense
One or two lines on streaming.

## Looking ahead
Byes and holes over the next 2–3 weeks, players to stash or sell.

## Changes since last report
What moved and why (skip on the first report of a week).

## Sources
- [Title](url) — one line each
```

Keep it readable on a phone: short sentences, tables where they help, no walls of text.

## 4. Weekly check-in (Tuesdays)

The Tuesday morning run (am, Mountain time) writes a weekly check-in instead of the regular report, saved as
`reports/<season>/week-<WW>/<YYYY-MM-DD>-weekly.md`, where WW is the **upcoming** week. The Tuesday pm run is a
normal report. Weekly check-in sections, in order:

```
# Week <W> check-in · <Tue Mon D>
_Conorrhea · No Fun League · record <W-L> · <standing>th · playoff odds <x>%_

## Last week
Result and score vs opponent. Who carried you, who let you down, and bench points left on the table
(points a benched player scored above the starter he could have replaced). One line on what to learn from it.

## Where you stand
Standings snapshot (your rank, games back of the last playoff spot), playoff odds and how they moved,
remaining schedule difficulty (opponents' projected strength), and what record you likely need.

## This week
Opponent, win chance, the early lineup plan and the decisions to revisit later in the week.

## Waiver plan
Exact claims for this week's waiver run, in priority order, with FAAB bids and drops. Account for upcoming byes.
Keep a FAAB reserve and say how much.

## Trade ideas
1–3 realistic trades that fix a weakness. Start from the engine's ideas (`trades` in latest.json), vet them with news,
and add your own if you see a better one. For each: who to target on which team, why that team would want it,
what to offer, and a one-line message the user could send. Mark these as ideas; never assume the user will make them.

## Roster health
Players to hold, sell, stash or cut over the next month; IR returns and when.

## Sources
```

## 5. Game-day check

Short, urgent runs before each kickoff window (Thursday before the night game, Sunday morning before the 1 PM ET games,
Sunday before the late-afternoon games, Monday before the night game). Saved as
`reports/<season>/week-<WW>/<YYYY-MM-DD>-gameday-<HHMM>.md` (Mountain time). Focus only on players in that window and
players still unlocked after it:

```
# Week <W> game-day check · <Day> <time> MT
## Make these moves now
Exact swaps, or "No changes needed". Include the kickoff/lock time for each.
## Inactives and late news
Each relevant player: active / inactive / game-time decision, with the source and time.
## Still to decide
Later-window decisions and when the news will come.
## Sources
```

Search for official inactives and the latest beat-reporter news. When a starter is out or doubtful and his game
hasn't started, the alert is `"priority": "high"` and the title starts with "Lineup change:".

## 6. Flash report (event watcher)

The event watcher runs hourly. It reads `data/events.json` (alerts the monitor logged) and `data/events_handled.json`
(a JSON object `{"last_id": "<id>"}`; create it if missing). Each event has `type`
(injury, roster, lineup, waiver, stream, other) and `severity` (high marks a starter who's now out or doubtful, or a
lineup problem). If no event after `last_id` is `high` severity or of type `roster` or `injury`, set `last_id` to the
newest event, commit, push, and stop: no report, no alert.

Otherwise research the new events and write `reports/<season>/week-<WW>/<YYYY-MM-DD>-flash-<HHMM>.md`:

```
# Flash: <what happened, in a few words>
## What changed
## What to do
Exact moves and deadlines.
## Sources
```

Then file it as below and set `last_id` to the newest event handled.

## 7. Filing

1. Write the report file.
2. Copy it over `reports/LATEST.md`.
3. Update `reports/README.md`: newest report first, grouped under `## <season> · Week <W>` headings, one line per report:
   `- [Wed Nov 4 · AM](2026/week-09/2026-11-04-am.md) — <one-line bottom line>`
4. Add an entry to `data/outbox.json` (a JSON list; keep only the newest 10 entries) so the monitor sends a phone alert
   within ~5 minutes:
   `{"id": "<report file name>", "title": "Week <W> <AM|PM> report", "body": "<top 2–3 moves, under 300 characters>",
   "priority": "default", "tags": "memo", "click": "https://github.com/Mhutchings24/fantasy-monitor/blob/main/reports/LATEST.md"}`
   For the weekly check-in use the title `"Week <W> check-in"` and lead the body with the waiver claims.
   For game-day checks use `"Game day: <window>"`; for flash reports `"Flash: <what happened>"`.
   Also add the report to the index (step 3) — every report type goes in the same week group.
   Use `"priority": "high"` when there's an urgent move (a starter ruled out, a game about to lock).
5. Commit with message `Week <W> <type> report` and push to `main` (pull with rebase first; the monitor commits often).

Never change `monitor.py`, the app files (`index.html`, `app.js`) or the workflow in a scheduled run. If something in
them looks broken, say so at the top of the report.
