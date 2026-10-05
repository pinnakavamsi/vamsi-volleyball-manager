import {
  api,
  esc,
  items,
  pretty,
  dt,
  date,
  badge,
  empty,
  icon,
  credit,
  field,
  select,
  teamName,
  courtName,
  score,
  matchCard,
  table,
  download,
  toast,
  imageTag,
  localDateTime,
  contrastText,
} from "./common.js";
import {
  standingsTable,
  renderWeather,
  normalizeDisplayWidgets,
  displayWidgetSize,
} from "./app.js";
let data,
  tab = "overview",
  filters = {},
  weather,
  lastUpdate = 0,
  stale = false,
  rotationIndex = 0,
  weatherUnits = "celsius",
  refreshBusy = false,
  publicReportOptions = "",
  rotationTimer,
  rotationSeconds;
const slug = document.body.dataset.slug,
  isDisplay = document.body.dataset.experience === "display";
const endpoint = `/public/${encodeURIComponent(slug)}`;
export async function start() {
  try {
    await refresh(true);
    if (!data) return;
    setInterval(() => {
      if (!document.hidden) refresh(false);
    }, 1000);
    matchMedia("(prefers-reduced-motion: reduce)").addEventListener(
      "change",
      configureRotation,
    );
  } catch (e) {
    unavailable(e);
  }
}
function unavailable(e) {
  data = null;
  clearInterval(rotationTimer);
  rotationTimer = null;
  document.querySelector("#app").innerHTML =
    `<main id="main" class="initial-load"><img src="/static/brand.svg" width="52" height="52" alt="Courtline"><h1>Tournament unavailable</h1><p>${esc(e.message || "This tournament is private or unavailable.")}</p><p class="muted">Ask the organizer for current public tournament information.</p>${credit()}</main>`;
}
async function refresh(first) {
  if (refreshBusy) return;
  refreshBusy = true;
  try {
    let fullRefresh = first || !data;
    if (first || !data) data = await api(endpoint, { publicRequest: true });
    else {
      const live = await api(endpoint + "/live", { publicRequest: true });
      const previousIds = new Set(items(data.matches).map((match) => match.id));
      const liveMatches = items(live.matches);
      const changedIds =
        previousIds.size !== liveMatches.length ||
        liveMatches.some((match) => !previousIds.has(match.id));
      if (
        live.full_refresh ||
        changedIds ||
        live.configuration_version !==
          (data.configuration_version || data.tournament.updated_at) ||
        live.standings_generation !== data.tournament.standings_generation
      ) {
        data = await api(endpoint, { publicRequest: true });
        fullRefresh = true;
      } else {
        const previous = new Map(
          items(data.matches).map((match) => [match.id, match]),
        );
        data.matches = liveMatches.map((match) => ({
          ...previous.get(match.id),
          ...match,
        }));
        if (live.courts) {
          const courts = new Map(
            items(data.courts).map((court) => [court.id, court]),
          );
          data.courts = items(live.courts).map((court) => ({
            ...courts.get(court.id),
            ...court,
          }));
        }
        data.updated_at = live.updated_at;
      }
    }
    lastUpdate = Date.now();
    stale = false;
    if (isDisplay) {
      drawDisplay();
      configureRotation();
    } else if (fullRefresh) {
      if (first) {
        try {
          filters.team =
            localStorage.getItem(`courtline-public-favorite:${slug}`) || "";
        } catch {}
      }
      drawPublic();
    } else updatePublic();
    if (first)
      api(
        endpoint +
          "/weather?units=" +
          (isDisplay ? data.display?.units || "celsius" : weatherUnits),
        { publicRequest: true },
      )
        .then((w) => {
          weather = w;
          if (tab === "venues") drawPublic();
          if (isDisplay) drawDisplay();
        })
        .catch(() => {
          weather = { status: "unavailable", message: "Weather unavailable." };
        });
  } catch (e) {
    if ([403, 404, 401].includes(e.status)) {
      unavailable(e);
      return;
    }
    stale = true;
    if (first) throw e;
    const n = document.querySelector("#freshness");
    if (n) n.textContent = "Offline · Scores may be stale";
  } finally {
    refreshBusy = false;
  }
}
function configureRotation() {
  if (!isDisplay || !data) return;
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
    clearInterval(rotationTimer);
    rotationTimer = null;
    return;
  }
  const seconds = Math.max(
    5,
    Math.min(300, Number(data.display?.rotation_seconds) || 20),
  );
  if (rotationTimer && seconds === rotationSeconds) return;
  clearInterval(rotationTimer);
  rotationSeconds = seconds;
  rotationTimer = setInterval(() => {
    if (!document.hidden && data) {
      rotationIndex++;
      drawDisplay();
    }
  }, seconds * 1000);
}
function publicFilters() {
  return `<form class="filters" id="public-filters">${select("team", "Your team", [["", "All teams"], ...items(data.entries).map((t) => [t.id, t.name])], filters.team || "")}${select("division", "Division", [["", "All divisions"], ...items(data.divisions).map((d) => [d.id, d.name])], filters.division || "")}${select("stage", "Stage", [["", "All stages"], ...items(data.stages).map((s) => [s.id, s.name])], filters.stage || "")}${field("day", "Day", "date", filters.day || "")}${select("venue", "Venue", [["", "All venues"], ...items(data.venues).map((c) => [c.id, c.name])], filters.venue || "")}${select("court", "Court", [["", "All courts"], ...items(data.courts).map((c) => [c.id, c.name])], filters.court || "")}</form>`;
}
function matches() {
  return items(data.matches).filter(
    (m) =>
      (!filters.team ||
        m.team_a?.id === filters.team ||
        m.team_b?.id === filters.team) &&
      (!filters.division || m.division_id === filters.division) &&
      (!filters.stage || m.stage_id === filters.stage) &&
      (!filters.day ||
        localDateTime(m.planned_at, data.tournament.timezone).slice(0, 10) ===
          filters.day) &&
      (!filters.court || m.court?.id === filters.court) &&
      (!filters.venue || m.court?.venue_id === filters.venue),
  );
}
function drawPublic() {
  if (!data) return;
  const t = data.tournament,
    ms = matches();
  document.documentElement.style.setProperty(
    "--org-brand",
    /^#[0-9a-f]{6}$/i.test(data.organization?.color || "")
      ? data.organization.color
      : "#216b50",
  );
  document.querySelector("#app").innerHTML =
    `<div class="public-shell"><header class="public-header"><a href="/t/${esc(t.slug)}" class="brand"><img src="/static/brand.svg" alt="">Courtline</a><span class="muted small" id="freshness">${stale ? "Offline · Scores may be stale" : "Live updates · Every 1 second"}</span></header><main id="main"><section class="public-hero"><span class="eyebrow">${imageTag(data.organization?.logo, data.organization?.name + " logo")}${esc(data.organization?.name || "VOLLEYBALL TOURNAMENT")}</span><h1>${esc(t.name)}</h1><p>${esc(t.description)}</p><div class="public-hero-details"><span>${icon("calendar")} ${esc(date(t.start_date))} – ${esc(date(t.end_date))}</span><span>${icon("location")} ${esc(
      items(data.venues)
        .map((v) => v.name)
        .join(" · ") || "Venue to be confirmed",
    )}</span><span>${esc(t.timezone)}</span>${badge(t.status)}${data.demo ? badge("demo event") : ""}</div></section><nav class="tabs" aria-label="Public tournament sections">${["overview", "schedule", "live", "results", "standings", "brackets", "venues", ...(data.display?.public_reports?.length ? ["reports"] : [])].map((x) => `<button type="button" class="tab ${tab === x ? "active" : ""}" data-public-tab="${x}">${pretty(x)}</button>`).join("")}</nav>${publicFilters()}<div id="public-content" class="public-content">${publicContent(ms)}</div></main>${credit()}</div>`;
  document.querySelectorAll("[data-public-tab]").forEach(
    (b) =>
      (b.onclick = () => {
        tab = b.dataset.publicTab;
        drawPublic();
      }),
  );
  document.querySelector("#public-filters").onchange = (e) => {
    filters = Object.fromEntries(new FormData(e.currentTarget));
    try {
      localStorage.setItem(
        `courtline-public-favorite:${slug}`,
        filters.team || "",
      );
    } catch {}
    updatePublic();
  };
}
function updatePublic() {
  if (!data) return;
  const node = document.querySelector("#public-content");
  if (
    node &&
    (tab !== "reports" ||
      publicReportOptions !==
        JSON.stringify(data.display?.public_reports || []))
  ) {
    const focused = document.activeElement;
    const focusId = node.contains(focused) ? focused.id : "";
    const scrollPositions = [...node.querySelectorAll(".table-scroll")].map(
      (table) => ({ left: table.scrollLeft, top: table.scrollTop }),
    );
    node.innerHTML = publicContent(matches());
    bindPublicContent();
    node.querySelectorAll(".table-scroll").forEach((table, index) => {
      if (scrollPositions[index]) {
        table.scrollLeft = scrollPositions[index].left;
        table.scrollTop = scrollPositions[index].top;
      }
    });
    if (focusId)
      document.getElementById(focusId)?.focus({ preventScroll: true });
  }
  const n = document.querySelector("#freshness");
  if (n)
    n.textContent = stale
      ? "Offline · Scores may be stale"
      : "Live updates · Every 1 second";
}
function publicContent(ms) {
  if (tab === "reports") return publicReports();
  if (tab === "schedule")
    return `<section class="panel">${publicFixtureTable(ms)}</section>`;
  if (tab === "live")
    return `<div class="public-match-grid">${
      ms
        .filter((m) => ["in_progress", "paused"].includes(m.status))
        .map((m) =>
          matchCard(m, {
            publicView: true,
            timezone: data.tournament.timezone,
          }),
        )
        .join("") ||
      empty(
        "No live games",
        "A started game will appear with its latest committed score.",
      )
    }</div>`;
  if (tab === "results")
    return `<section class="panel">${table(
      [
        {
          label: "Teams",
          render: (m) => `${esc(teamName(m, "a"))} vs ${esc(teamName(m, "b"))}`,
        },
        { label: "Sets", render: (m) => esc(score(m)) },
        {
          label: "Result",
          render: (m) =>
            badge(m.result_status === "finalized" ? "official" : "provisional"),
        },
        { label: "Outcome", render: (m) => esc(pretty(m.outcome || "normal")) },
      ],
      ms.filter((m) => ["ended", "abandoned", "cancelled"].includes(m.status)),
    )}</section>`;
  if (tab === "standings") return officialStandings();
  if (tab === "brackets") return brackets();
  if (tab === "venues") return venues();
  const live = ms.filter((m) => ["in_progress", "paused"].includes(m.status)),
    upcoming = ms
      .filter(
        (m) =>
          ["scheduled", "ready"].includes(m.status) &&
          (!m.planned_at || Date.parse(m.planned_at) >= Date.now()),
      )
      .sort((a, b) =>
        String(a.planned_at || "9999").localeCompare(
          String(b.planned_at || "9999"),
        ),
      );
  return `${data.demo ? '<div class="banner demo">This is an isolated sample competition. Scores and charts use its persisted demo records.</div>' : ""}<div class="metric-grid">${[
    [
      "Teams",
      items(data.entries).filter(
        (e) =>
          (!filters.team || e.id === filters.team) &&
          (!filters.division || e.division_id === filters.division),
      ).length,
    ],
    [
      "Divisions",
      filters.division
        ? 1
        : filters.team
          ? new Set(
              items(data.entries)
                .filter((e) => e.id === filters.team)
                .map((e) => e.division_id),
            ).size
          : items(data.divisions).length,
    ],
    ["Live games", live.length],
    ["Completed", ms.filter((m) => m.status === "ended").length],
  ]
    .map(
      ([label, v]) =>
        `<div class="metric"><span class="muted small">${label}</span><strong class="metric-value">${v}</strong><span class="metric-foot">${filters.team || filters.division ? "Current filter" : "Tournament records"}</span></div>`,
    )
    .join(
      "",
    )}</div><div class="dashboard-grid"><div class="stack"><section class="panel"><header class="panel-heading"><div><h2>Live games</h2><p>Committed scores from the court</p></div></header><div class="live-cards">${
    live
      .slice(0, 4)
      .map((m) =>
        matchCard(m, { publicView: true, timezone: data.tournament.timezone }),
      )
      .join("") || empty("No games in progress")
  }</div></section><section class="panel"><header class="panel-heading"><h2>Upcoming fixtures</h2></header>${publicFixtureTable(upcoming.slice(0, 6))}</section></div><div class="stack"><section class="panel panel-pad"><h2>Event announcements</h2>${
    items(data.announcements)
      .map(
        (a) =>
          `<div class="notice"><strong>${esc(a.title)}</strong><p>${esc(a.content || a.body)}</p><small>${esc(dt(a.publish_at))}</small></div>`,
      )
      .join("") || '<p class="muted small">No announcements published.</p>'
  }</section><section class="panel panel-pad"><h2>Competition format</h2><p class="muted small">${
    items(data.stages)
      .map((s) => `${esc(s.name)} · ${esc(pretty(s.format))}`)
      .join("<br>") || "Stages to be confirmed."
  }</p>${data.tournament.rules ? `<h3>Match rules</h3><p class="muted small">${ruleText(data.tournament.rules)}</p>` : ""}</section></div></div>`;
}
function ruleText(r) {
  return `${r.mode === "fixed" ? "Fixed " + r.fixed_sets : "Best of " + r.best_of} sets · Regular ${r.regular_target} · Deciding ${r.deciding_target} · Winning lead ${r.win_by} · ${r.cap ? "Hard cap " + r.cap : "No point cap"}`;
}
function publicFixtureTable(ms) {
  ms = [...ms].sort((a, b) =>
    String(a.planned_at || "9999").localeCompare(
      String(b.planned_at || "9999"),
    ),
  );
  return table(
    [
      {
        label: "Planned time",
        render: (m) =>
          esc(dt(m.planned_at, { timeZone: data.tournament.timezone })),
      },
      {
        label: "Game",
        render: (m) =>
          `<strong>${esc(teamName(m, "a"))}</strong> vs <strong>${esc(teamName(m, "b"))}</strong>`,
      },
      {
        label: "Division / stage",
        render: (m) => `${esc(m.division_name)} · ${esc(m.stage_name)}`,
      },
      { label: "Court", render: (m) => esc(courtName(m)) },
      { label: "Status", render: (m) => badge(m.status) },
      { label: "Sets", render: (m) => esc(score(m)) },
    ],
    ms,
  );
}
function officialStandings() {
  return `<div class="banner">${icon("check")} Official standings · Finalized results only · Generation ${data.tournament.standings_generation || 0}</div>${
    items(data.standings)
      .filter((g) => !filters.division || g.division_id === filters.division)
      .map(
        (g) =>
          `<section class="panel section-gap"><header class="panel-heading"><h2>${esc(g.division_name || items(data.divisions).find((d) => d.id === g.division_id)?.name || "Standings")}</h2></header>${standingsTable(g.rows || g.standings || g.table || [])}</section>`,
      )
      .join("") || empty("Official standings not yet available")
  }`;
}
function brackets() {
  const stages = items(data.stages).filter(
    (s) =>
      s.format === "single_elimination" &&
      (!filters.division || s.division_id === filters.division),
  );
  return (
    stages
      .map((s) => {
        const ms = matches().filter((m) => m.stage_id === s.id),
          rounds = [...new Set(ms.map((m) => m.round_number))].sort(
            (a, b) => a - b,
          );
        return `<section class="panel section-gap"><header class="panel-heading"><h2>${esc(s.name)}</h2></header><div class="bracket">${
          rounds
            .map(
              (r) =>
                `<div class="bracket-round"><span class="eyebrow">Round ${r}</span>${ms
                  .filter((m) => m.round_number === r)
                  .map(
                    (m) =>
                      `<article class="bracket-match"><p><strong>${esc(teamName(m, "a"))}</strong><span>${m.winner_id === m.team_a?.id ? "Winner" : ""}</span></p><p><strong>${esc(teamName(m, "b"))}</strong><span>${m.winner_id === m.team_b?.id ? "Winner" : ""}</span></p><p class="muted small">${esc(score(m))} · ${esc(pretty(m.status))}</p></article>`,
                  )
                  .join("")}</div>`,
            )
            .join("") || empty("Bracket fixtures not generated")
        }</div></section>`;
      })
      .join("") ||
    empty(
      "No knockout brackets",
      "This competition currently uses other stage formats.",
    )
  );
}
function venues() {
  return `<div class="record-grid">${items(data.venues)
    .map(
      (v) =>
        `<section class="panel record-card"><div class="record-top">${icon("location")}${badge(v.indoor ? "indoor" : "outdoor")}</div><h2>${esc(v.name)}</h2><p>${esc(v.address || "Address to be confirmed")}</p><p class="muted small">${esc(v.timezone)}</p><p>${items(
          data.courts,
        )
          .filter((c) => c.venue_id === v.id)
          .map((c) => esc(c.name))
          .join(
            " · ",
          )}</p>${v.latitude != null && v.longitude != null ? `<a class="button secondary" href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(v.latitude + "," + v.longitude)}" target="_blank" rel="noopener">Open map location ${icon("arrow")}</a>` : '<p class="muted small">Verified map position not available.</p>'}</section>`,
    )
    .join(
      "",
    )}</div><section class="panel panel-pad section-gap"><h2>Venue weather</h2><label class="field" style="max-width:150px;margin-bottom:15px"><span>Temperature units</span><select id="weather-units"><option value="celsius" ${weatherUnits === "celsius" ? "selected" : ""}>Celsius</option><option value="fahrenheit" ${weatherUnits === "fahrenheit" ? "selected" : ""}>Fahrenheit</option></select></label>${weather ? renderWeather(weather) : '<p class="muted small">Checking available event-day forecasts…</p>'}</section>`;
}
function displayWidgetPages(allowed, sizes, pageSize) {
  const pages = [];
  let normal = [];
  for (const widget of allowed) {
    if (displayWidgetSize(sizes, widget) === "wide") {
      if (normal.length) pages.push(normal);
      normal = [];
      pages.push([widget]);
    } else {
      normal.push(widget);
      if (normal.length === pageSize) {
        pages.push(normal);
        normal = [];
      }
    }
  }
  if (normal.length) pages.push(normal);
  return pages;
}
function drawDisplay() {
  if (!data) return;
  const config = data.display || {};
  filters = {
    division: config.division_id || "",
    court: config.court_id || "",
  };
  const allowed = normalizeDisplayWidgets(config.widgets, [
    "live",
    "standings",
    "upcoming",
    "results",
  ]);
  const pageSize = matchMedia("(max-width: 800px)").matches ? 1 : 2,
    widgetPages = displayWidgetPages(allowed, config.sizes, pageSize),
    widgetPageCount = Math.max(1, widgetPages.length),
    currentWidgets = widgetPages[rotationIndex % widgetPageCount] || [],
    standingsGroups = items(data.standings).filter(
      (g) => !config.division_id || g.division_id === config.division_id,
    ),
    standingsIndex =
      Math.floor(rotationIndex / widgetPageCount) %
      Math.max(1, standingsGroups.length);
  const ms = items(data.matches).filter(
    (m) =>
      (!config.division_id || m.division_id === config.division_id) &&
      (!config.court_id || m.court?.id === config.court_id),
  );
  const widgets = {
    live: `<section class="panel"><header class="panel-heading"><h2>Live on court</h2><span class="display-live">● ${ms.filter((m) => ["in_progress", "paused"].includes(m.status)).length} live</span></header><div class="live-cards">${
      ms
        .filter((m) => ["in_progress", "paused"].includes(m.status))
        .map((m) =>
          matchCard(m, {
            publicView: true,
            timezone: data.tournament.timezone,
          }),
        )
        .join("") || empty("No live games")
    }</div></section>`,
    standings: `<section class="panel"><header class="panel-heading"><h2>Official standings</h2></header>${
      standingsGroups
        .slice(standingsIndex, standingsIndex + 1)
        .map((g) => {
          const rs = g.rows || g.table || g.standings || [],
            divisionName =
              g.division_name ||
              items(data.divisions).find((d) => d.id === g.division_id)?.name ||
              g.name ||
              "Division standings";
          return `<section class="display-standings-group"><header class="display-standings-heading"><h3>${esc(divisionName)}</h3><p>Division ${standingsIndex + 1} of ${standingsGroups.length} · Finalized results only</p></header>${table(
            [
              {
                label: "Team",
                render: (r) => esc(r.name || r.team_name || r.entry_name),
              },
              { label: "P", render: (r) => esc(r.played ?? 0) },
              { label: "W", render: (r) => esc(r.won ?? r.wins ?? 0) },
              {
                label: "Pts",
                render: (r) =>
                  `<strong>${esc(r.standings_points ?? r.points ?? 0)}</strong>`,
              },
            ],
            rs.slice(0, 6),
          )}${rs.length > 6 ? `<p class="display-standings-limit">Showing top 6 of ${rs.length} teams</p>` : ""}</section>`;
        })
        .join("") || empty("No official standings yet")
    }</section>`,
    upcoming: `<section class="panel"><header class="panel-heading"><h2>Up next</h2></header>${publicFixtureTable(ms.filter((m) => ["scheduled", "ready"].includes(m.status)).slice(0, 5))}</section>`,
    results: `<section class="panel"><header class="panel-heading"><h2>Latest results</h2></header>${table(
      [
        {
          label: "Game",
          render: (m) => `${esc(teamName(m, "a"))} vs ${esc(teamName(m, "b"))}`,
        },
        { label: "Sets", render: (m) => esc(score(m)) },
        {
          label: "Result",
          render: (m) =>
            badge(m.result_status === "finalized" ? "official" : "provisional"),
        },
      ],
      ms
        .filter((m) => m.status === "ended")
        .slice(-5)
        .reverse(),
    )}</section>`,
    announcements: `<section class="panel panel-pad"><h2>Announcements</h2>${
      items(data.announcements)
        .map(
          (a) => `<h3>${esc(a.title)}</h3><p>${esc(a.content || a.body)}</p>`,
        )
        .join("") || "<p>No current announcements.</p>"
    }</section>`,
    weather: `<section class="panel panel-pad"><h2>Venue weather</h2>${weather ? renderWeather(weather) : "<p>Forecast unavailable.</p>"}</section>`,
    sponsors: `<section class="panel panel-pad"><h2>${esc(config.sponsor_name || "Event sponsor")}</h2>${imageTag(config.sponsor_image, config.sponsor_name || "Sponsor", "sponsor-logo")}${/^https:\/\//i.test(config.sponsor_url || "") ? `<p><a href="${esc(config.sponsor_url)}" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">Visit ${esc(config.sponsor_name || "sponsor")} website</a></p>` : ""}${!config.sponsor_image && !config.sponsor_name ? "<p>No sponsor information configured.</p>" : ""}</section>`,
    brackets: brackets(),
  };
  document.querySelector("#app").innerHTML =
    `<main id="main" class="display-shell dedicated-display" style="${/^#[0-9a-f]{6}$/i.test(config.background || "") ? "background:" + config.background + ";" : ""}--display-ink:${contrastText(config.background || "#0e281e")};--display-scale:${Math.max(0.8, Math.min(2, Number(config.text_scale) || 1))};font-size:${Math.max(0.8, Math.min(2, Number(config.text_scale) || 1))}em"><header class="display-header"><div><span class="eyebrow">${imageTag(config.logo || data.organization?.logo, data.organization?.name + " logo")}${esc(data.organization?.name || "COURTLINE")} ${data.demo ? "· DEMO COMPETITION" : ""}</span><h1>${esc(data.tournament.name)}</h1><p>${esc(date(data.tournament.start_date))} · ${esc(
      items(data.venues)
        .map((v) => v.name)
        .join(" · "),
    )}</p></div><div style="text-align:right"><div class="display-live" id="freshness">${stale ? "Offline · Scores may be stale" : "● Live scores"}</div><p class="small">${esc(new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", timeZone: data.tournament.timezone, timeZoneName: "short" }).format(new Date()))}</p></div></header><div class="display-grid">${
      currentWidgets
        .map((w) =>
          widgets[w].replace(
            'class="panel',
            'class="panel ' +
              (displayWidgetSize(config.sizes, w) === "wide"
                ? "wide-widget"
                : ""),
          ),
        )
        .join("") ||
      `<section class="panel panel-pad">${empty("No display content selected")}</section>`
    }</div>${credit()}</main>`;
}

function publicReports() {
  const reports = data.display?.public_reports || [];
  publicReportOptions = JSON.stringify(reports);
  if (!reports.length)
    return '<section class="panel panel-pad"><h2>Public reports unavailable</h2><p class="muted">The organizer has not enabled any public report downloads.</p></section>';
  return `<section class="panel panel-pad"><h2>Public tournament reports</h2><p class="muted small">Download organizer-enabled public information. Current team, division, date, venue, and court filters apply.</p><div class="filters">${select("type", "Report", reports, "", 'id="public-report-type"')}${["csv", "xlsx", "pdf"].map((f) => `<button class="button secondary" data-public-export="${f}">Download ${f.toUpperCase()}</button>`).join("")}</div></section>`;
}
function bindPublicContent() {
  document.querySelectorAll("[data-public-export]").forEach(
    (b) =>
      (b.onclick = () => {
        const params = {
          type: document.querySelector("#public-report-type").value,
          format: b.dataset.publicExport,
        };
        for (const [k, v] of Object.entries(filters)) {
          if (!v) continue;
          const name = {
            team: "team_id",
            division: "division_id",
            stage: "stage_id",
            venue: "venue_id",
            court: "court_id",
            day: "from",
          }[k];
          params[name] = v;
          if (k === "day") params.to = v;
        }
        download(
          `${endpoint}/reports/export?${new URLSearchParams(params)}`,
        ).catch((e) => toast(e.message, true));
      }),
  );
  document
    .querySelector("#weather-units")
    ?.addEventListener("change", async (e) => {
      const units = e.target.value;
      weatherUnits = units;
      try {
        weather = await api(endpoint + "/weather?units=" + units, {
          publicRequest: true,
        });
        updatePublic();
        document.querySelector("#weather-units").value = units;
      } catch (e) {
        toast(e.message, true);
      }
    });
}
