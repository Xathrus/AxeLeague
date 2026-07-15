// Venue projector: shows up to three matches with the most recent scoring,
// refreshing every 3 seconds. Designed to be readable across the room.
(function () {
  const root = document.getElementById("proj-root");
  const clock = document.getElementById("proj-clock");
  // Stable board positions: a match keeps its slot (left / center / right)
  // for as long as it's on screen; new matches only fill vacated slots.
  let slots = [];
  const OUTCOME_CLASS = {
    "1": "o-score", "2": "o-score", "3": "o-score", "4": "o-score", "5": "o-score",
    "B": "o-bull", "KH": "o-kshit", "KD": "o-ksdrop", "KM": "o-ksmiss",
    "D": "o-drop", "M": "o-miss",
  };
  const OUTCOME_TEXT = { "B": "6", "KH": "8", "KD": "D", "KM": "X", "D": "D", "M": "·" };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function chips(outcomes) {
    const row = el("div", "proj-chips");
    for (let i = 0; i < 10; i++) {
      const o = outcomes[i];
      const c = el("span", "proj-chip" + (i === 4 ? " lane-divide" : "")
        + (o ? " " + OUTCOME_CLASS[o] : " empty"));
      c.textContent = o ? (OUTCOME_TEXT[o] || o) : "";
      row.appendChild(c);
    }
    return row;
  }

  function board(b) {
    const card = el("div", "proj-card");
    const stage = b.stage === "playoff" ? " · PLAYOFFS" : "";
    card.appendChild(el("div", "proj-season", b.season.toUpperCase() + stage));

    const score = el("div", "proj-score");
    [["home", b.home_name], ["away", b.away_name]].forEach(([side, name]) => {
      const rowEl = el("div", "proj-team");
      rowEl.appendChild(el("div", "proj-team-name", name));
      rowEl.appendChild(el("div", "proj-team-wins", String(b.wins[side])));
      score.appendChild(rowEl);
    });
    card.appendChild(score);

    const games = el("div", "proj-games");
    b.games.forEach(g => {
      const gEl = el("div", "proj-game" + (g.complete ? " done" : ""));
      gEl.appendChild(el("div", "proj-game-label", "G" + g.number));
      gEl.appendChild(el("div", "", `${g.home_total}–${g.away_total}`));
      games.appendChild(gEl);
    });
    card.appendChild(games);

    if (b.status === "sudden_death") {
      card.appendChild(el("div", "proj-sd", "SUDDEN DEATH"));
    } else {
      const cur = el("div", "proj-current");
      cur.appendChild(el("div", "proj-current-label",
        `GAME ${b.current.game} · SET ${b.current.set}`));
      [["home_player", "home_total", "home_throws"],
       ["away_player", "away_total", "away_throws"]].forEach(([pn, tt, th]) => {
        const line = el("div", "proj-thrower");
        line.appendChild(el("div", "proj-thrower-name",
          b.current[pn] || "—"));
        line.appendChild(chips(b.current[th]));
        line.appendChild(el("div", "proj-thrower-total", String(b.current[tt])));
        cur.appendChild(line);
      });
      card.appendChild(cur);
    }
    return card;
  }

  function achievementsCard(list) {
    const card = el("div", "proj-standings proj-ach");
    card.appendChild(el("div", "proj-current-label", "RECENT ACHIEVEMENTS"));
    const grid = el("div", "proj-ach-grid");
    list.forEach(a => {
      const line = el("div", "proj-ach-row");
      line.appendChild(el("span", "proj-ach-icon", a.icon));
      const txt = el("span", "proj-ach-text");
      txt.appendChild(el("strong", "", a.name));
      const whoBits = a.who + (a.who_team ? " (" + a.who_team + ")" : "");
      txt.appendChild(document.createTextNode(" — " + whoBits
        + (a.detail ? " · " + a.detail : "")));
      line.appendChild(txt);
      grid.appendChild(line);
    });
    card.appendChild(grid);
    return card;
  }

  function standingsCard(st) {
    const card = el("div", "proj-standings");
    card.appendChild(el("div", "proj-current-label",
      "STANDINGS · " + st.season.toUpperCase()));
    const grid = el("div", "proj-standings-grid");
    st.rows.forEach((r, i) => {
      const line = el("div", "proj-standing");
      line.appendChild(el("span", "proj-rank", String(i + 1)));
      line.appendChild(el("span", "proj-standing-team", r.team));
      line.appendChild(el("span", "proj-standing-rec",
        `${r.wins}–${r.losses}`));
      line.appendChild(el("span", "proj-standing-bulls", `${r.bulls} 🎯`));
      grid.appendChild(line);
    });
    card.appendChild(grid);
    return card;
  }

  async function refresh() {
    try {
      const r = await fetch("/api/projector");
      if (!r.ok) return;
      const data = await r.json();
      root.innerHTML = "";
      if (!data.boards.length) {
        slots = [];
        root.appendChild(el("div", "proj-idle",
          "No matches underway — check back soon 🪓"));
        root.className = "proj-root";
        return;
      }
      const byId = {};
      data.boards.forEach(b => { byId[b.match_id] = b; });
      // 1) vacate slots whose match is gone (completed / rotated out)
      slots = slots.map(id => (id !== null && byId[id] !== undefined) ? id : null);
      // 2) place new matches (most recent first) into open slots, left first
      data.boards.forEach(b => {
        if (slots.indexOf(b.match_id) !== -1) return;
        const open = slots.indexOf(null);
        if (open !== -1) slots[open] = b.match_id;
        else if (slots.length < 3) slots.push(b.match_id);
      });
      // 3) drop trailing empties so 1-2 matches don't leave dangling gaps
      while (slots.length && slots[slots.length - 1] === null) slots.pop();

      root.className = "proj-root cols-" + slots.length;
      slots.forEach(id => {
        if (id === null) {
          root.appendChild(el("div", "proj-card proj-empty",
            "Waiting for the next match…"));
        } else {
          root.appendChild(board(byId[id]));
        }
      });
      if (data.achievements && data.achievements.length) {
        root.appendChild(achievementsCard(data.achievements));
      }
      if (data.standings && data.standings.rows.length) {
        root.appendChild(standingsCard(data.standings));
      }
    } catch (e) { /* keep last good frame */ }
  }

  function tick() {
    const d = new Date();
    clock.textContent = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  tick(); setInterval(tick, 15000);
  refresh(); setInterval(refresh, 3000);
})();
