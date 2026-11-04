# Fantasy monitor

Watches the **No Fun League** on Sleeper for `Mhutch2446` every ~5 minutes.

- **Latest report:** [data/REPORT.md](data/REPORT.md)
- **Raw analysis:** [data/latest.json](data/latest.json)
- **In-depth reports (twice daily):** [reports/](reports/README.md) · [latest](reports/LATEST.md)

Each run pulls the league from Sleeper's public API, then:

- **Waiver wire:** ranks free agents by how many extra projected starting points they add over the next 4 weeks, after dropping your least useful player.
- **Lineup check:** flags starters who are out, doubtful, on bye, or empty slots, and lists better swaps from your bench.
- **Alerts:** sends a push to the ntfy app for new injuries on your roster, lineup problems, and strong waiver targets. Each alert is sent once.

Settings live in `config.json`. Alerts need a repository secret named `NTFY_TOPIC`.
To check right now: **Actions → Fantasy monitor → Run workflow**.
