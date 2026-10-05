import {
  api,
  setContext,
  esc,
  pretty,
  icon,
  credit,
  toast,
  modal,
  confirm,
  field,
  textarea,
  teamName,
  courtName,
  score,
  badge,
  errorText,
  contrastText,
  table,
} from "./common.js";
let match,
  session,
  queue = [],
  busy = false,
  conflict = false,
  fenced = false,
  flipped = false,
  acknowledged = false,
  connection = "Connecting",
  pollTimer,
  clockTimer,
  renewTimer,
  wakeSentinel,
  wakeState = "Not requested",
  storageAvailable = true,
  receivedAt = 0;
const app = document.querySelector("#app");
const whistle = new Audio("/static/whistle.wav");
whistle.preload = "auto";
whistle.volume = 1;
let soundState = "Tap Test whistle",
  wakeWanted = false,
  wakeRequestInFlight = false;
const queueKey = () => `courtline-queue:${match.id}`;
function storeQueue() {
  try {
    localStorage.setItem(queueKey(), JSON.stringify(queue));
  } catch {
    storageAvailable = false;
  }
}
function loadQueue() {
  try {
    queue = JSON.parse(localStorage.getItem(queueKey()) || "[]");
    if (!Array.isArray(queue)) queue = [];
  } catch {
    storageAvailable = false;
    queue = [];
  }
}
const clone = (v) => JSON.parse(JSON.stringify(v));
function accept(data) {
  match = data.match || data;
  session = data.session || session;
  receivedAt = performance.now();
  if (data.csrf_token) setContext(data.csrf_token, "");
  if (["ended", "cancelled", "abandoned"].includes(match.status)) {
    releaseWake();
    whistle.pause();
  }
}
function pendingState() {
  const s = clone(match.state);
  if (conflict || fenced) return s;
  for (const a of queue) {
    if (
      a.generation !== session.generation ||
      a.expected_version < match.version
    )
      continue;
    const current = s.sets[s.set_index];
    if (a.type === "point") {
      current[a.payload.team]++;
      s.serve = a.payload.team;
      const rules = match.rules,
        target = (
          rules.mode === "fixed"
            ? s.set_index === rules.fixed_sets - 1
            : s.set_index === rules.best_of - 1
        )
          ? rules.deciding_target
          : rules.regular_target;
      const hi = Math.max(current.a, current.b),
        diff = Math.abs(current.a - current.b);
      if (
        (hi >= target && diff >= rules.win_by) ||
        (rules.cap && hi >= rules.cap)
      ) {
        current.complete = true;
        current.winner = current.a > current.b ? "a" : "b";
      }
    } else if (a.type === "serve") s.serve = a.payload.team;
    else if (a.type === "timeout")
      s.timeouts[a.payload.team] = Math.max(0, s.timeouts[a.payload.team] - 1);
  }
  return s;
}
function statusText() {
  if (session?.generation < 0 && !fenced) return "Scoring control required";
  if (fenced) return "Access revoked";
  if (conflict) return "Conflict — review pending actions";
  if (!navigator.onLine) return queue.length ? "Offline · Pending" : "Offline";
  if (queue.length)
    return `Pending · ${queue.length} action${queue.length === 1 ? "" : "s"}`;
  return acknowledged ? "Saved" : "Connected";
}
function statusClass() {
  return fenced || conflict
    ? "conflict"
    : !navigator.onLine
      ? "offline"
      : queue.length
        ? "pending"
        : "saved";
}
function winnerReached(s) {
  const r = match.rules,
    a = s.sets.filter((v) => v.complete && v.winner === "a").length,
    b = s.sets.filter((v) => v.complete && v.winner === "b").length;
  return r.mode === "fixed"
    ? s.sets.length >= r.fixed_sets && s.sets.every((v) => v.complete)
    : Math.max(a, b) >= r.sets_to_win;
}
export async function start() {
  const secret = location.hash.slice(1);
  if (secret) history.replaceState(null, "", location.pathname);
  try {
    const me = await api("/auth/me", { publicRequest: true });
    setContext(me.csrf_token, "");
    const result = secret
      ? await api("/referee/exchange", {
          method: "POST",
          data: { token: secret },
          publicRequest: true,
        })
      : await api("/referee/state", { publicRequest: true });
    accept(result);
    loadQueue();
    if (queue.some((a) => a.generation !== session.generation)) conflict = true;
    connection =
      session.generation < 0 ? "Ready for explicit takeover" : "Connected";
    draw();
    setupLifecycle();
    if (
      session.generation === match.generation &&
      ["in_progress", "paused"].includes(match.status)
    )
      requestWake();
    if (!conflict && session.generation === match.generation && queue.length)
      flush();
  } catch (e) {
    app.innerHTML = `<main id="main" class="scorer-shell"><header class="scorer-header"><a class="brand" href="/"><img src="/static/brand.svg" alt="">Courtline</a>${badge("referee access")}</header><section class="scorer-ready"><h1>Game access unavailable</h1><div class="form-error">${errorText(e)}</div><p class="muted section-gap">Ask an organizer to assign a court and show this game's private QR code. Scan the fresh code to continue.</p><button class="button secondary" id="retry-access">Retry access</button></section>${credit()}</main>`;
    document.querySelector("#retry-access").onclick = () => location.reload();
  }
}
function draw() {
  if (!match) return;
  const focused = document.activeElement,
    focusId = focused?.id,
    focusPoint = focused?.dataset?.point,
    focusServe = focused?.dataset?.serve,
    focusTimeout = focused?.dataset?.timeout;
  const s = pendingState(),
    current = s.sets[s.set_index],
    owns = session.generation === match.generation && !fenced;
  const active = ["in_progress", "paused"].includes(match.status);
  const transitionPending = queue.some((action) =>
    ["start", "pause", "resume", "next_set", "end", "correct", "undo"].includes(
      action.type,
    ),
  );
  const ready = match.status === "ready";
  const ended = ["ended", "cancelled", "abandoned"].includes(match.status);
  app.innerHTML = `<main id="main" class="scorer-shell"><header class="scorer-header"><span class="brand"><img src="/static/brand.svg" alt="">Courtline <span class="eyebrow">REFEREE</span></span><button class="button secondary small" id="exit-scoring">Exit game</button></header><div class="scorer-subhead"><div><strong>${esc(courtName(match))}</strong><div class="muted small">${esc(match.organization_name || "Delegated game access")}</div><div class="muted small">${esc(match.tournament_name || "Tournament")} · ${esc(match.division_name || "Division")} · ${esc(match.stage_name || "Stage")}</div></div><div style="text-align:right"><span class="scorer-status ${statusClass()}" id="save-status" role="status" aria-live="polite"><i class="dot"></i>${esc(statusText())}</span><div class="scorer-timer" id="match-timer">${timerText()}</div></div></div>${!storageAvailable ? '<div class="banner danger">Offline storage unavailable. Keep this browser open and stay connected; pending entries cannot survive a browser restart.</div>' : ""}${conflict || fenced ? `<div class="banner danger">${fenced ? "Another device owns scoring control. Local pending actions have not been applied." : "Authoritative game state changed. Review pending actions before continuing."}<button class="button secondary" id="reconcile">Review</button></div>` : ""}${
    !owns && !ended
      ? `<section class="scorer-ready"><span class="eyebrow">CENTRAL GAME RECOVERY</span><h1>${esc(teamName(match, "a"))} vs ${esc(teamName(match, "b"))}</h1><p class="muted small">Recorded sets: ${esc(score(match))} · ${pretty(match.status)}</p><p class="muted small">Taking control revokes the previous phone's write access. A different phone cannot recover actions that were never sent from another device.</p><button class="button primary" id="takeover">Resume and take scoring control</button></section>`
      : ready
        ? `<section class="scorer-ready"><span class="eyebrow">PREGAME READINESS</span><h1>${esc(teamName(match, "a"))}<br><span class="muted">vs</span><br>${esc(teamName(match, "b"))}</h1><dl class="detail-list"><div><dt>Court</dt><dd>${esc(courtName(match))}</dd></div><div><dt>Competition rules</dt><dd>${ruleText()}</dd></div><div><dt>Connectivity</dt><dd>${navigator.onLine ? "Connected" : "Offline — reconnect before start"}</dd></div><div><dt>Sound readiness</dt><dd id="sound-state">${esc(soundState)}</dd></div></dl><button class="button secondary" id="test-whistle">Test whistle</button><p class="form-help">Check your phone or speaker volume at the venue. Browser audio respects device volume.</p><button class="button primary section-gap" id="start-game" ${conflict || queue.length || !navigator.onLine ? "disabled" : ""}>Start Game ${icon("arrow")}</button></section>`
        : ended
          ? `<section class="scorer-ready"><span class="tournament-symbol" style="margin:auto">${icon("check")}</span><h1>${match.status === "cancelled" ? "Game cancelled" : match.status === "abandoned" ? "Game abandoned" : "Game submitted"}</h1><p>${esc(teamName(match, "a"))} vs ${esc(teamName(match, "b"))}</p><h2>${esc(score(match))}</h2>${badge(match.result_status)}<p class="muted section-gap">The court has been released. ${match.result_status === "finalized" ? "Result is official." : match.result_status === "excluded" ? "This game is excluded from official standings." : "An authorized organizer will review the result."}</p></section>`
          : `<div class="set-line"><strong>Set ${s.set_index + 1}</strong><span>Sets won ${s.sets.filter((v) => v.complete && v.winner === "a").length}–${s.sets.filter((v) => v.complete && v.winner === "b").length}</span>${s.sets
              .filter((v) => v.complete)
              .map(
                (v, i) =>
                  `<span class="set-chip">Set ${i + 1} · ${v.a}–${v.b}</span>`,
              )
              .join(
                "",
              )}</div><div class="scoreboard">${(flipped ? ["b", "a"] : ["a", "b"]).map((side) => scoreSide(side, s, active && owns && !conflict && !transitionPending)).join("")}</div>${current.complete ? `<div class="banner section-gap">${icon("check")} Set ${s.set_index + 1} complete · ${esc(teamName(match, current.winner || "a"))}${!queue.length ? `<button class="button primary" id="${winnerReached(s) ? "end-game" : "next-set"}">${winnerReached(s) ? "Review & End Game" : "Confirm next set"}</button>` : " · Waiting for durable acknowledgment"}</div>` : ""}<div class="scorer-controls"><button class="button secondary" id="undo-action" ${queue.length || conflict || !owns ? "disabled" : ""}>${icon("refresh")}Undo</button><button class="button secondary" id="flip-sides">⇄ Switch sides</button><button class="button secondary" id="whistle">♬ Whistle</button><button class="button secondary" id="pause-game" ${queue.length || conflict || !owns ? "disabled" : ""}>${match.status === "paused" ? "▶ Resume" : "Ⅱ Pause"}</button><button class="button secondary" id="correct-score" ${queue.length || conflict || !owns ? "disabled" : ""}>Correct score</button><button class="button secondary" id="rules-drawer">Rules & history</button><button class="button secondary" id="wake-retry">${icon("sun")}Screen awake</button><button class="button ${winnerReached(s) ? "primary" : "secondary"}" id="end-game-secondary" ${queue.length || conflict || !owns ? "disabled" : ""}>End Game</button></div>`
  }<div class="scorer-bottom"><span id="wake-status">Screen awake: ${esc(wakeState)}</span><span id="connection-status">${esc(connection)} · Version ${match.version}</span></div>${credit()}</main>`;
  bind();
  const restore = focusId
    ? document.getElementById(focusId)
    : focusPoint
      ? document.querySelector('[data-point="' + focusPoint + '"]')
      : focusServe
        ? document.querySelector('[data-serve="' + focusServe + '"]')
        : focusTimeout
          ? document.querySelector('[data-timeout="' + focusTimeout + '"]')
          : null;
  if (restore && !restore.disabled) restore.focus({ preventScroll: true });
}
function scoreSide(side, s, allowed) {
  const current = s.sets[s.set_index];
  return `<section class="score-side" style="--team:${esc(match[`team_${side}`]?.color || (side === "a" ? "#247a57" : "#456db0"))};--team-ink:${contrastText(match[`team_${side}`]?.color || (side === "a" ? "#247a57" : "#456db0"))}"><h2>${esc(teamName(match, side))}</h2><div class="serving">${s.serve === side ? "● Serving" : "Receiving"}</div><div class="score-number" aria-label="${esc(teamName(match, side))} score ${current[side]}">${current[side]}</div><button class="score-plus" data-point="${side}" aria-label="Add one point for ${esc(teamName(match, side))}" ${!allowed || match.status !== "in_progress" || current.complete ? "disabled" : ""}>+1 <span>POINT</span></button><div class="score-side-actions"><button class="button secondary" data-serve="${side}" ${!allowed ? "disabled" : ""}>Set serve</button><button class="button secondary" data-timeout="${side}" aria-label="${esc(teamName(match, side))} timeout; ${s.timeouts[side]} remaining" ${!allowed || match.status !== "in_progress" || current.complete || s.timeouts[side] <= 0 ? "disabled" : ""}>Timeout · ${s.timeouts[side]}</button></div></section>`;
}
function timerText() {
  let seconds = match?.elapsed_seconds || 0;
  if (match?.state?.clock_anchor)
    seconds += Math.max(0, (performance.now() - receivedAt) / 1000);
  seconds = Math.floor(seconds);
  return `${String(Math.floor(seconds / 3600)).padStart(2, "0")}:${String(Math.floor(seconds / 60) % 60).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}
function ruleText() {
  const r = match.rules || {};
  return `${r.mode === "fixed" ? "Fixed " + r.fixed_sets : "Best of " + r.best_of} · Sets to ${r.regular_target}, deciding ${r.deciding_target} · Win by ${r.win_by}${r.cap ? " · Hard cap " + r.cap : ""}`;
}
function bind() {
  document
    .querySelectorAll("[data-point]")
    .forEach(
      (b) => (b.onclick = () => enqueue("point", { team: b.dataset.point })),
    );
  document
    .querySelectorAll("[data-serve]")
    .forEach(
      (b) => (b.onclick = () => enqueue("serve", { team: b.dataset.serve })),
    );
  document
    .querySelectorAll("[data-timeout]")
    .forEach(
      (b) =>
        (b.onclick = () => enqueue("timeout", { team: b.dataset.timeout })),
    );
  document.querySelector("#start-game")?.addEventListener("click", async () => {
    if (
      await confirm(
        "Start Game",
        `Confirm teams and court. ${ruleText()}. Starting freezes these rules and starts the match clock.`,
        "Start Game",
      )
    )
      enqueue("start");
  });
  document.querySelector("#takeover")?.addEventListener("click", takeover);
  document
    .querySelector("#test-whistle")
    ?.addEventListener("click", playWhistle);
  document.querySelector("#whistle")?.addEventListener("click", playWhistle);
  document.querySelector("#flip-sides")?.addEventListener("click", () => {
    flipped = !flipped;
    draw();
  });
  document
    .querySelector("#undo-action")
    ?.addEventListener("click", async () => {
      if (
        await confirm(
          "Undo last scoring action",
          "This records a compensation event and preserves action history.",
        )
      )
        enqueue("undo");
    });
  document
    .querySelector("#pause-game")
    ?.addEventListener("click", () =>
      enqueue(match.status === "paused" ? "resume" : "pause"),
    );
  document
    .querySelector("#correct-score")
    ?.addEventListener("click", correctScore);
  document.querySelector("#next-set")?.addEventListener("click", async () => {
    if (
      await confirm(
        "Start next set",
        `Confirm completed set ${match.state.set_index + 1}: ${score(match)}. The court remains occupied.`,
        "Start next set",
      )
    )
      enqueue("next_set");
  });
  document.querySelector("#end-game")?.addEventListener("click", endGame);
  document
    .querySelector("#end-game-secondary")
    ?.addEventListener("click", endGame);
  document.querySelector("#rules-drawer")?.addEventListener("click", () =>
    modal("Rules & recorded set history", rulesHistory(), null, {
      wide: true,
    }),
  );
  document.querySelector("#wake-retry")?.addEventListener("click", requestWake);
  document
    .querySelector("#exit-scoring")
    ?.addEventListener("click", async () => {
      if (queue.length) {
        toast("Review pending actions before exiting.", true);
        return reviewConflict();
      }
      if (
        await confirm(
          "Exit scoring screen",
          "Your game and court remain active. Re-scan the private QR to resume later.",
          "Exit game",
        )
      ) {
        try {
          await api("/referee/exit", {
            method: "POST",
            data: {},
            publicRequest: true,
          });
          releaseWake();
          clearInterval(pollTimer);
          clearInterval(clockTimer);
          clearInterval(renewTimer);
          location.href = "/referee";
        } catch (e) {
          toast(e.message, true);
        }
      }
    });
  document
    .querySelector("#reconcile")
    ?.addEventListener("click", reviewConflict);
}
function rulesHistory() {
  const r = match.rules,
    nameA = teamName(match, "a"),
    nameB = teamName(match, "b");
  const rankingLabels = {
    standings_points: "Standings points",
    won: "Matches won",
    head_to_head: "Head to head",
    set_quotient: "Set quotient",
    point_quotient: "Point quotient",
  };
  const policy = [
    {
      rule: "Set format",
      value:
        r.mode === "fixed"
          ? `${r.fixed_sets} fixed sets`
          : `Best of ${r.best_of} · first to ${r.sets_to_win} set wins`,
    },
    { rule: "Regular set target", value: `${r.regular_target} points` },
    { rule: "Deciding set target", value: `${r.deciding_target} points` },
    { rule: "Required winning lead", value: `${r.win_by} points` },
    {
      rule: "Hard score cap",
      value: r.cap ? `${r.cap} points · wins immediately at the cap` : "No cap",
    },
    { rule: "Timeout allowance", value: `${r.timeouts} per team per set` },
    {
      rule: "Draw policy",
      value: r.draw_allowed
        ? "Draws permitted after the fixed set count"
        : "A winner is required",
    },
    {
      rule: "Results approval",
      value: r.automatic_approval
        ? "Eligible submitted results finalize automatically"
        : "Organizer approval required",
    },
    {
      rule: "Standings awards",
      value: `Win ${r.win_points} · loss ${r.loss_points} · draw ${r.draw_points}`,
    },
    {
      rule: "Forfeit awards",
      value: `Winner ${r.forfeit_win_points} · loser ${r.forfeit_loss_points}`,
    },
    {
      rule: "Forfeit score quotients",
      value: r.include_forfeit_scores
        ? "Forfeit set and point totals are included"
        : "Forfeit set and point totals are excluded",
    },
    {
      rule: "Tie break order",
      value: (r.tie_breaks || [])
        .map((k) => rankingLabels[k] || pretty(k))
        .join(" → "),
    },
    {
      rule: "Head to head",
      value:
        r.head_to_head === "mini_table"
          ? "Mini table for tied teams"
          : "Two-team comparison",
    },
    {
      rule: "Restart after tie splits",
      value: r.restart_tie_breaks ? "Yes" : "No",
    },
    {
      rule: "Zero-denominator quotient",
      value: r.quotient_zero === "infinity" ? "Infinity" : "Zero",
    },
    {
      rule: "Cross-pool ranking",
      value:
        r.cross_pool === "points_per_match"
          ? "Points per match"
          : "Total points",
    },
  ];
  const sets = (match.state.sets || []).map((s, i) => ({
    set: i + 1,
    a: s.a,
    b: s.b,
    status: s.complete
      ? "Completed"
      : i === match.state.set_index
        ? "Current set"
        : "Recorded",
    winner: s.complete
      ? teamName(match, s.winner || (s.a > s.b ? "a" : "b"))
      : "—",
  }));
  return `<h3>${esc(ruleText())}</h3><p class="muted small">${match.rule_snapshot ? "The effective rules froze at Start Game." : "These are the effective rules before Start Game."}</p>${table(
    [
      { key: "rule", label: "Rule" },
      { key: "value", label: "Policy" },
    ],
    policy,
  )}<h3 class="section-gap">Recorded sets</h3>${table(
    [
      { key: "set", label: "Set" },
      { key: "a", label: nameA },
      { key: "b", label: nameB },
      { key: "status", label: "Status" },
      { key: "winner", label: "Set winner" },
    ],
    sets,
  )}<p>Serving: ${esc(teamName(match, match.state.serve))}</p><p>Timeouts remaining: ${esc(nameA)} ${match.state.timeouts.a} · ${esc(nameB)} ${match.state.timeouts.b}</p><p class="muted small">This set record includes accepted server actions. Saved points survive a browser or application restart. Pending points remain in this device’s local queue until acknowledged.</p>`;
}
function enqueue(type, payload = {}) {
  if (conflict || fenced || session.generation !== match.generation)
    return toast("Review scoring control before continuing.", true);
  const action = {
    client_action_id: crypto.randomUUID(),
    expected_version: match.version + queue.length,
    generation: session.generation,
    type,
    payload,
  };
  queue.push(action);
  storeQueue();
  acknowledged = false;
  draw();
  flush();
}
async function flush() {
  if (busy || conflict || fenced || !navigator.onLine || !queue.length) return;
  busy = true;
  while (queue.length && !conflict && !fenced) {
    const action = queue[0];
    try {
      const result = await api("/referee/action", {
        method: "POST",
        data: action,
        publicRequest: true,
      });
      if (
        !result.saved ||
        result.receipt?.client_action_id !== action.client_action_id
      )
        throw new Error("The server did not acknowledge this action.");
      accept(result);
      queue.shift();
      storeQueue();
      acknowledged = true;
      connection = "Connected";
      if (["start", "resume"].includes(action.type)) requestWake();
      if (action.type === "end") releaseWake();
      draw();
    } catch (e) {
      acknowledged = false;
      if (e.status === 409) {
        conflict = true;
        try {
          accept(await api("/referee/state", { publicRequest: true }));
        } catch {}
        connection = "Conflict";
        draw();
        toast(e.message, true);
      } else if ([401, 403].includes(e.status)) {
        fenced = true;
        connection = "Access revoked";
        releaseWake();
        draw();
        toast(e.message, true);
      } else if (e.status && e.status < 500) {
        conflict = true;
        connection = "Action rejected";
        draw();
        toast(e.message, true);
      } else {
        connection = "Reconnecting";
        draw();
        break;
      }
    }
  }
  busy = false;
}
async function takeover() {
  if (
    !(await confirm(
      "Take scoring control",
      "This atomically revokes the previous scorer. Central scores will be restored; unsent actions from another phone are not recoverable here.",
      "Resume and take scoring control",
    ))
  )
    return;
  try {
    const result = await api("/referee/resume", {
      method: "POST",
      data: { takeover: true },
      publicRequest: true,
    });
    accept(result);
    fenced = false;
    conflict = queue.length > 0;
    acknowledged = false;
    connection = "Connected";
    draw();
    if (["in_progress", "paused"].includes(match.status)) requestWake();
    if (conflict) reviewConflict();
  } catch (e) {
    toast(e.message, true);
  }
}
async function reviewConflict() {
  try {
    if (!fenced) accept(await api("/referee/state", { publicRequest: true }));
  } catch {}
  modal(
    "Reconcile local pending actions",
    `<p><strong>Server version ${match.version}</strong> · ${esc(score(match))} · ${esc(pretty(match.status))}</p><p class="muted small">These local actions are not confirmed Saved. Retry retains their unique identifiers. Conflicting actions are never silently applied over current state.</p><div class="pending-list">${queue.map((a) => `<p>${esc(pretty(a.type))} ${a.payload.team ? esc(teamName(match, a.payload.team)) : ""} · Expected version ${a.expected_version}</p>`).join("") || "No local actions."}</div><p class="muted small">Discarding removes only this browser's unsent queue. If the real score needs repair, use a reasoned score correction after control is restored.</p>${fenced ? '<p class="form-error">Access revoked. Ask the organizer for a fresh QR and confirm takeover.</p>' : '<button type="button" class="button secondary section-gap" id="retry-pending">Retry same action IDs</button>'}`,
    async () => {
      queue = [];
      storeQueue();
      conflict = false;
      acknowledged = false;
      draw();
    },
    { submit: "Discard local queue" },
  );
  document.querySelector("#retry-pending")?.addEventListener("click", () => {
    document.querySelector("#dialog").close();
    conflict = false;
    flush();
  });
}
function correctScore() {
  const s = match.state.sets[match.state.set_index];
  modal(
    "Correct current set score",
    `<p class="muted small">Before: ${esc(teamName(match, "a"))} ${s.a} – ${s.b} ${esc(teamName(match, "b"))}</p>${field("a", teamName(match, "a"), "number", s.a, 'min="0" max="1000" required')}${field("b", teamName(match, "b"), "number", s.b, 'min="0" max="1000" required')}${textarea("reason", "Required correction reason", "", "required")}`,
    async (fd) =>
      enqueue("correct", {
        a: Number(fd.get("a")),
        b: Number(fd.get("b")),
        reason: fd.get("reason"),
      }),
    { submit: "Record correction" },
  );
}
function endGame() {
  const s = match.state;
  if (!winnerReached(s))
    return toast(
      "Complete the match winning condition, or ask an organizer to record an exceptional outcome.",
      true,
    );
  modal(
    "Review & End Game",
    `<h3>${esc(teamName(match, "a"))} vs ${esc(teamName(match, "b"))}</h3><div class="notice-list">${s.sets.map((v, i) => `<div class="notice">Set ${i + 1}: <strong>${v.a} – ${v.b}</strong> · ${v.winner ? esc(teamName(match, v.winner)) : "Draw"}</div>`).join("")}</div><p class="muted section-gap">End Game stops timing, releases the court, and submits the result for organizer approval. Submission succeeds only after the server acknowledges it.</p>`,
    async () => enqueue("end"),
    { submit: "End Game & submit result" },
  );
}
async function playWhistle() {
  try {
    whistle.pause();
    whistle.currentTime = 0;
    await whistle.play();
    soundState = "Whistle ready";
    toast("Whistle played. Check venue volume.");
  } catch {
    soundState = "Sound blocked — check device settings";
    toast(
      "Whistle could not play. Check device volume and browser sound settings.",
      true,
    );
  }
  if (document.querySelector("#sound-state"))
    document.querySelector("#sound-state").textContent = soundState;
}
async function requestWake() {
  if (
    !["in_progress", "paused"].includes(match?.status) ||
    document.hidden ||
    fenced
  )
    return;
  if (!("wakeLock" in navigator)) {
    wakeState = "Unavailable · adjust device auto-lock";
    updateWake();
    return;
  }
  wakeWanted = true;
  try {
    if (wakeRequestInFlight || (wakeSentinel && !wakeSentinel.released)) return;
    wakeRequestInFlight = true;
    wakeSentinel = await navigator.wakeLock.request("screen");
    if (
      !wakeWanted ||
      document.hidden ||
      fenced ||
      !["in_progress", "paused"].includes(match.status)
    ) {
      releaseWake();
      return;
    }
    wakeState = "Screen awake";
    wakeSentinel.addEventListener("release", () => {
      wakeState = "Interrupted · keep page visible";
      wakeSentinel = null;
      updateWake();
      if (wakeWanted && !document.hidden && !fenced) requestWake();
    });
  } catch {
    wakeState = "Denied · check power / auto-lock settings";
  } finally {
    wakeRequestInFlight = false;
  }
  updateWake();
}
function updateWake() {
  const n = document.querySelector("#wake-status");
  if (n) n.textContent = wakeState;
}
function releaseWake() {
  wakeWanted = false;
  wakeSentinel?.release().catch(() => {});
  wakeSentinel = null;
  wakeState = "Released";
  updateWake();
}
function setupLifecycle() {
  clockTimer = setInterval(() => {
    const n = document.querySelector("#match-timer");
    if (n) n.textContent = timerText();
  }, 500);
  pollTimer = setInterval(async () => {
    if (document.hidden || busy || conflict || fenced) return;
    if (queue.length) {
      flush();
      return;
    }
    try {
      const result = await api("/referee/state", { publicRequest: true });
      const old = match.version;
      accept(result);
      connection =
        session.generation < 0 ? "Ready for explicit takeover" : "Connected";
      if (session.generation >= 0 && session.generation !== match.generation) {
        fenced = true;
        releaseWake();
        draw();
      } else if (match.version !== old) draw();
    } catch (e) {
      connection =
        e.status === 401 || e.status === 403
          ? "Access revoked"
          : "Reconnecting";
      if ([401, 403].includes(e.status)) {
        fenced = true;
        releaseWake();
        draw();
      } else {
        const n = document.querySelector("#connection-status");
        if (n) n.textContent = connection;
      }
    }
  }, 2000);
  renewTimer = setInterval(
    () => {
      if (["in_progress", "paused", "ready"].includes(match.status) && !fenced)
        api("/referee/renew", {
          method: "POST",
          data: {},
          publicRequest: true,
        }).catch(() => {});
    },
    15 * 60 * 1000,
  );
  window.addEventListener("online", () => {
    connection = "Reconnecting";
    draw();
    flush();
  });
  window.addEventListener("offline", () => {
    connection = "Offline";
    draw();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      releaseWake();
      whistle.pause();
    } else {
      requestWake();
      flush();
    }
  });
  window.addEventListener("pagehide", releaseWake);
  if (
    ["in_progress", "paused"].includes(match.status) &&
    session.generation === match.generation
  )
    requestWake();
}
