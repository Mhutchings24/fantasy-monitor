/* Conorrhea HQ — reads the monitor's data files from this same site. */
(function () {
  "use strict";
  var state = { data: null, events: [], latestMd: "", indexMd: "", reportPath: null };
  var $ = function (id) { return document.getElementById(id); };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function n1(x) { return (Math.round((+x || 0) * 10) / 10).toFixed(1); }
  function pct(x) { return Math.round((+x || 0) * 100) + "%"; }
  function signed(x) { x = +x || 0; return (x >= 0 ? "+" : "") + n1(x); }
  function ago(iso) {
    if (!iso) return "";
    var m = Math.round((Date.now() - Date.parse(iso)) / 60000);
    if (m < 1) return "just now";
    if (m < 60) return m + " min ago";
    var h = Math.round(m / 60);
    return h < 48 ? h + " hr ago" : Math.round(h / 24) + " days ago";
  }
  function fetchText(path) {
    return fetch(path + "?t=" + Date.now(), { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(path + " returned " + r.status);
      return r.text();
    });
  }
  function md(text) {
    if (window.marked) return window.marked.parse(text);
    return "<pre>" + esc(text) + "</pre>";
  }

  /* --------------------------------------------------------------- tabs */
  function show(tab) {
    document.querySelectorAll("nav button").forEach(function (b) {
      b.setAttribute("aria-selected", b.dataset.t === tab ? "true" : "false");
    });
    document.querySelectorAll("section.tab").forEach(function (s) {
      s.classList.toggle("on", s.id === "t-" + tab);
    });
    try { localStorage.setItem("tab", tab); } catch (e) {}
    if (location.hash !== "#" + tab) history.replaceState(null, "", "#" + tab);
    window.scrollTo(0, 0);
  }
  document.querySelectorAll("nav button").forEach(function (b) {
    b.addEventListener("click", function () { show(b.dataset.t); });
  });

  /* --------------------------------------------------------------- render */
  function bottomLine(mdText) {
    var m = /##\s*Bottom line\s*\n([\s\S]*?)(\n##\s|$)/i.exec(mdText || "");
    if (!m) return [];
    return m[1].split("\n").filter(function (l) { return /^\s*-\s+/.test(l); })
      .map(function (l) { return l.replace(/^\s*-\s+/, ""); });
  }
  function reportTitle(mdText) {
    var m = /^#\s+(.+)$/m.exec(mdText || "");
    return m ? m[1] : "Latest report";
  }

  function renderToday(d) {
    var lu = d.lineup || {}, urgent = [];
    (lu.problems || []).forEach(function (p) { urgent.push(p.text); });
    (lu.swaps || []).forEach(function (s) { urgent.push(s.text); });
    var top = (d.waivers || [])[0];
    var h = "";
    if (urgent.length) {
      h += '<div class="play"><h2>Fix your lineup</h2><ul>' +
        urgent.map(function (t) { return "<li>" + esc(t) + "</li>"; }).join("") + "</ul></div>";
    } else {
      h += '<div class="play calm"><h2>Lineup is set</h2><p class="small">Your starters are the best available by projection. ' +
        "Check the analyst's call below for news-based changes.</p></div>";
    }
    var bl = bottomLine(state.latestMd);
    if (bl.length) {
      h += '<div class="box"><div class="row"><h3 style="margin:0">Analyst\'s call</h3>' +
        '<button class="backlink" data-open-latest>Full report</button></div>' +
        '<p class="small muted">' + esc(reportTitle(state.latestMd)) + "</p>" +
        '<div class="md small">' + md(bl.map(function (b) { return "- " + b; }).join("\n")) + "</div></div>";
    }
    var m = (d.overview || {}).matchup;
    if (m) {
      h += '<div class="box"><div class="row"><div><strong>Week ' + esc(d.week) + " vs " + esc(m.opponent) +
        '</strong><div class="small muted">Projected ' + n1(m.my_proj) + " – " + n1(m.opp_proj) + "</div></div>" +
        '<div class="gain num">' + pct(m.win_prob) + "</div></div></div>";
    }
    if (top) {
      h += '<div class="box"><strong>Best waiver add:</strong> ' + esc(top.name) + " (" + esc(top.pos) + ", " + esc(top.team || "FA") +
        ") for " + esc(top.drop_name) + ' <span class="pill">' + signed(top.gain_window) + " pts</span></div>";
    }
    var ev = (state.events || []).slice(-8).reverse();
    if (ev.length) {
      h += '<h3>Recent alerts</h3><div class="box"><ul class="clean">' + ev.map(function (e) {
        return '<li><div class="row"><strong>' + esc(e.title) + '</strong><span class="small muted">' + esc(ago(e.time)) +
          '</span></div><div class="small">' + esc(e.body) + "</div></li>";
      }).join("") + "</ul></div>";
    }
    $("t-today").innerHTML = h;
  }

  function fieldBar(p) {
    var lines = "";
    for (var y = 10; y <= 90; y += 10) {
      var x = 9 + y * 0.82;
      lines += '<div class="yl" style="left:' + x + '%"></div><div class="yn" style="left:' + x + '%">' + (y <= 50 ? y : 100 - y) + "</div>";
    }
    var pos = 9 + Math.max(0.02, Math.min(0.98, p)) * 82;
    return '<div class="field" role="img" aria-label="Win chance ' + pct(p) + '">' +
      '<div class="ez l">THEM</div><div class="ez r">YOU</div>' + lines +
      '<div class="ball" style="left:50%" data-left="' + pos + '%"></div></div>';
  }

  function renderMatchup(d) {
    var ov = d.overview || {}, m = ov.matchup, h = "<h2>Week " + esc(d.week) + " matchup</h2>";
    if (!m) { $("t-matchup").innerHTML = h + '<p class="muted">No matchup found for this week.</p>'; return; }
    h += '<div class="box"><div class="row"><div><div class="small muted">Win chance vs ' + esc(m.opponent) + " (" + esc(m.opponent_record) +
      ')</div><div class="wc num">' + pct(m.win_prob) + '</div></div><div class="small muted r" style="text-align:right">You ' +
      '<strong class="num">' + n1(m.my_proj) + '</strong><br>Them <strong class="num">' + n1(m.opp_proj) + "</strong></div></div>" +
      fieldBar(m.win_prob) + '<p class="small muted" style="margin-top:8px">Based on projections for the lineups each team has set. ' +
      "Your lineup shows any player Sleeper lists as out at zero.</p></div>";
    var w = String(d.week);
    var starters = (d.roster || []).filter(function (p) { return p.slot === "starter"; });
    h += '<h3>Your starters</h3><div class="box scroll"><table><tr><th>Player</th><th>Pos</th><th class="r">Proj</th></tr>' +
      starters.map(function (p) {
        var st = p.status || (p.bye_this_week ? "BYE" : "");
        return "<tr><td>" + esc(p.name) + (st ? ' <span class="pill red">' + esc(st) + "</span>" : "") +
          '<div class="small muted">' + esc(p.team || "FA") + "</div></td><td>" + esc(p.pos) + '</td><td class="r num">' +
          n1((p.proj_by_week || {})[w]) + "</td></tr>";
      }).join("") + "</table></div>";
    var lu = d.lineup || {};
    if (lu.best_lineup) {
      h += '<h3>Best lineup by projection</h3><div class="box scroll"><table><tr><th>Slot</th><th>Player</th><th class="r">Proj</th></tr>' +
        lu.best_lineup.map(function (s) {
          return "<tr><td>" + esc(s.slot) + "</td><td>" + esc(s.name || "—") + '</td><td class="r num">' + n1(s.proj) + "</td></tr>";
        }).join("") + '</table><p class="small muted" style="margin:8px 0 0">Projected ' + n1(lu.best_projected) +
        " vs your current " + n1(lu.current_projected) + ".</p></div>";
    }
    $("t-matchup").innerHTML = h;
    requestAnimationFrame(function () {
      var b = document.querySelector(".field .ball");
      if (b) setTimeout(function () { b.style.left = b.dataset.left; }, 60);
    });
  }

  function playerLines(list) {
    return list.map(function (p) {
      return '<div class="pl">' + esc(p.name) + (p.status ? ' <span class="pill red">' + esc(p.status) + "</span>" : "") +
        "<span>" + esc(p.pos) + ", " + esc(p.team || "FA") + " · " + n1(p.ros_per_week) + " pts/wk</span></div>";
    }).join("");
  }

  function renderTrades(d) {
    var t = d.trades || [], h = "<h2>Trade ideas</h2>";
    h += '<p class="small muted">Each trade raises your projected starting points for the rest of the season and also helps the other team, ' +
      "so they have a reason to say yes. Check injury news before sending one.</p>";
    if (!t.length) { $("t-trades").innerHTML = h + '<div class="box">No trades that help both sides right now. New ideas appear as rosters and injuries change.</div>'; return; }
    h += t.map(function (x) {
      var cls = x.likelihood === "Likely" ? "" : "flag";
      return '<div class="box trade"><div class="row"><strong>' + esc(x.partner) + '</strong><span class="pill ' + cls + '">' +
        esc(x.likelihood) + "</span></div>" +
        '<div class="sides"><div class="side"><h4>You give</h4>' + playerLines(x.give) + '</div><div class="swap" aria-hidden="true">⇄</div>' +
        '<div class="side"><h4>You get</h4>' + playerLines(x.get) + "</div></div>" +
        '<div class="row"><div><span class="gain num">' + signed(x.your_gain_per_week) + '</span> <span class="small muted">pts/wk for you</span></div>' +
        '<div class="small muted num">' + signed(x.their_gain_per_week) + " for them</div></div>" +
        '<ul class="why">' + (x.why_you || []).concat(x.why_them || []).map(function (w) { return "<li>" + esc(w) + "</li>"; }).join("") +
        "</ul></div>";
    }).join("");
    $("t-trades").innerHTML = h;
  }

  function renderWaivers(d) {
    var h = "<h2>Waiver wire</h2>";
    if (d.faab) h += '<div class="box row"><span>FAAB left</span><span class="gain num">$' + esc(d.faab.remaining) + "</span></div>";
    var weeks = d.weeks_projected || [];
    h += '<p class="small muted">Gain is extra projected points in your starting lineup over weeks ' + esc(weeks[0]) + "–" +
      esc(weeks[weeks.length - 1]) + ", after dropping the player shown.</p>";
    var w = d.waivers || [];
    if (!w.length) h += '<div class="box">No free agent improves your lineup right now.</div>';
    else h += '<div class="box"><ul class="clean">' + w.map(function (x) {
      return '<li><div class="row"><div><strong>' + esc(x.name) + "</strong> " + (x.status ? '<span class="pill red">' + esc(x.status) + "</span>" : "") +
        '<div class="small muted">' + esc(x.pos) + ", " + esc(x.team || "FA") + " · drop " + esc(x.drop_name) +
        (x.trending_adds_24h ? " · added in " + Number(x.trending_adds_24h).toLocaleString() + " leagues today" : "") +
        '</div></div><div style="text-align:right"><div class="gain num">' + signed(x.gain_window) + '</div><div class="small muted num">' +
        signed(x.gain_this_week) + " this wk</div></div></div></li>";
    }).join("") + "</ul></div>";
    if ((d.streams || []).length) {
      h += "<h3>Kicker &amp; defense streams</h3><div class=\"box\"><ul class=\"clean\">" + d.streams.map(function (x) {
        return '<li><div class="row"><span>' + esc(x.name) + ' <span class="small muted">for ' + esc(x.drop_name) + "</span></span>" +
          '<span class="num">' + signed(x.gain_this_week) + " this wk</span></div></li>";
      }).join("") + "</ul></div>";
    }
    $("t-waivers").innerHTML = h;
  }

  function renderLeague(d) {
    var ov = d.overview || {}, s = ov.standings || [], po = ov.playoff;
    var h = "<h2>" + esc((d.league || {}).name || "League") + "</h2>";
    if (po) h += '<p class="small muted">Top ' + esc(po.playoff_teams) + " make the playoffs, starting week " + esc(po.playoff_week_start) +
      ". Playoff odds come from " + Number(po.sims).toLocaleString() + " simulated seasons. Power rank orders teams by projected points per week.</p>";
    h += '<div class="box scroll"><table><tr><th>#</th><th>Team</th><th class="nw">W-L</th><th>Playoffs</th></tr>' +
      s.map(function (t) {
        var odds = t.playoff_odds != null ? t.playoff_odds : null;
        return '<tr class="' + (t.mine ? "me" : "") + '"><td class="num">' + esc(t.rank) + "</td><td>" + esc(t.team) +
          '<div class="small muted num">Power #' + esc(t.power_rank) + " · " + n1(t.pf) + " pts</div>" +
          '</td><td class="num nw">' + esc(t.wins) + "-" + esc(t.losses) +
          '</td><td style="min-width:64px">' +
          (odds == null ? "—" : '<span class="num">' + pct(odds) + '</span><div class="bar"><i style="width:' + Math.round(odds * 100) + '%"></i></div>') +
          "</td></tr>";
      }).join("") + "</table></div>";
    var lw = ov.last_week;
    if (lw) {
      var won = (+lw.my_points || 0) > (+lw.opp_points || 0);
      h += "<h3>Last week</h3><div class=\"box\"><div class=\"row\"><strong>" + (won ? "Won" : "Lost") + " vs " + esc(lw.opponent) +
        '</strong><span class="num">' + n1(lw.my_points) + " – " + n1(lw.opp_points) + "</span></div>" +
        '<table style="margin-top:8px"><tr><th>Starter</th><th class="r">Pts</th></tr>' + (lw.starters || []).map(function (p) {
          return "<tr><td>" + esc(p.name) + ' <span class="small muted">' + esc(p.pos) + '</span></td><td class="r num">' + n1(p.points) + "</td></tr>";
        }).join("") + "</table></div>";
    }
    $("t-league").innerHTML = h;
  }

  function renderReports() {
    var el = $("t-reports");
    if (state.reportPath) {
      fetchText("reports/" + state.reportPath).then(function (t) {
        el.innerHTML = '<button class="backlink" data-reports-home>All reports</button><div class="md">' + md(t) + "</div>";
      }).catch(function (e) { el.innerHTML = '<div class="err">Couldn\'t open that report: ' + esc(e.message) + "</div>"; });
      return;
    }
    var h = "";
    if (state.latestMd) h += '<div class="box md">' + md(state.latestMd) + "</div>";
    if (state.indexMd) h += '<h3>All reports</h3><div class="box md" data-index>' + md(state.indexMd.replace(/^#.*\n/, "")) + "</div>";
    el.innerHTML = h || '<p class="muted">No reports yet. The first one arrives with the next scheduled analysis.</p>';
  }

  document.addEventListener("click", function (e) {
    var a = e.target.closest("a");
    if (a && a.closest("[data-index]")) {
      var href = a.getAttribute("href") || "";
      if (/\.md$/.test(href) && !/^https?:/.test(href)) {
        e.preventDefault();
        state.reportPath = href === "LATEST.md" ? null : href;
        renderReports(); window.scrollTo(0, 0);
      }
    }
    if (e.target.closest("[data-reports-home]")) { state.reportPath = null; renderReports(); }
    if (e.target.closest("[data-open-latest]")) { state.reportPath = null; renderReports(); show("reports"); }
  });

  /* --------------------------------------------------------------- load */
  function renderAll() {
    var d = state.data;
    if (!d) return;
    $("teamName").textContent = ((d.team || {}).name || "Your team").trim() + " HQ";
    var st = ((d.overview || {}).standings || []).filter(function (t) { return t.mine; })[0];
    $("teamSub").textContent = (d.league || {}).name + " · " + (d.team || {}).record + (st ? " · " + ordinal(st.rank) : "") +
      " · week " + d.week;
    var age = (Date.now() - Date.parse(d.generated_at)) / 60000;
    var f = $("fresh");
    f.textContent = "Data changed " + ago(d.generated_at);
    f.classList.toggle("stale", false);
    renderToday(d); renderMatchup(d); renderTrades(d); renderWaivers(d); renderLeague(d); renderReports();
    void age;
  }
  function ordinal(n) { var s = ["th", "st", "nd", "rd"], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); }

  function load() {
    return Promise.all([
      fetchText("data/latest.json").then(JSON.parse),
      fetchText("data/events.json").then(JSON.parse).catch(function () { return []; }),
      fetchText("reports/LATEST.md").catch(function () { return ""; }),
      fetchText("reports/README.md").catch(function () { return ""; })
    ]).then(function (r) {
      state.data = r[0]; state.events = r[1] || []; state.latestMd = r[2]; state.indexMd = r[3];
      $("error").innerHTML = "";
      renderAll();
    }).catch(function (e) {
      $("error").innerHTML = '<div class="err">Couldn\'t load your league data (' + esc(e.message) +
        "). The monitor may not have run yet. This page retries every 2 minutes.</div>";
    });
  }

  var start = (location.hash || "").slice(1);
  if (!start) { try { start = localStorage.getItem("tab") || ""; } catch (e) {} }
  if (start && document.getElementById("t-" + start)) show(start);
  window.addEventListener("hashchange", function () {
    var t = location.hash.slice(1);
    if (document.getElementById("t-" + t)) show(t);
  });
  load();
  setInterval(load, 120000);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) load(); });
})();
