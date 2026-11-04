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


def team_name(roster, users):
    u = users.get(roster.get("owner_id"), {}) or {}
    return (u.get("metadata") or {}).get("team_name") or u.get("display_name") or f"Team {roster.get('roster_id')}"


TRADE_POS = {"QB", "RB", "WR", "TE"}


def trade_ideas(rosters, users, mine, slots, players, ros, settings, cfg):
    """Find trades that raise YOUR projected starting points for the rest of the season
    while also helping (or barely costing) the other team, so they have a reason to say yes.

    Value = projected points of each team's best possible lineup, week by week, through week 17
    (fantasy playoff weeks count 1.25x). That rewards fixing holes, not just collecting names."""
    from itertools import combinations
    pws = int(settings.get("playoff_week_start") or 15)
    weights = {w: (1.25 if w >= pws else 1.0) for w in ros}
    n_weeks = sum(weights.values()) or 1.0
    tcfg = cfg.get("trades", {})
    min_gain = float(tcfg.get("min_gain_per_week", 1.0))
    max_ideas = int(tcfg.get("max_ideas", 8))

    def active_of(r):
        res = set(r.get("reserve") or []) | set(r.get("taxi") or [])
        return [p for p in (r.get("players") or []) if p and p not in res and p in players]

    def value(pids):
        return sum(weights[w] * optimal_lineup(pids, slots, players, ros[w])[0] for w in ros)

    def raw(p):  # name-value proxy: total projected points rest of season
        return sum(ros[w].get(p, 0.0) for w in ros)

    def starts(pids, p):  # weeks p would start in that roster's best lineup
        return sum(1 for w in ros if p in {x for _, x in optimal_lineup(pids, slots, players, ros[w])[1]})

    def pos(p):
        return (players.get(p) or {}).get("position")

    def tradeable(pids, k):
        c = [p for p in pids if pos(p) in TRADE_POS and raw(p) > 5]
        return sorted(c, key=raw, reverse=True)[:k]

    def drop_worst(pids):
        if len(pids) <= 1:
            return pids
        worst = min(pids, key=raw)
        return [p for p in pids if p != worst]

    my_active = active_of(mine)
    base_me = value(my_active)
    my_c = tradeable(my_active, 12)
    ideas = []
    for r in rosters:
        if r is mine:
            continue
        theirs = active_of(r)
        if not theirs:
            continue
        base_t = value(theirs)
        th_c = tradeable(theirs, 12)
        deals = [((g,), (t,)) for g in my_c for t in th_c]
        deals += [(g2, (t,)) for g2 in combinations(my_c[:9], 2) for t in th_c[:6]]
        deals += [((g,), t2) for g in my_c[:6] for t2 in combinations(th_c[:9], 2)]
        for give, get_ in deals:
            me_new = [p for p in my_active if p not in give] + list(get_)
            t_new = [p for p in theirs if p not in get_] + list(give)
            if len(get_) > len(give):
                me_new = drop_worst(me_new)
            if len(give) > len(get_):
                t_new = drop_worst(t_new)
            dm = (value(me_new) - base_me) / n_weeks
            if dm < min_gain:
                continue
            dt = (value(t_new) - base_t) / n_weeks
            vg, vr = sum(raw(p) for p in give), sum(raw(p) for p in get_)
            fairness = vr / vg if vg else 9.9
            # don't overpay: 1-for-1 should be roughly even; consolidating 2-for-1 may cost some depth
            floor = 0.65 if len(give) > len(get_) else 0.8
            if fairness < floor:
                continue
            if dt > 0.3 and fairness <= 1.2:
                likely = "Likely"
            elif dt > -0.5 and fairness <= 1.35:
                likely = "Possible"
            else:
                continue  # long shots aren't worth showing
            ideas.append({"rid": r.get("roster_id"), "give": give, "get": get_, "dm": dm, "dt": dt,
                          "fairness": fairness, "likely": likely, "me_new": me_new, "t_new": t_new,
                          "theirs": theirs})
    # best idea per trade partner first, then the rest, ranked by your gain (likely ones favored)
    ideas.sort(key=lambda x: (x["dm"] + (1.5 if x["likely"] == "Likely" else 0)), reverse=True)
    picked, seen_partner, seen_players = [], {}, set()
    for x in ideas:
        key = (frozenset(x["give"]), frozenset(x["get"]))
        if key in seen_players or seen_partner.get(x["rid"], 0) >= 2:
            continue
        seen_players.add(key)
        seen_partner[x["rid"]] = seen_partner.get(x["rid"], 0) + 1
        picked.append(x)
        if len(picked) >= max_ideas:
            break

    nw = len(ros)
    out = []
    rosters_by_id = {r.get("roster_id"): r for r in rosters}
    for x in picked:
        partner = team_name(rosters_by_id[x["rid"]], users)
        def card(p):
            pl = players.get(p) or {}
            return {"pid": p, "name": name_of(p, players), "pos": pl.get("position"), "team": pl.get("team"),
                    "status": pl.get("injury_status"), "ros_per_week": round(raw(p) / nw, 1)}
        why_you, why_them = [], []
        for p in x["get"]:
            why_you.append(f"{name_of(p, players)} would start {starts(x['me_new'], p)} of {nw} weeks for you")
        for p in x["give"]:
            s_ = starts(my_active, p)
            why_you.append(f"{name_of(p, players)} " + (f"starts only {s_} of {nw} weeks for you" if s_ <= nw // 2
                                                       else f"starts {s_} of {nw} weeks for you"))
        for p in x["give"]:
            why_them.append(f"{name_of(p, players)} would start {starts(x['t_new'], p)} of {nw} weeks for them")
        for p in x["get"]:
            s_ = starts(x["theirs"], p)
            if s_ <= nw // 2:
                why_them.append(f"{name_of(p, players)} only starts {s_} of {nw} weeks on their roster")
        out.append({
            "partner": partner, "partner_roster_id": x["rid"],
            "give": [card(p) for p in x["give"]], "get": [card(p) for p in x["get"]],
            "your_gain_per_week": round(x["dm"], 1), "their_gain_per_week": round(x["dt"], 1),
            "value_ratio": round(x["fairness"], 2), "likelihood": x["likely"],
            "why_you": why_you, "why_them": why_them,
            "summary": (f"Give {' + '.join(name_of(p, players) for p in x['give'])}, get "
                        f"{' + '.join(name_of(p, players) for p in x['get'])} from {partner}: "
                        f"+{x['dm']:.1f} pts/week for you, {x['dt']:+.1f} for them."),
        })
    return out


def league_overview(rosters, users, mine, slots, players, adj, week, settings, lid, matchups_now, sims=4000):
    """Standings, power rankings, this week's matchup and simulated playoff odds."""
    import math
    import random

    SD = 25.0  # rough spread of a fantasy team's weekly score around its projection
    proj_weeks = sorted(adj)
    teams = {}
    for r in rosters:
        st = r.get("settings") or {}
        rid = r.get("roster_id")
        active = [p for p in (r.get("players") or []) if p and p not in set(r.get("reserve") or [])]
        weekly = {w: optimal_lineup(active, slots, players, adj[w])[0] for w in proj_weeks}
        avg = round(sum(weekly.values()) / len(weekly), 1) if weekly else 0.0
        teams[rid] = {
            "roster_id": rid, "team": team_name(r, users), "mine": r is mine,
            "wins": st.get("wins", 0), "losses": st.get("losses", 0), "ties": st.get("ties", 0),
            "pf": round(st.get("fpts", 0) + st.get("fpts_decimal", 0) / 100, 2),
            "pa": round(st.get("fpts_against", 0) + st.get("fpts_against_decimal", 0) / 100, 2),
            "proj_by_week": weekly, "proj_avg": avg,
        }

    # this week's matchup: use each side's set lineup if there is one
    my_rid = mine.get("roster_id")
    def set_lineup_proj(entry, rid):
        starters = [p for p in (entry.get("starters") or []) if p and p != "0"]
        if starters and week in adj:
            return round(sum(adj[week].get(p, 0.0) for p in starters), 1)
        return teams[rid]["proj_by_week"].get(week, teams[rid]["proj_avg"])
    matchup = None
    by_id = {}
    for m in matchups_now or []:
        if m.get("matchup_id") is not None:
            by_id.setdefault(m["matchup_id"], []).append(m)
    for mid, pair in by_id.items():
        rids = [m.get("roster_id") for m in pair]
        if my_rid in rids and len(pair) == 2:
            me_e = pair[0] if pair[0]["roster_id"] == my_rid else pair[1]
            op_e = pair[1] if me_e is pair[0] else pair[0]
            mp, op = set_lineup_proj(me_e, my_rid), set_lineup_proj(op_e, op_e["roster_id"])
            z = (mp - op) / (2 * SD)  # erf argument: diff / (sd_diff * sqrt2), sd_diff = SD*sqrt2
            matchup = {"opponent": teams[op_e["roster_id"]]["team"], "opponent_record":
                       f"{teams[op_e['roster_id']]['wins']}-{teams[op_e['roster_id']]['losses']}",
                       "my_proj": mp, "opp_proj": op, "win_prob": round(0.5 * (1 + math.erf(z)), 3),
                       "opp_best_possible": teams[op_e["roster_id"]]["proj_by_week"].get(week)}

    # remaining regular-season schedule
    pws = int(settings.get("playoff_week_start") or 15)
    n_playoff = int(settings.get("playoff_teams") or 6)
    schedule = {}
    for w in range(week, pws):
        ms = matchups_now if w == week else (get(f"{BASE}/league/{lid}/matchups/{w}") or [])
        pairs = {}
        for m in ms:
            if m.get("matchup_id") is not None:
                pairs.setdefault(m["matchup_id"], []).append(m["roster_id"])
        schedule[w] = [p for p in pairs.values() if len(p) == 2]
    playoff = None
    if schedule and any(schedule.values()):
        made = {rid: 0 for rid in teams}
        seed1 = {rid: 0 for rid in teams}
        rng = random.Random(42)
        for _ in range(sims):
            wins = {rid: t["wins"] + 0.5 * t["ties"] for rid, t in teams.items()}
            pts = {rid: t["pf"] for rid, t in teams.items()}
            for w, pairs in schedule.items():
                for a, b in pairs:
                    if a not in teams or b not in teams:
                        continue
                    sa = rng.gauss(teams[a]["proj_by_week"].get(w, teams[a]["proj_avg"]), SD)
                    sb = rng.gauss(teams[b]["proj_by_week"].get(w, teams[b]["proj_avg"]), SD)
                    pts[a] += sa
                    pts[b] += sb
                    wins[a if sa > sb else b] += 1
            order = sorted(teams, key=lambda r: (wins[r], pts[r]), reverse=True)
            for rid in order[:n_playoff]:
                made[rid] += 1
            seed1[order[0]] += 1
        playoff = {"playoff_teams": n_playoff, "playoff_week_start": pws,
                   "weeks_simulated": sorted(schedule), "sims": sims}
        for rid in teams:
            teams[rid]["playoff_odds"] = round(made[rid] / sims, 3)
            teams[rid]["first_seed_odds"] = round(seed1[rid] / sims, 3)

    standings = sorted(teams.values(), key=lambda t: (t["wins"] + 0.5 * t["ties"], t["pf"]), reverse=True)
    for i, t in enumerate(standings, 1):
        t["rank"] = i
    power = sorted(teams.values(), key=lambda t: t["proj_avg"], reverse=True)
    for i, t in enumerate(power, 1):
        t["power_rank"] = i
    for t in teams.values():
        t["proj_by_week"] = {str(k): v for k, v in t["proj_by_week"].items()}
    # last week's result, with each of your starters' points
    last = None
    if week > 1:
        prev = get(f"{BASE}/league/{lid}/matchups/{week - 1}") or []
        me_e = next((m for m in prev if m.get("roster_id") == my_rid), None)
        if me_e:
            op_e = next((m for m in prev if m.get("matchup_id") == me_e.get("matchup_id")
                         and m.get("roster_id") != my_rid), None)
            pp = me_e.get("players_points") or {}
            starters = [p for p in (me_e.get("starters") or []) if p and p != "0"]
            bench = [p for p in (me_e.get("players") or []) if p and p not in starters]
            last = {
                "week": week - 1, "my_points": me_e.get("points"),
                "opponent": teams[op_e["roster_id"]]["team"] if op_e and op_e.get("roster_id") in teams else None,
                "opp_points": op_e.get("points") if op_e else None,
                "starters": [{"name": name_of(p, players), "pos": (players.get(p) or {}).get("position"),
                              "points": pp.get(p, 0)} for p in starters],
                "bench": sorted([{"name": name_of(p, players), "pos": (players.get(p) or {}).get("position"),
                                  "points": pp.get(p, 0)} for p in bench], key=lambda x: -x["points"]),
            }
    return {"standings": standings, "matchup": matchup, "playoff": playoff, "last_week": last}


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
    matchups_now = get(f"{BASE}/league/{lid}/matchups/{week}") or []
    for m in matchups_now:
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
    window = [w for w in range(week, min(week + int(cfg.get("projection_weeks", 4)), last_week + 1))] or [week]
    weeks = list(range(week, last_week + 1)) or [week]  # rest of season, for trades

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
    have_proj = any(w in pts_by_week for w in window)

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

    ros = {w: adj_pts(w) for w in pts_by_week}
    adj = {w: ros[w] for w in window if w in ros}

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

    try:
        trades = trade_ideas(rosters, users, mine, slots, players, ros, settings, cfg) if ros else []
    except Exception as e:
        log(f"Trade engine failed: {e}")
        trades = []

    try:
        overview = league_overview(rosters, users, mine, slots, players, adj, week, settings, lid,
                                   matchups_now) if adj else None
    except Exception as e:  # the overview must never break the core monitor
        log(f"League overview failed: {e}")
        overview = None

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
        "overview": overview, "trades": trades,
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
                out.append(("high" if (new in BAD_START_STATUS and r["slot"] == "starter") else "default",
                            f"{r['name']} now {new}",
                            f"{r['name']} ({r['pos']}, {r['team']}) changed from {old or 'healthy'} to {new}.",
                            "warning"))
            else:
                out.append(("default", f"{r['name']} cleared",
                            f"{r['name']} no longer has an injury designation.", "white_check_mark"))
        prev_status[r["pid"]] = new

    # roster changes (waiver results, trades, drops)
    now_roster = {r["pid"]: r["name"] for r in result["roster"]}
    prev_roster = alert_state.get("roster")
    if prev_roster is not None:
        added = [n for p, n in now_roster.items() if p not in prev_roster]
        dropped = [n for p, n in prev_roster.items() if p not in now_roster]
        if added or dropped:
            parts = ([f"Added {', '.join(added)}"] if added else []) + ([f"Dropped {', '.join(dropped)}"] if dropped else [])
            out.append(("default", "Roster change", ". ".join(parts) + ".", "inbox_tray"))
    alert_state["roster"] = now_roster

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
    # every alert also goes to the events log the event watcher reads
    if out:
        ev_path = os.path.join(DATA, "events.json")
        events = load(ev_path, []) or []
        stamp = now_iso()
        for i, (prio, title, body, tags_) in enumerate(out):
            kind = {"warning": "injury", "white_check_mark": "injury", "inbox_tray": "roster",
                    "rotating_light": "lineup", "arrows_counterclockwise": "lineup",
                    "chart_with_upwards_trend": "waiver", "shield": "stream"}.get(tags_, "other")
            events.append({"id": f"{stamp}-{i}", "time": stamp, "week": week, "type": kind,
                           "severity": prio, "title": title, "body": body})
        save(ev_path, events[-200:])

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
    ov = r.get("overview") or {}
    if ov.get("matchup"):
        m = ov["matchup"]
        L.append("## This week's matchup")
        L.append(f"vs **{m['opponent']}** ({m['opponent_record']}): you **{m['my_proj']:.1f}** – {m['opp_proj']:.1f} projected · "
                 f"win chance **{m['win_prob']*100:.0f}%**\n")
    if ov.get("standings"):
        po = ov.get("playoff")
        L.append("## League overview")
        if po:
            L.append(f"Top {po['playoff_teams']} make the playoffs (starting week {po['playoff_week_start']}). "
                     f"Odds come from {po['sims']:,} simulated seasons using projections.\n")
        L.append("| # | Team | W-L | PF | PA | Power | Proj/wk | Playoffs |")
        L.append("|---|---|---|---|---|---|---|---|")
        for t in ov["standings"]:
            nm = f"**{t['team']}**" if t["mine"] else t["team"]
            odds = f"{t['playoff_odds']*100:.0f}%" if "playoff_odds" in t else "—"
            L.append(f"| {t['rank']} | {nm} | {t['wins']}-{t['losses']} | {t['pf']:.1f} | {t['pa']:.1f} | "
                     f"{t['power_rank']} | {t['proj_avg']:.1f} | {odds} |")
        L.append("")
    if r.get("trades"):
        L.append("## Trade ideas")
        L.append("Gains are projected starting points per week, rest of season (playoff weeks weighted 1.25x).\n")
        for t in r["trades"]:
            L.append(f"- **{t['likelihood']}** · {t['summary']}")
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
