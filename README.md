# Conorrhea HQ

Fantasy management for **Conorrhea** in the **No Fun League** (Sleeper user `Mhutch2446`).

- **App:** https://mhutchings24.github.io/fantasy-monitor/ (add it to your home screen)
- **Latest analysis:** [reports/LATEST.md](reports/LATEST.md) · [all reports](reports/README.md)
- **Alerts:** the ntfy app, topic set in `config.json`

## How it works

| What | When | Does |
|---|---|---|
| **Monitor** (`monitor.py`) | Every ~5 min, around the clock | Pulls the league from Sleeper. Checks your lineup, ranks waiver adds and K/DEF streams, finds trades, runs standings and playoff odds. Alerts on injury changes, lineup problems, roster changes and strong pickups. |
| **Analysis** (Claude) | Daily, 7:36 AM and 5:36 PM MT | Researches news on every starter, injury and waiver target and writes an in-depth report. Tuesday morning's is the **weekly check-in**: recap, playoff picture, waiver plan, trade ideas. |
| **Game-day checks** (Claude) | Thu & Mon 4:51 PM, Sun 9:41 AM & 12:46 PM MT | Inactives and late news before each kickoff window, with exact swaps and lock times. |
| **Event watcher** (Claude) | Hourly | When the monitor logs something serious (a starter ruled out, a roster change), it researches it and writes a flash report. |

Every report lands in `reports/`, shows up in the app, and sends a phone alert.

## Files

- `monitor.py`: the monitor. Settings live in `config.json`.
- `ANALYSIS_GUIDE.md`: how Claude writes every report. Edit it to change the format.
- `data/latest.json`: everything the monitor knows, refreshed every check. `data/REPORT.md` is the readable version.
- `data/events.json`: the alert log. `data/outbox.json`: report alerts waiting to send.
- `index.html`, `app.js`: the app.
- To run a check right now: **Actions → Fantasy monitor → Run workflow**.
