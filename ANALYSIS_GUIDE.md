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

The Tuesday morning run (am, Mountain time) writes a weekly check-in instead of the regular report. The Tuesday pm run is a normal report, saved as
`reports/<season>/week-<WW>/<YYYY-MM-DD>-weekly.md`, where WW is the **upcoming** week. Sections, in order:

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
1–3 realistic trades that fix a weakness: who to target on which team (and why that team would want it),
what to offer from your surplus. Mark these as ideas to consider; never assume the user will make them.

## Roster health
Players to hold, sell, stash or cut over the next month; IR returns and when.

## Sources
```

## 5. Filing

1. Write the report file.
2. Copy it over `reports/LATEST.md`.
3. Update `reports/README.md`: newest report first, grouped under `## <season> · Week <W>` headings, one line per report:
   `- [Wed Nov 4 · AM](2026/week-09/2026-11-04-am.md) — <one-line bottom line>`
4. Add an entry to `data/outbox.json` (a JSON list; keep only the newest 10 entries) so the monitor sends a phone alert
   within ~5 minutes:
   `{"id": "<report file name>", "title": "Week <W> <AM|PM> report", "body": "<top 2–3 moves, under 300 characters>",
   "priority": "default", "tags": "memo", "click": "https://github.com/Mhutchings24/fantasy-monitor/blob/main/reports/LATEST.md"}`
   For the weekly check-in use the title `"Week <W> check-in"` and lead the body with the waiver claims.
   Use `"priority": "high"` when there's an urgent move (a starter ruled out, a game about to lock).
5. Commit with message `Week <W> <am|pm> report` and push to `main` (pull with rebase first; the monitor commits often).
