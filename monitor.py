#!/usr/bin/env python3
"""Fantasy monitor for a Sleeper league.

Each run pulls the league from Sleeper, ranks waiver pickups by how much they
improve your best possible lineup over the next few weeks, checks this week's
lineup for problems, saves the results to data/, and sends phone alerts for
anything new (via ntfy when the NTFY_TOPIC secret is set).

Standard library only, so it runs anywhere with Python 3.9+.
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://api.sleeper.app/v1"
PROJ = "https://api.sleeper.app/projections/nfl"
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(ROOT, ".cache")
UA = "fantasy-monitor/1.0 (+github actions)"

NFL_TEAMS = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET",
    "GB", "HOU", "IND", "JAX", "KC", "LAC", "LAR", "LV", "MIA", "MIN", "NE",
    "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
}

SLOT_ELIG = {
    "QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"}, "K": {"K"},
    "DEF": {"DEF"}, "DL": {"DL"}, "LB": {"LB"}, "DB": {"DB"},
    "FLEX": {"RB", "WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "REC_FLEX": {"WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
    "IDP_FLEX": {"DL", "LB", "DB"},
}
NON_STARTING = {"BN", "IR", "TAXI"}

# How much of a player's projection to trust this week given injury status.
STATUS_WEIGHT_THIS_WEEK = {
    "Out": 0.0, "IR": 0.0, "PUP": 0.0, "Sus": 0.0, "Suspended": 0.0, "NA": 0.0,
    "COV": 0.0, "DNR": 0.0, "Doubtful": 0.25, "Questionable": 0.85,
}
# Statuses that wipe out future weeks too (long-term absences).
LONG_TERM_OUT = {"IR", "PUP", "Sus", "Suspended", "NA", "DNR"}
BAD_START_STATUS = {"Out", "IR", "PUP", "Sus", "Suspended", "NA", "COV", "DNR", "Doubtful"}


# ---------------------------------------------------------------- utilities

def log(*a):
    print(*a, file=sys.stderr, flush=True)


def get(url, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last = e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ConnectionError) as e:
            last = e
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"GET failed for {url}: {last}")


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=True, ensure_ascii=False)
    os.replace(tmp, path)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- fetching

def players_db():
    """Full player list. Sleeper asks that this be pulled at most ~once a day."""
    path = os.path.join(CACHE, "players.json")
    today = datetime.now(timezone.utc).date()
    if os.path.exists(path) and datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).date() == today:
        db = load(path)
        if db:
            return db
    log("Refreshing player database from Sleeper")
    raw = get(f"{BASE}/players/nfl") or {}
    keep = ("full_name", "first_name", "last_name", "position", "team",
            "injury_status", "status", "fantasy_positions", "age", "years_exp")
    slim = {pid: {k: p.get(k) for k in keep} for pid, p in raw.items()}
    save(path, slim)
    return slim


def fetch_projections(season, week, positions):
    q = [("season_type", "regular")] + [("position[]", p) for p in positions]
    url = f"{PROJ}/{season}/{week}?" + urllib.parse.urlencode(q)
    try:
        rows = get(url) or []
    except RuntimeError as e:
        log(f"Projections unavailable for week {week}: {e}")
        return None
    return rows if isinstance(rows, list) else None


def find_league(cfg, season):
    user = get(f"{BASE}/user/{cfg['sleeper_username']}")
    if not user:
        raise SystemExit(f"Sleeper user '{cfg['sleeper_username']}' not found")
    leagues = get(f"{BASE}/user/{user['user_id']}/leagues/nfl/{season}") or []
    want = cfg["league_name"].strip().lower()
    match = [l for l in leagues if (l.get("name") or "").strip().lower() == want]
    if not match:
        match = [l for l in leagues if want in (l.get("name") or "").lower()]
    if not match:
        names = ", ".join(l.get("name", "?") for l in leagues) or "none"
        raise SystemExit(f"League '{cfg['league_name']}' not found. Leagues in {season}: {names}")
    return user, match[0]


# ---------------------------------------------------------------- scoring

def make_scorer(scoring):
    rec = scoring.get("rec", 0) or 0
    fallback_key = "pts_ppr" if rec >= 1 else "pts_half_ppr" if rec >= 0.5 else "pts_std"

    def score(stats, position):
        if not stats:
            return 0.0
        fallback = stats.get(fallback_key)
        if position in ("DEF", "K") and isinstance(fallback, (int, float)):
            return round(float(fallback), 2)
        pts, used = 0.0, False
        for k, v in stats.items():
            w = scoring.get(k)
            if isinstance(w, (int, float)) and isinstance(v, (int, float)):
                pts += v * w
                used = True
        if used and pts != 0:
            return round(pts, 2)
        return round(float(fallback or 0), 2)

    return score


# ---------------------------------------------------------------- lineups

def eligible(player, slot):
    allowed = SLOT_ELIG.get(slot)
    if not allowed:
        return False
    pos = set(player.get("fantasy_positions") or []) | {player.get("position")}
    return bool(allowed & pos)


def starting_slots(roster_positions):
    return [s for s in roster_positions if s not in NON_STARTING]


def optimal_lineup(pids, slots, players, pts):
    """Greedy fill, strictest slots first. Returns (total, [(slot, pid or None)])."""
    order = sorted(range(len(slots)), key=lambda i: len(SLOT_ELIG.get(slots[i], ())))
    pool = sorted(pids, key=lambda p: pts.get(p, 0.0), reverse=True)
    used, assign, total = set(), [None] * len(slots), 0.0
    for i in order:
        for p in pool:
            if p in used or p not in players:
                continue
            if eligible(players[p], slots[i]):
                used.add(p)
                assign[i] = p
                total += pts.get(p, 0.0)
                break
    return round(total, 2), list(zip(slots, assign))


# ---------------------------------------------------------------- alerts

def ntfy_topic():
    t = os.environ.get("NTFY_TOPIC", "").strip()
    if not t:
        t = (load(os.path.join(ROOT, "config.json"), {}) or {}).get("ntfy_topic", "").strip()
    return t


def notify(title, body, priority="default", tags="", click=None):
    topic = ntfy_topic()
    if not topic:
        log(f"[alert, no NTFY_TOPIC set] {title}: {body}")
        return False
    params = {"title": title, "priority": priority}
    if tags:
        params["tags"] = tags
    if click:
        params["click"] = click
    url = f"https://ntfy.sh/{urllib.parse.quote(topic)}?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST",
                                     headers={"User-Agent": UA})
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except Exception as e:  # never let an alert failure kill the run
        log(f"ntfy send failed: {e}")
        return False


# ---------------------------------------------------------------- main analysis

def name_of(pid, players):
    p = players.get(pid) or {}
    if p.get("position") == "DEF":
        return f"{pid} D/ST"
    return p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or pid


def tag(pid, players):
    p = players.get(pid) or {}
    return f"{name_of(pid, players)} ({p.get('position', '?')}, {p.get('team') or 'FA'})"


def run():
    cfg = load(os.path.join(ROOT, "config.json"))
    th = cfg.get("alert_thresholds", {})
    state = get(f"{BASE}/state/nfl")
    season = str(state.get("league_season") or state.get("season"))
    week = int(state.get("week") or state.get("display_week") or 1)
    season_type = state.get("season_type", "regular")

    user, league = find_league(cfg, season)
    lid = league["league_id"]
    scoring = league.get("scoring_settings") or {}
    settings = league.get("settings") or {}
    roster_positions = league.get("roster_positions") or []
    slots = starting_slots(roster_positions)
    rosters = get(f"{BASE}/league/{lid}/rosters") or []
    users = {u["user_id"]: u for u in (get(f"{BASE}/league/{lid}/users") or [])}
    mine = next((r for r in rosters if r.get("owner_id") == user["user_id"]
                 or user["user_id"] in (r.get("co_owners") or [])), None)
    if not mine:
        raise SystemExit("Couldn't find your roster in the league")

    # The week's actual lineup lives in that week's matchup; roster.starters can lag behind.
    current_starters = list(mine.get("starters") or [])
    lineup_source = "roster"
    for m in (get(f"{BASE}/league/{lid}/matchups/{week}") or []):
        if m.get("roster_id") == mine.get("roster_id") and m.get("starters"):
            current_starters, lineup_source = list(m["starters"]), f"matchup week {week}"
            break
    log(f"Lineup source: {lineup_source}")

    players = players_db()
    score = make_scorer(scoring)
    league_positions = sorted({p for s in slots for p in SLOT_ELIG.get(s, ())})

    last_week = 17 if int(season) >= 2021 else 16
    if season_type != "regular":
        log(f"Season type is {season_type}; projections may be empty")
    weeks = [w for w in range(week, min(week + int(cfg.get("projection_weeks", 4)), last_week + 1))] or [week]

    # projections: pts[week][pid]
    pts_by_week, teams_playing, shape_sample = {}, {}, None
    for w in weeks:
        rows = fetch_projections(season, w, league_positions)
        if rows is None:
            continue
        wk, playing = {}, set()
        for r in rows:
            pid = r.get("player_id")
            if not pid:
                continue
            pl = r.get("player") or {}
            pos = (players.get(pid) or {}).get("position") or pl.get("position")
            wk[pid] = score(r.get("stats") or {}, pos)
            if r.get("opponent") and r.get("team"):
                playing.add(r["team"])
            # projection rows carry fresher injury news than the daily player file
            if pid in players and "injury_status" in pl and w == week:
                players[pid]["injury_status"] = pl.get("injury_status")
            if pid in players and pl.get("team") and w == week:
                players[pid]["team"] = pl.get("team")
            if shape_sample is None:
                shape_sample = {"row_keys": sorted(r.keys()), "player_keys": sorted(pl.keys()),
                                "stat_keys_sample": sorted((r.get("stats") or {}).keys())[:40]}
        pts_by_week[w] = wk
        teams_playing[w] = playing
    have_proj = bool(pts_by_week)

    def bye(pid, w):
        team = (players.get(pid) or {}).get("team")
        playing = teams_playing.get(w) or set()
        return bool(team and len(playing) >= 20 and team not in playing)

    def adj_pts(w):
        """Projected points for week w after injury and bye adjustments."""
        out = {}
        for pid, v in pts_by_week.get(w, {}).items():
            st = (players.get(pid) or {}).get("injury_status")
            if bye(pid, w):
                v = 0.0
            elif w == week and st in STATUS_WEIGHT_THIS_WEEK:
                v *= STATUS_WEIGHT_THIS_WEEK[st]
            elif st in LONG_TERM_OUT:
                v = 0.0
            out[pid] = round(v, 2)
        return out

    adj = {w: adj_pts(w) for w in pts_by_week}

    roster_all = [p for p in (mine.get("players") or []) if p]
    reserve = set(mine.get("reserve") or []) | set(mine.get("taxi") or [])
    active = [p for p in roster_all if p not in reserve]
    rostered_anywhere = {p for r in rosters for p in (r.get("players") or []) if p}

    def window_total(pids):
        return round(sum(optimal_lineup(pids, slots, players, adj[w])[0] for w in adj), 2)

    # ------------------------------------------------ lineup check (this week)
    lineup = {"week": week, "problems": [], "swaps": [], "source": lineup_source}
    if week in adj:
        a = adj[week]
        current = current_starters
        cur_total = 0.0
        for i, slot in enumerate(slots):
            pid = current[i] if i < len(current) else None
            if not pid or pid == "0":
                lineup["problems"].append({"type": "empty", "slot": slot,
                                           "text": f"{slot} slot is empty"})
                continue
            cur_total += a.get(pid, 0.0)
            st = (players.get(pid) or {}).get("injury_status")
            if bye(pid, week):
                lineup["problems"].append({"type": "bye", "pid": pid,
                                           "text": f"{tag(pid, players)} is on bye"})
            elif st in BAD_START_STATUS:
                lineup["problems"].append({"type": "injury", "pid": pid, "status": st,
                                           "text": f"{tag(pid, players)} is {st}"})
        best_total, best = optimal_lineup(active, slots, players, a)
        lineup["current_projected"] = round(cur_total, 2)
        lineup["best_projected"] = best_total
        lineup["best_lineup"] = [{"slot": s, "pid": p, "name": name_of(p, players) if p else None,
                                  "proj": a.get(p, 0.0) if p else 0.0} for s, p in best]
        best_set = {p for _, p in best if p}
        cur_set = {p for p in current if p and p != "0"}
        outs = sorted(cur_set - best_set, key=lambda p: a.get(p, 0.0))
        ins = sorted(best_set - cur_set, key=lambda p: -a.get(p, 0.0))
        for o, n in zip(outs, ins):
            lineup["swaps"].append({"bench": o, "start": n,
                                    "text": f"Start {tag(n, players)} ({a.get(n, 0):.1f}) over "
                                            f"{tag(o, players)} ({a.get(o, 0):.1f})"})
        for n in ins[len(outs):]:
            lineup["swaps"].append({"bench": None, "start": n,
                                    "text": f"Start {tag(n, players)} ({a.get(n, 0):.1f})"})
        lineup["gain"] = round(best_total - cur_total, 2)

    # ------------------------------------------------ waiver wire
    waivers, streams, free_agents = [], [], []
    trending = get(f"{BASE}/players/nfl/trending/add?lookback_hours=24&limit=50") or []
    trend_count = {t["player_id"]: t.get("count", 0) for t in trending if t.get("player_id")}
    if have_proj:
        base = window_total(active)
        fa = [p for p in set().union(*[set(adj[w]) for w in adj])
              if p not in rostered_anywhere and p in players
              and (players[p].get("position") in league_positions
                   or set(players[p].get("fantasy_positions") or []) & set(league_positions))]
        fa.sort(key=lambda p: sum(adj[w].get(p, 0.0) for w in adj), reverse=True)
        pool = fa[: int(cfg.get("waiver_candidates", 80))]
        pool += [p for p in trend_count if p in fa and p not in pool]
        base_week = optimal_lineup(active, slots, players, adj[week])[0] if week in adj else 0.0
        for f in pool:
            fpos = players[f].get("position")
            streamer = fpos in ("K", "DEF")
            # K/DEF are weekly streams: judge on this week only, swapping for the same position
            drops = [p for p in active if players.get(p, {}).get("position") == fpos] if streamer else list(active)
            if streamer and (not drops or week not in adj):
                continue
            best_gain, best_drop = None, None
            for d in drops:
                trial = [p for p in active if p != d] + [f]
                if streamer:
                    g = optimal_lineup(trial, slots, players, adj[week])[0] - base_week
                else:
                    g = window_total(trial) - base
                if best_gain is None or g > best_gain:
                    best_gain, best_drop = g, d
            if best_gain is not None and best_gain > 0.05:
                this_week = 0.0
                if week in adj:
                    trial = [p for p in active if p != best_drop] + [f]
                    this_week = (optimal_lineup(trial, slots, players, adj[week])[0]
                                 - optimal_lineup(active, slots, players, adj[week])[0])
                waivers.append({
                    "pid": f, "name": name_of(f, players),
                    "pos": players[f].get("position"), "team": players[f].get("team"),
                    "status": players[f].get("injury_status"),
                    "drop": best_drop, "drop_name": name_of(best_drop, players),
                    "gain_window": round(best_gain, 2), "gain_this_week": round(this_week, 2),
                    "proj_by_week": {str(w): adj[w].get(f, 0.0) for w in adj},
                    "trending_adds_24h": trend_count.get(f, 0),
                    "stream": streamer,
                })
        free_agents = [{"name": name_of(p, players), "pos": players[p].get("position"),
                        "team": players[p].get("team"), "status": players[p].get("injury_status"),
                        "proj_4wk": round(sum(adj[w].get(p, 0.0) for w in adj), 1),
                        "proj_this_week": adj.get(week, {}).get(p, 0.0),
                        "adds_24h": trend_count.get(p, 0)}
                       for p in (fa[:80] + [p for p in trend_count if p in fa and p not in fa[:80]])]
        waivers.sort(key=lambda x: (x["gain_window"], x["trending_adds_24h"]), reverse=True)
        streams = [w for w in waivers if w["stream"]]
        streams = [next((w for w in streams if w["pos"] == pos), None) for pos in ("DEF", "K")]
        streams = [w for w in streams if w]
        waivers = [w for w in waivers if not w["stream"]][:12]

    faab = None
    if settings.get("waiver_type") == 2:
        total = settings.get("waiver_budget", 100)
        used = (mine.get("settings") or {}).get("waiver_budget_used", 0)
        faab = {"budget": total, "used": used, "remaining": total - used}

    my_roster = []
    for p in roster_all:
        pl = players.get(p) or {}
        my_roster.append({
            "pid": p, "name": name_of(p, players), "pos": pl.get("position"),
            "team": pl.get("team"), "status": pl.get("injury_status"),
            "slot": "reserve" if p in reserve else ("starter" if p in current_starters else "bench"),
            "proj_by_week": {str(w): adj[w].get(p, 0.0) for w in adj},
            "bye_this_week": bye(p, week),
        })
    my_roster.sort(key=lambda r: (r["slot"] != "starter", r["pos"] or "", -(r["proj_by_week"].get(str(week)) or 0)))

    owner = users.get(mine.get("owner_id"), {})
    result = {
        "league": {"id": lid, "name": league.get("name"), "season": season,
                   "teams": len(rosters), "scoring_rec": scoring.get("rec", 0),
                   "roster_positions": roster_positions,
                   "waiver_type": {0: "rolling", 1: "reverse standings", 2: "FAAB"}.get(settings.get("waiver_type"), settings.get("waiver_type"))},
        "team": {"name": (owner.get("metadata") or {}).get("team_name") or owner.get("display_name"),
                 "roster_id": mine.get("roster_id"),
                 "record": "{}-{}{}".format((mine.get("settings") or {}).get("wins", 0),
                                            (mine.get("settings") or {}).get("losses", 0),
                                            ("-%d" % (mine.get("settings") or {}).get("ties", 0)) if (mine.get("settings") or {}).get("ties") else "")},
        "week": week, "weeks_projected": list(adj.keys()), "projections_ok": have_proj,
        "roster": my_roster, "lineup": lineup, "waivers": waivers, "streams": streams, "free_agents": free_agents, "faab": faab,
    }
    return result, shape_sample, players


def alerts_for(result, players, alert_state, cfg):
    th = cfg.get("alert_thresholds", {})
    sent = alert_state.setdefault("sent", {})
    prev_status = alert_state.setdefault("status", {})
    week = result["week"]
    link = f"https://sleeper.com/leagues/{result['league']['id']}"
    out = []

    # injury status changes on my players
    for r in result["roster"]:
        old, new = prev_status.get(r["pid"], "__unset__"), r["status"]
        if old != "__unset__" and old != new:
            if new:
                out.append(("high" if new in BAD_START_STATUS else "default",
                            f"{r['name']} now {new}",
                            f"{r['name']} ({r['pos']}, {r['team']}) changed from {old or 'healthy'} to {new}.",
                            "warning"))
            else:
                out.append(("default", f"{r['name']} cleared",
                            f"{r['name']} no longer has an injury designation.", "white_check_mark"))
        prev_status[r["pid"]] = new

    lu = result["lineup"]
    for pr in lu.get("problems", []):
        key = f"lineup:{week}:{pr['type']}:{pr.get('pid', pr.get('slot'))}:{pr.get('status', '')}"
        if key not in sent:
            sent[key] = now_iso()
            fix = "; ".join(s["text"] for s in lu.get("swaps", [])[:2]) or "No healthy replacement on your bench — check waivers."
            out.append(("high", "Lineup problem", f"{pr['text']}. Suggested: {fix}", "rotating_light"))
    if lu.get("swaps") and lu.get("gain", 0) >= th.get("lineup_gain_points", 2.0):
        sig = hashlib.sha1(json.dumps(sorted(s["start"] for s in lu["swaps"])).encode()).hexdigest()[:10]
        key = f"swap:{week}:{sig}"
        if key not in sent:
            sent[key] = now_iso()
            out.append(("default", f"Lineup: +{lu['gain']:.1f} projected pts available",
                        "\n".join(s["text"] for s in lu["swaps"]), "arrows_counterclockwise"))

    for w in result["waivers"][:3]:
        if w["gain_window"] < th.get("waiver_gain_points", 6.0):
            continue
        key = f"waiver:{week}:{w['pid']}"
        if key in sent:
            continue
        sent[key] = now_iso()
        weeks = result["weeks_projected"]
        body = (f"Add {w['name']} ({w['pos']}, {w['team']}), drop {w['drop_name']}. "
                f"+{w['gain_window']:.1f} projected starting pts over weeks {weeks[0]}-{weeks[-1]}"
                f" (+{w['gain_this_week']:.1f} this week).")
        if w["trending_adds_24h"]:
            body += f" Added in {w['trending_adds_24h']:,} leagues in the last 24h."
        out.append(("default", f"Waiver target: {w['name']}", body, "chart_with_upwards_trend"))

    for w in result.get("streams", []):
        if w["gain_this_week"] < th.get("stream_gain_points", 3.0):
            continue
        key = f"stream:{week}:{w['pid']}"
        if key in sent:
            continue
        sent[key] = now_iso()
        out.append(("default", f"{w['pos']} stream: {w['name']}",
                    f"Add {w['name']}, drop {w['drop_name']}: +{w['gain_this_week']:.1f} projected pts this week.",
                    "shield"))

    # keep the dedupe log from growing forever
    cutoff = week - 2
    alert_state["sent"] = {k: v for k, v in sent.items()
                           if not (k.split(":")[1].isdigit() and int(k.split(":")[1]) < cutoff)}

    first_run = not alert_state.get("initialized")
    alert_state["initialized"] = True
    if not alert_state.get("welcome_sent") and ntfy_topic():
        # one summary instead of a flood of everything that's already true
        lu = result["lineup"]
        lines = [f"Watching {result['league']['name']}, week {week}."]
        if lu.get("problems") or lu.get("swaps"):
            lines.append("Lineup: " + "; ".join([p["text"] for p in lu.get("problems", [])]
                                                 + [s["text"] for s in lu.get("swaps", [])][:3]))
        else:
            lines.append("Lineup looks optimal.")
        if result["waivers"]:
            w = result["waivers"][0]
            lines.append(f"Top waiver add: {w['name']} for {w['drop_name']} (+{w['gain_window']:.1f}).")
        if notify("Fantasy monitor is live", "\n".join(lines), "default", "football", link):
            alert_state["welcome_sent"] = True
        return out, True
    if first_run:
        return out, first_run
    for prio, title, body, tags in out[:6]:
        notify(title, body, prio, tags, link)
    if len(out) > 6 and not first_run:
        notify("More updates", f"{len(out) - 6} more alerts — see data/REPORT.md in the repo.", "low", "", link)
    return out, first_run


def report_md(r):
    L = []
    L.append(f"# {r['team']['name']} — {r['league']['name']}")
    L.append(f"Week {r['week']} · record {r['team']['record']} · updated {r['generated_at']} UTC\n")
    if not r["projections_ok"]:
        L.append("> Projections didn't load this run, so rankings are missing.\n")
    lu = r["lineup"]
    L.append("## Lineup check")
    if "best_projected" in lu:
        L.append(f"Current lineup projects **{lu['current_projected']:.1f}**, best possible **{lu['best_projected']:.1f}**.\n")
    for p in lu.get("problems", []):
        L.append(f"- ⚠️ {p['text']}")
    for s in lu.get("swaps", []):
        L.append(f"- 🔁 {s['text']}")
    if not lu.get("problems") and not lu.get("swaps"):
        L.append("- ✅ Your lineup is already the best one available.")
    L.append("")
    L.append("## Waiver targets")
    if r.get("faab"):
        L.append(f"FAAB left: **${r['faab']['remaining']}** (league starting budget ${r['faab']['budget']})\n")
    weeks = r["weeks_projected"]
    if r["waivers"]:
        L.append(f"Gain = extra projected starting points over weeks {weeks[0]}–{weeks[-1]} after the swap.\n")
        L.append("| Add | Pos | Team | Drop | Gain | This wk | Adds 24h |")
        L.append("|---|---|---|---|---|---|---|")
        for w in r["waivers"]:
            st = f" ({w['status']})" if w["status"] else ""
            L.append(f"| {w['name']}{st} | {w['pos']} | {w['team'] or 'FA'} | {w['drop_name']} | "
                     f"+{w['gain_window']:.1f} | +{w['gain_this_week']:.1f} | {w['trending_adds_24h']:,} |")
    else:
        L.append("No free agent improves your projected lineup right now.")
    for w in r.get("streams", []):
        L.append(f"\n**{w['pos']} stream this week:** add {w['name']} for {w['drop_name']} (+{w['gain_this_week']:.1f} pts this week)")
    L.append("")
    L.append("## Roster")
    L.append("| Player | Pos | Team | Spot | Status | " + " | ".join(f"Wk {w}" for w in weeks) + " |")
    L.append("|---|---|---|---|---|" + "---|" * len(weeks))
    for p in r["roster"]:
        st = p["status"] or ("BYE" if p["bye_this_week"] else "")
        L.append(f"| {p['name']} | {p['pos']} | {p['team'] or 'FA'} | {p['slot']} | {st} | "
                 + " | ".join(f"{p['proj_by_week'].get(str(w), 0):.1f}" for w in weeks) + " |")
    return "\n".join(L) + "\n"


def send_outbox(alert_state):
    """Deliver messages queued by the scheduled analysis (data/outbox.json), once each."""
    box = load(os.path.join(DATA, "outbox.json"), []) or []
    done = alert_state.setdefault("outbox_sent", [])
    for m in box:
        mid = m.get("id")
        if not mid or mid in done:
            continue
        if notify(m.get("title", "Fantasy update"), m.get("body", ""), m.get("priority", "default"),
                  m.get("tags", "memo"), m.get("click")):
            done.append(mid)
    alert_state["outbox_sent"] = done[-50:]


def main():
    cfg = load(os.path.join(ROOT, "config.json"))
    try:
        result, shape, players = run()
    except SystemExit:
        raise
    except Exception as e:
        log(f"Run failed (will retry next schedule): {e}")
        st_path = os.path.join(DATA, "alert_state.json")
        st = load(st_path, {}) or {}
        send_outbox(st)
        save(st_path, st)
        return 0
    stable = json.dumps(result, sort_keys=True)
    h = hashlib.sha1(stable.encode()).hexdigest()
    latest_path = os.path.join(DATA, "latest.json")
    prev = load(latest_path, {}) or {}

    alert_path = os.path.join(DATA, "alert_state.json")
    alert_state = load(alert_path, {}) or {}
    out, first = alerts_for(result, players, alert_state, cfg)
    send_outbox(alert_state)
    save(alert_path, alert_state)

    if prev.get("content_hash") != h:
        result["content_hash"] = h
        result["generated_at"] = now_iso()
        save(latest_path, result)
        with open(os.path.join(DATA, "REPORT.md"), "w", encoding="utf-8") as f:
            f.write(report_md(result))
        log("Analysis changed; saved.")
    else:
        log("No change in analysis.")
    if shape and not os.path.exists(os.path.join(DATA, "debug_shapes.json")):
        save(os.path.join(DATA, "debug_shapes.json"), shape)
    log(f"{len(out)} alert(s){' (first run: recorded, not sent)' if first else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
