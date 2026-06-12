/* Abilene Axe League — live scorekeeper */
(function () {
  const root = document.getElementById("sk-root");
  const matchId = window.MATCH_ID;

  const OUTCOME_LABEL = {
    "1": "1", "2": "2", "3": "3", "4": "4", "5": "5",
    "B": "6", "KH": "8", "KD": "KD", "KM": "KM", "D": "D", "M": "M",
  };
  const OUTCOME_NAME = {
    "1": "1 point", "2": "2 points", "3": "3 points", "4": "4 points",
    "5": "5 points", "B": "Bullseye (6)", "KH": "Killshot hit (8)",
    "KD": "Killshot drop (0)", "KM": "Killshot miss (0)",
    "D": "Drop (0)", "M": "Miss (0)",
  };
  const OUTCOME_CLASS = {
    "1": "o-score", "2": "o-score", "3": "o-score", "4": "o-score", "5": "o-score",
    "B": "o-bull", "KH": "o-kshit", "KD": "o-ksdrop", "KM": "o-ksmiss",
    "D": "o-drop", "M": "o-miss",
  };

  let state = null;
  let lastJSON = "";
  const ui = {
    game: 0,            // index 0..2
    set: 0,             // index 0..2
    ks: {},             // playerId -> killshot armed
    editing: null,      // {throwId, outcome, label}
    navigated: false,   // auto-jump to first open set once
    error: "",
  };

  // ------------------------------------------------------------------ api
  async function api(path, body) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || data.ok === false) {
      ui.error = data.error || "Something went wrong.";
    } else {
      ui.error = "";
    }
    await refresh(true);
  }

  async function refresh(force) {
    if (ui.editing && !force) return; // don't redraw under an open editor
    const r = await fetch(`/api/match/${matchId}/state`);
    if (!r.ok) return;
    const text = await r.text();
    if (!force && text === lastJSON) return;
    lastJSON = text;
    state = JSON.parse(text);
    if (!ui.navigated) autoNavigate();
    render();
  }

  function autoNavigate() {
    ui.navigated = true;
    for (let gi = 0; gi < state.games.length; gi++) {
      const g = state.games[gi];
      if (!g.complete) {
        ui.game = gi;
        for (let si = 0; si < g.sets.length; si++) {
          if (!g.sets[si].complete) { ui.set = si; return; }
        }
        ui.set = 0;
        return;
      }
    }
    ui.game = 2; ui.set = 2;
  }

  // ------------------------------------------------------------- rendering
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function render() {
    if (!state) return;
    const editing = ui.editing; // survives rerender
    root.innerHTML = "";
    root.appendChild(header());
    root.appendChild(statusBanner());
    if (ui.error) root.appendChild(el("div", "sk-error", ui.error));
    root.appendChild(gameTabs());
    root.appendChild(setTabs());
    root.appendChild(setView());
    ui.editing = editing;
  }

  function header() {
    const m = state.match, st = state.status;
    const h = el("div", "sk-header");
    const back = el("a", "sk-back", "← Schedule");
    back.href = m.stage === "playoff"
      ? `/season/${m.season_id}/playoffs`
      : `/season/${m.season_id}/schedule`;
    h.appendChild(back);

    const title = el("div", "sk-title");
    title.appendChild(el("span", "sk-team home", m.home_team_name || "TBD"));
    const score = el("span", "sk-matchscore",
      `${st.home_wins} – ${st.away_wins}`);
    title.appendChild(score);
    title.appendChild(el("span", "sk-team away", m.away_team_name || "TBD"));
    h.appendChild(title);

    const sub = [];
    if (m.stage === "playoff") sub.push("Playoff match");
    else if (m.week) sub.push(`Week ${m.week}`);
    if (st.ties) sub.push(`${st.ties} game${st.ties > 1 ? "s" : ""} tied`);
    h.appendChild(el("div", "muted-small center", sub.join(" · ")));
    return h;
  }

  function statusBanner() {
    const st = state.status, m = state.match;
    const wrap = el("div", "sk-banner-wrap");

    if (st.state === "completed") {
      const b = el("div", "sk-banner done",
        `Final — ${st.winner_team_name} win the match ${st.home_wins}–${st.away_wins}` +
        (m.sudden_death_winner_team_id ? " (sudden death)" : ""));
      wrap.appendChild(b);
      if (m.stage !== "playoff") {
        const btn = el("button", "ghost", "Reopen match");
        btn.onclick = () => api(`/api/match/${m.id}/reopen`);
        wrap.appendChild(btn);
      }
    } else if (st.state === "decided") {
      const b = el("div", "sk-banner decided",
        `${st.winner_team_name} have won the match` +
        (m.sudden_death_winner_team_id ? " (sudden death)" : "") +
        ` — ${st.home_wins} game wins to ${st.away_wins}.`);
      wrap.appendChild(b);
      const btn = el("button", "primary", "Complete match");
      btn.onclick = () => api(`/api/match/${m.id}/complete`);
      wrap.appendChild(btn);
    } else if (st.state === "sudden_death") {
      const b = el("div", "sk-banner sd");
      b.appendChild(el("div", "", "SUDDEN DEATH — one throw per player. Scorekeeper declares the winner:"));
      const sel = el("select");
      sel.appendChild(new Option("Select winner…", ""));
      sel.appendChild(new Option(m.home_team_name, m.home_team_id));
      sel.appendChild(new Option(m.away_team_name, m.away_team_id));
      const btn = el("button", "primary", "Declare winner");
      btn.onclick = () => {
        if (!sel.value) return;
        api(`/api/match/${m.id}/sudden_death`, { winner_team_id: +sel.value });
      };
      const row = el("div", "sd-row");
      row.appendChild(sel); row.appendChild(btn);
      b.appendChild(row);
      wrap.appendChild(b);
    }
    return wrap;
  }

  function gameTabs() {
    const tabs = el("div", "sk-tabs");
    state.games.forEach((g, i) => {
      const t = el("button",
        "sk-tab" + (i === ui.game ? " active" : "") + (g.complete ? " done" : ""));
      let res = "";
      if (g.complete) {
        res = g.winner === "tie" ? " · TIE" :
          g.winner === "home" ? " · H" : " · A";
      }
      t.textContent = `Game ${g.number}  ${g.home_total}–${g.away_total}${res}`;
      t.onclick = () => { ui.game = i; ui.set = 0; ui.editing = null; render(); };
      tabs.appendChild(t);
    });
    return tabs;
  }

  function setTabs() {
    const g = state.games[ui.game];
    const tabs = el("div", "sk-tabs sub");
    g.sets.forEach((s, i) => {
      const t = el("button",
        "sk-tab small" + (i === ui.set ? " active" : "") + (s.complete ? " done" : ""));
      t.textContent = `Set ${s.number}  ${s.home_total}–${s.away_total}`;
      t.onclick = () => { ui.set = i; ui.editing = null; render(); };
      tabs.appendChild(t);
    });
    return tabs;
  }

  function setView() {
    const s = state.games[ui.game].sets[ui.set];
    const view = el("div", "sk-set");

    const swapped = s.home_throws.length >= 5 && s.away_throws.length >= 5;
    if (swapped && !s.complete) {
      view.appendChild(el("div", "swap-chip", "⇄ Lanes swapped for throws 6–10"));
    }

    const panels = el("div", "sk-panels");
    const home = panel(s, "home");
    const away = panel(s, "away");
    if (swapped) { panels.appendChild(away); panels.appendChild(home); }
    else { panels.appendChild(home); panels.appendChild(away); }
    view.appendChild(panels);
    return view;
  }

  function panel(s, side) {
    const m = state.match;
    const locked = state.status.state === "completed" || m.completed;
    const pid = s[side + "_player_id"];
    const pname = s[side + "_player_name"];
    const throws = s[side + "_throws"];
    const ksLeft = s[side + "_ks_left"];
    const teamName = side === "home" ? m.home_team_name : m.away_team_name;

    const p = el("div", `sk-panel ${side}`);
    p.appendChild(el("div", "sk-panel-team", teamName || "TBD"));

    // ---- player assignment / name
    if (!pid) {
      const sel = el("select", "player-select");
      sel.appendChild(new Option(`Select ${teamName} thrower…`, ""));
      state.rosters[side].forEach(pl => sel.appendChild(new Option(pl.name, pl.id)));
      sel.onchange = () => {
        if (sel.value) api(`/api/set/${s.id}/assign`, { [side + "_player_id"]: +sel.value });
      };
      p.appendChild(sel);
      p.appendChild(el("div", "muted-small center", "Pick a thrower to start scoring"));
      return p;
    }

    const nameRow = el("div", "sk-player-row");
    nameRow.appendChild(el("div", "sk-player-name", pname));
    if (throws.length === 0 && !locked) {
      const change = el("button", "ghost tiny", "change");
      change.onclick = () => {
        api(`/api/set/${s.id}/assign`, { [side + "_player_id"]: null });
      };
      nameRow.appendChild(change);
    }
    nameRow.appendChild(el("div", "sk-set-total", String(s[side + "_total"])));
    p.appendChild(nameRow);

    // ---- killshot pips
    const pipRow = el("div", "ks-pips");
    pipRow.appendChild(el("span", "muted-small", "KS calls "));
    const shown = Math.max(ksLeft, 0);
    for (let i = 0; i < Math.min(shown, 6); i++) pipRow.appendChild(el("span", "pip on"));
    if (shown === 0) pipRow.appendChild(el("span", "pip off"));
    pipRow.appendChild(el("span", "muted-small", ` ${shown} left`));
    p.appendChild(pipRow);

    // ---- throw grid
    const grid = el("div", "throw-grid");
    for (let i = 0; i < 10; i++) {
      const t = throws[i];
      const cell = el("div", "tcell" + (i === 4 ? " lane-divide" : ""));
      if (t) {
        cell.classList.add("filled", OUTCOME_CLASS[t.outcome]);
        cell.textContent = OUTCOME_LABEL[t.outcome];
        cell.title = `Throw ${t.n}: ${OUTCOME_NAME[t.outcome]} — tap to edit`;
        if (!locked) {
          cell.onclick = () => {
            ui.editing = { throwId: t.id, outcome: t.outcome, n: t.n, player: pname };
            render();
          };
        }
      } else {
        cell.classList.add("empty");
        if (i === throws.length && !locked && !s.complete) cell.classList.add("next");
      }
      grid.appendChild(cell);
    }
    p.appendChild(grid);

    // ---- edit popover for a throw belonging to this player
    if (ui.editing && throws.some(t => t.id === ui.editing.throwId)) {
      p.appendChild(editBox(s, side));
      return p;
    }

    // ---- entry buttons
    if (locked) {
      p.appendChild(el("div", "muted-small center", "Match completed — scoring locked"));
      return p;
    }
    if (throws.length >= 10) {
      p.appendChild(el("div", "muted-small center", "All 10 throws recorded"));
    } else {
      p.appendChild(buttons(s, pid, ksLeft));
    }
    return p;
  }

  function buttons(s, pid, ksLeft) {
    const wrap = el("div", "btn-pad");
    const armed = !!ui.ks[pid];

    const send = (outcome) => {
      ui.ks[pid] = false;
      api(`/api/set/${s.id}/throw`, { player_id: pid, outcome });
    };

    if (!armed) {
      const row1 = el("div", "btn-row");
      ["1", "2", "3", "4", "5"].forEach(v => {
        const b = el("button", "scorebtn", v);
        b.onclick = () => send(v);
        row1.appendChild(b);
      });
      const bull = el("button", "scorebtn bull", "6");
      bull.title = "Bullseye";
      bull.onclick = () => send("B");
      row1.appendChild(bull);
      wrap.appendChild(row1);

      const row2 = el("div", "btn-row");
      const ks = el("button", "scorebtn ks", "KS");
      ks.title = "Call killshot";
      ks.disabled = ksLeft <= 0;
      ks.onclick = () => { ui.ks[pid] = true; render(); };
      const drop = el("button", "scorebtn drop", "Drop");
      drop.onclick = () => send("D");
      const miss = el("button", "scorebtn miss", "Miss");
      miss.onclick = () => send("M");
      const undo = el("button", "scorebtn undo", "Undo");
      undo.onclick = () => api(`/api/set/${s.id}/undo`, { player_id: pid });
      [ks, drop, miss, undo].forEach(b => row2.appendChild(b));
      wrap.appendChild(row2);
    } else {
      wrap.appendChild(el("div", "ks-armed-label", "KILLSHOT CALLED — record the result"));
      const row = el("div", "btn-row");
      const hit = el("button", "scorebtn kshit", "KS HIT · 8");
      hit.onclick = () => send("KH");
      const kd = el("button", "scorebtn drop", "KS Drop");
      kd.onclick = () => send("KD");
      const km = el("button", "scorebtn miss", "KS Miss");
      km.onclick = () => send("KM");
      const cancel = el("button", "scorebtn undo", "Cancel");
      cancel.onclick = () => { ui.ks[pid] = false; render(); };
      [hit, kd, km, cancel].forEach(b => row.appendChild(b));
      wrap.appendChild(row);
    }
    return wrap;
  }

  function editBox(s) {
    const e = ui.editing;
    const box = el("div", "edit-box");
    box.appendChild(el("div", "muted-small",
      `Editing throw ${e.n} for ${e.player}`));
    const sel = el("select");
    Object.keys(OUTCOME_NAME).forEach(o => {
      const opt = new Option(OUTCOME_NAME[o], o);
      if (o === e.outcome) opt.selected = true;
      sel.appendChild(opt);
    });
    const save = el("button", "primary", "Save");
    save.onclick = () => {
      const v = sel.value;
      ui.editing = null;
      api(`/api/throw/${e.throwId}/edit`, { outcome: v });
    };
    const cancel = el("button", "ghost", "Cancel");
    cancel.onclick = () => { ui.editing = null; render(); };
    const row = el("div", "sd-row");
    row.appendChild(sel); row.appendChild(save); row.appendChild(cancel);
    box.appendChild(row);
    return box;
  }

  // ------------------------------------------------------------------ poll
  refresh(true);
  setInterval(() => {
    if (!ui.editing && !document.hidden) refresh(false);
  }, 3000);
})();
