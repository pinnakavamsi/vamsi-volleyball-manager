import {
  api,
  setContext,
  esc,
  items,
  pretty,
  dt,
  date,
  badge,
  empty,
  icon,
  credit,
  toast,
  modal,
  confirm,
  field,
  select,
  textarea,
  formObject,
  teamName,
  courtName,
  score,
  matchCard,
  table,
  download,
  theme,
  errorText,
  closeDialog,
  zonedISO,
  localDateTime,
  imageTag,
  contrastText,
} from "./common.js";
import { addressFields, bindAddressSearch } from "./locations.js";
let organizerLayout = {},
  identity,
  org,
  dashboard,
  tournament,
  tournamentLocations,
  routeToken = 0,
  refreshTimer,
  refreshCleanup,
  qrTimer,
  qrDeadline = 0,
  qrGeneration = 0,
  organizationLogsPageNumber = 1,
  organizationSupportPage = 1,
  platformSupportPageNumber = 1,
  supportCurrentStatus = "";
const app = document.querySelector("#app");
const nav = [
  ["overview", "Overview", "grid"],
  ["tournaments", "Tournaments", "trophy"],
  ["schedule", "Schedule", "calendar"],
  ["teams", "Teams", "team"],
  ["venues", "Address book", "location"],
  ["reports", "Reports", "chart"],
  ["staff", "Organizer staff", "shield"],
  ["settings", "Settings", "settings"],
  ["logs", "Logs", "chart"],
  ["support", "Support", "help"],
];
const platformNav = [
  ["platform", "Dashboard", "grid"],
  ["platform-organizations", "Organizations", "team"],
  ["platform-backups", "Backups", "download"],
  ["platform-performance", "Performance", "chart"],
  ["platform-health", "Service health", "shield"],
  ["platform-support", "Support cases", "help"],
];
const platformMode = () => Boolean(identity?.user?.platform_admin);
const route = () => {
  const requested = location.hash.slice(1);
  if (platformMode()) {
    const page = requested.split("/")[0];
    return [...platformNav.map(([id]) => id), "profile", "help", "more"].includes(page)
      ? requested
      : "platform";
  }
  return requested || "overview";
};
const can = (permission) =>
  org?.role === "admin" ||
  {
    manager: ["manage", "teams", "schedule", "results"],
    scheduler: ["schedule"],
    results: ["results"],
  }[org?.role]?.includes(permission);
function heading(title, subtitle, action = "") {
  return `<div class="page-heading"><div><div class="eyebrow">${imageTag(org?.logo, org?.name + " logo")}${esc(org?.name || (platformMode() ? "PLATFORM ADMINISTRATION" : "WORKSPACE"))}</div><h1>${esc(title)}</h1><p>${esc(subtitle)}</p></div>${action}</div>`;
}
function options(list, label = "name") {
  return list.map((r) => [r.id, r[label] || r.name || r.id]);
}
function organizationOptions() {
  if (!identity.organizations.length)
    return '<option value="">No active organization</option>';
  return identity.organizations
    .map(
      (o) =>
        `<option value="${esc(o.id)}" ${o.id === org?.id ? "selected" : ""}>${esc(o.name)}${o.demo ? " · Demo" : ""}</option>`,
    )
    .join("");
}
async function refreshIdentity() {
  identity = await api("/auth/me", { publicRequest: true });
  setContext(identity.csrf_token);
  if (!identity.user) {
    location.href = "/login";
    return false;
  }
  if (identity.user.must_change_password) {
    hideQR();
    stopOrganizerRefresh();
    requiredPasswordChange();
    return false;
  }
  if (platformMode()) {
    org = undefined;
    setContext(null, "");
    return true;
  }
  if (!org && identity.organizations.length) {
    org = identity.organizations[0];
    setContext(null, org.id);
    dashboard = null;
    tournament = null;
    await loadOrganization();
    await loadOrganizerPrefs();
    shell();
  }
  return true;
}
function invitationsPanel() {
  const invitations = items(identity.invitations);
  if (!invitations.length) return "";
  return `<section class="panel panel-pad section-gap"><h2>Organization invitations</h2><p class="muted small">${platformMode() ? "These invitations record organization membership. Your platform administrator account continues to use platform administration; organization staff manage their own workspace." : "Accept an invitation to open that organization's tournaments, teams, and settings."}</p><div class="notice-list">${invitations
    .map(
      (invitation) =>
        `<div class="notice"><h3>${esc(invitation.organization.name)}</h3><p>${esc(pretty(invitation.role))} access · Expires ${esc(dt(invitation.expires_at))}</p>${invitation.requires_mfa && !identity.user.mfa_enabled ? '<p>This organization requires an authenticator. Set it up in Profile & security, then accept this invitation.</p><a class="button secondary section-gap" href="#profile">Set up authenticator</a>' : `<button type="button" class="button primary section-gap" data-accept-invitation="${esc(invitation.id)}" aria-label="Accept invitation to ${esc(invitation.organization.name)}">Accept invitation</button>`}</div>`,
    )
    .join("")}</div></section>`;
}
function bindInvitations() {
  document.querySelectorAll("[data-accept-invitation]").forEach(
    (button) =>
      (button.onclick = () => acceptInvitation(button.dataset.acceptInvitation)),
  );
}
function acceptInvitation(id) {
  const invitation = items(identity.invitations).find((item) => item.id === id);
  if (!invitation) return;
  modal(
    "Accept organization invitation",
    `<p>Join <strong>${esc(invitation.organization.name)}</strong> as ${esc(pretty(invitation.role))} using ${esc(identity.user.email)}.</p>` +
      (identity.user.mfa_enabled
        ? field(
            "otp",
            "Authenticator or recovery code (if requested)",
            "text",
            "",
            'autocomplete="one-time-code"',
          )
        : ""),
    async (fd) => {
      const result = await api(
        `/auth/invitations/${encodeURIComponent(id)}/accept`,
        {
          method: "POST",
          data: formObject(fd),
          publicRequest: true,
        },
      );
      identity = { ...identity, ...result };
      setContext(result.csrf_token);
      if (platformMode()) {
        org = undefined;
        setContext(null, "");
        shell();
        await render();
        toast("Invitation accepted. Platform administration is ready.");
      } else {
        await switchOrg(invitation.organization.id);
        toast(result.message || "Invitation accepted. Your workspace is ready.");
      }
    },
    { submit: "Accept invitation" },
  );
}
function showInitialCredentials(result) {
  const credentials = result.initial_credentials;
  if (!credentials) return;
  setTimeout(() => {
    modal(
      "Share organizer sign-in details",
      '<p>Save these details now and share them privately with the organizer. The temporary password is shown only once.</p>' +
        `<p>Sign-in page: <a href="/login">${esc(location.origin)}/login</a></p><div class="auth-form">` +
        field(
          "initial_username",
          "Username (email address)",
          "text",
          credentials.email,
          'readonly autocomplete="off"',
        ) +
        field(
          "initial_password",
          "Temporary password",
          "text",
          credentials.password,
          'readonly autocomplete="off"',
        ) +
        '</div><p class="form-help">The organizer signs in, chooses a new password, then accepts their organization invitation.</p>',
      null,
    );
    document.querySelector("#dialog-cancel").textContent = "Done";
  }, 0);
}
function requiredPasswordChange() {
  setContext(null, "");
  app.innerHTML = `<div class="auth-layout"><aside class="auth-brand"><span class="brand"><img src="/static/brand.svg" alt=""><span>Courtline<span class="product">VOLLEYBALL MANAGER</span></span></span><div><h1>Make your account yours.</h1><p>Choose a private password before entering your organizer workspace.</p></div>${credit()}</aside><main id="main" class="auth-main"><div class="auth-box"><span class="eyebrow">FIRST SIGN IN</span><h1>Change your temporary password</h1><p>Signed in as ${esc(identity.user.email)}. Use a new password with 12–256 characters.</p><form id="initial-password-form" class="auth-form">${field("current_password", "Temporary password", "password", "", 'required autocomplete="current-password"')}${field("password", "New password", "password", "", 'required minlength="12" maxlength="256" autocomplete="new-password"')}${field("confirm_password", "Confirm new password", "password", "", 'required minlength="12" maxlength="256" autocomplete="new-password"')}${identity.user.mfa_enabled ? field("otp", "Authenticator or recovery code", "text", "", 'required autocomplete="one-time-code"') : ""}<div class="form-error" role="alert" hidden></div><button class="button primary" type="submit">Save new password</button></form><button class="button ghost section-gap" id="initial-password-logout" type="button">Sign out</button></div>${credit()}</main></div>`;
  document.querySelector("#initial-password-logout").onclick = async () => {
    await api("/auth/logout", { method: "POST", publicRequest: true });
    location.href = "/login";
  };
  document.querySelector("#initial-password-form").onsubmit = async (event) => {
    event.preventDefault();
    const form = event.target,
      button = form.querySelector("[type=submit]"),
      error = form.querySelector(".form-error"),
      data = formObject(new FormData(form));
    error.hidden = true;
    if (data.password !== data.confirm_password) {
      error.textContent = "The new passwords do not match.";
      error.hidden = false;
      return;
    }
    delete data.confirm_password;
    button.disabled = true;
    try {
      await api("/auth/profile", { method: "POST", data, publicRequest: true });
      location.href = "/";
    } catch (err) {
      error.innerHTML = errorText(err);
      error.hidden = false;
      button.disabled = false;
    }
  };
}
function applyBranding() {
  const color = /^#[0-9a-f]{6}$/i.test(org?.color || "")
    ? org.color
    : "#216b50";
  document.documentElement.style.setProperty("--org-brand", color);
}
async function loadOrganization() {
  if (!org) return;
  try {
    const details = await api("/organizations/current");
    org = { ...org, ...(details.organization || details) };
    identity.organizations = identity.organizations.map((item) =>
      item.id === org.id ? { ...item, ...org } : item,
    );
  } catch {}
  applyBranding();
}
function shell() {
  if (identity.user.must_change_password) {
    requiredPasswordChange();
    return;
  }
  const active = route().split("/")[0];
  const platform = platformMode(),
    navigation = platform ? platformNav : nav,
    mobileNavigation = [...navigation.slice(0, 4), ["more", "More", "menu"]],
    home = platform ? "platform" : "overview";
  app.innerHTML = `<div class="shell organizer-shell ${platform ? "platform-shell" : ""}"><aside class="sidebar"><a href="#${home}" class="brand"><img src="/static/brand.svg" alt=""><span>Courtline<span class="product">VOLLEYBALL MANAGER</span></span></a>${platform ? "" : `<select class="organization-picker" aria-label="Current organization" ${identity.organizations.length ? "" : "disabled"}>${organizationOptions()}</select>`}<p class="nav-label">${platform ? "PLATFORM ADMINISTRATION" : "WORKSPACE"}</p><nav>${navigation.map(([id, label, ic]) => `<a class="nav-item ${active === id ? "active" : ""}" href="#${id}">${icon(ic)}${label}</a>`).join("")}</nav><div class="sidebar-bottom"><a class="nav-item ${active === "help" ? "active" : ""}" href="#help">${icon("help")}Help & about</a><a class="nav-item ${active === "profile" ? "active" : ""}" href="#profile">${icon("settings")}Profile & security</a><div class="sidebar-user"><span class="avatar">${esc((identity.user.name || identity.user.email).slice(0, 2).toUpperCase())}</span><div><strong>${esc(identity.user.name || "Organizer")}</strong><small>${platform ? "Platform administrator" : esc(pretty(org?.role || "organizer"))}</small></div><button class="icon-button" id="logout" aria-label="Sign out" style="color:#afc5b8;margin-left:auto">${icon("logout")}</button></div></div></aside><div class="workspace"><header class="topbar"><div class="breadcrumb">${platform ? "Platform" : "Workspace"} <span>/</span><span id="breadcrumb">${esc(navigation.find((n) => n[0] === active)?.[1] || pretty(active))}</span></div>${platform ? "" : `<select class="mobile-org" aria-label="Current organization" ${identity.organizations.length ? "" : "disabled"}>${organizationOptions()}</select>`}<div class="header-actions">${platform ? "" : `<label class="search-box">${icon("search")}<input id="global-search" placeholder="Find a tournament…" aria-label="Search tournaments"><kbd>⌘ K</kbd></label>`}<button class="icon-button" id="toggle-theme" aria-label="Toggle light or dark theme">${icon("sun")}</button>${platform ? "" : `<button class="icon-button notification-button" id="notifications" aria-label="View notifications">${icon("bell")}</button>`}<a href="#profile" class="avatar" aria-label="Profile">${esc((identity.user.name || "OR").slice(0, 2).toUpperCase())}</a></div></header><main id="main" class="main-content" tabindex="-1"><div class="loading-bar"></div></main>${credit()}</div><nav class="mobile-nav" aria-label="${platform ? "Mobile platform navigation" : "Mobile workspace navigation"}">${mobileNavigation.map(([id, label, ic]) => `<a href="#${id}" class="${active === id ? "active" : ""}">${icon(ic)}<span>${id === "overview" || id === "platform" ? "Home" : label}</span></a>`).join("")}</nav></div>`;
  document
    .querySelectorAll(".organization-picker,.mobile-org")
    .forEach((s) => (s.onchange = () => switchOrg(s.value)));
  document.querySelector("#logout").onclick = async () => {
    await api("/auth/logout", { method: "POST" });
    location.href = "/login";
  };
  document.querySelector("#toggle-theme").onclick = () =>
    theme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  const notifications = document.querySelector("#notifications"),
    search = document.querySelector("#global-search");
  if (notifications) notifications.onclick = showNotifications;
  if (search) search.onkeydown = (e) => {
    if (e.key === "Enter") {
      location.hash = "tournaments";
      setTimeout(() => {
        const f = document.querySelector("#record-search");
        if (f) {
          f.value = e.target.value;
          f.dispatchEvent(new Event("input"));
        }
      }, 200);
    }
  };
}
async function switchOrg(id) {
  if (platformMode()) {
    org = undefined;
    setContext(null, "");
    shell();
    await render();
    return;
  }
  hideQR();
  org = identity.organizations.find((o) => o.id === id);
  setContext(null, id);
  dashboard = null;
  tournament = null;
  organizationLogsPageNumber = 1;
  organizationSupportPage = 1;
  try {
    localStorage.setItem("courtline-org", id);
  } catch {}
  await loadOrganization();
  await loadOrganizerPrefs();
  location.hash = "overview";
  shell();
  await render();
}
export async function start() {
  window.addEventListener("unhandledrejection", (e) =>
    toast(
      e.reason?.message || "The action could not be completed. Please retry.",
      true,
    ),
  );
  identity = await api("/auth/me");
  setContext(identity.csrf_token);
  if (!identity.user) {
    location.href = identity.setup_required ? "/setup" : "/login";
    return;
  }
  if (identity.user.must_change_password) {
    requiredPasswordChange();
    return;
  }
  if (platformMode()) {
    org = undefined;
    setContext(null, "");
    if (route() === "platform")
      history.replaceState(null, "", location.pathname + location.search + "#platform");
  } else {
    let stored;
    try {
      stored = localStorage.getItem("courtline-org");
    } catch {}
    org =
      identity.organizations.find((o) => o.id === stored) ||
      identity.organizations[0];
    setContext(null, org?.id || "");
    await loadOrganization();
    await loadOrganizerPrefs();
  }
  shell();
  window.addEventListener("hashchange", () => {
    hideQR();
    shell();
    render();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) hideQR();
  });
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "k") {
      e.preventDefault();
      document.querySelector("#global-search")?.focus();
    }
  });
  await render();
}
async function render() {
  const token = ++routeToken;
  stopOrganizerRefresh();
  const r = route();
  let main = document.querySelector("#main");
  try {
    if (identity.user.must_change_password) {
      requiredPasswordChange();
      return;
    }
    if (
      !platformMode() &&
      !org &&
      r !== "platform" &&
      r !== "profile" &&
      r !== "help" &&
      r !== "more"
    ) {
      if (!(await refreshIdentity()) || token !== routeToken) return;
      if (org) {
        await render();
        return;
      }
      const destination = nav.find(([id]) => r.split("/")[0] === id)?.[1];
      main.innerHTML =
        heading(
          "Welcome to Courtline",
          destination
            ? `Accept an organization invitation to open ${destination}.`
            : "Accept an organization invitation to open your workspace.",
          identity.user.platform_admin
            ? '<a class="button secondary" href="#platform">Provision organization</a>'
            : "",
        ) +
        (items(identity.invitations).length
          ? invitationsPanel()
          : empty(
              "No active organization memberships",
              identity.user.platform_admin
                ? "Provision an organization with your email as its initial administrator, then accept the invitation."
                : "Ask an organization administrator to add your email address. Your invitation will appear here.",
            ));
      main.focus({ preventScroll: true });
      bindInvitations();
      return;
    }
    const [page, id, tab] = r.split("/");
    let html;
    if (platformMode() && platformNav.some(([name]) => name === page))
      html = await platformPage(page);
    else if (page === "overview") html = await overview();
    else if (page === "tournaments")
      html = id
        ? await eventPage(id, tab || "overview")
        : await tournamentsPage();
    else if (page === "teams") html = await teamsPage();
    else if (page === "venues") html = await venuesPage();
    else if (page === "staff") html = await staffPage();
    else if (page === "schedule") html = await schedulePage();
    else if (page === "reports") html = await reportsPage();
    else if (page === "logs") html = await logsPage();
    else if (page === "support") html = await supportPage();
    else if (page === "settings") html = await settingsPage();
    else if (page === "profile") html = await profilePage();
    else if (page === "platform") html = await platformPage();
    else if (page === "help") html = helpPage();
    else if (page === "more") html = morePage();
    else
      html = empty(
        "Page not found",
        "Choose a destination from the workspace navigation.",
      );
    if (token !== routeToken || !identity.user || identity.user.must_change_password) return;
    main = document.querySelector("#main");
    main.innerHTML = html;
    main.focus({ preventScroll: true });
    bindPage(page, id, tab);
    if (page === "overview") applyOrganizerLayout();
    if (
      ["overview", "schedule"].includes(page) ||
      (page === "tournaments" && id)
    )
      startRefresh(page, id, tab);
  } catch (e) {
    if (token === routeToken)
      main.innerHTML =
        heading("Unable to load this view", "Your data has not changed.") +
        `<div class="banner danger">${errorText(e)}<button class="button secondary" id="retry-view">Retry</button></div>`;
    document.querySelector("#retry-view")?.addEventListener("click", render);
  }
}
async function getDashboard() {
  dashboard = await api("/dashboard");
  org = { ...org, ...dashboard.organization };
  applyBranding();
  return dashboard;
}
function metric(label, value, desc, ic = "grid") {
  return `<div class="metric"><div class="metric-top"><span>${esc(label)}</span><span class="metric-icon">${icon(ic)}</span></div><strong class="metric-value">${esc(value ?? 0)}</strong><div class="metric-foot">${esc(desc)}</div></div>`;
}
function eventRow(t, matches) {
  const ms = matches.filter((m) => m.tournament_id === t.id),
    complete = ms.filter((m) =>
      ["ended", "completed"].includes(m.status),
    ).length,
    pct = ms.length ? Math.round((100 * complete) / ms.length) : 0;
  return `<div class="tournament-row"><span class="tournament-symbol">${icon(t.competition_type === "league" ? "calendar" : "trophy")}</span><div><a class="tournament-name" href="#tournaments/${esc(t.id)}/overview">${esc(t.name)}</a><div class="tournament-detail">${badge(t.status)}<span>${esc(date(t.start_date))}</span><span>· ${esc(pretty(t.competition_type))}</span></div></div><div class="progress-block">${complete}/${ms.length} matches<div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div></div></div>`;
}
async function overview() {
  return dashboardOverviewMarkup(await getDashboard());
}
function dashboardOverviewMarkup(d) {
  const m = d.metrics || {},
    matches = items(d.matches),
    live = matches.filter((m) => ["in_progress", "paused"].includes(m.status));
  const ts = items(d.tournaments);
  const status =
    m.status_distribution ||
    matches.reduce((a, m) => ((a[m.status] = (a[m.status] || 0) + 1), a), {});
  const completed = m.completed_games ?? 0,
    total = m.match_sample_size ?? matches.length,
    liveCount = m.live_games ?? live.length;
  return (
    heading(
      "Your tournament command center",
      `${new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" }).format(new Date())} · Everything happening across your courts.`,
      `<div class="button-group"><a class="button secondary" href="#reports">${icon("download")}Export report</a>${can("manage") ? `<button class="button primary" data-create="tournament">${icon("plus")}Create tournament</button>` : ""}</div>`,
    ) +
    (org.demo
      ? `<div class="banner demo">${icon("shield")} Isolated demo organization · Sample competitions and recorded match data</div>`
      : "") +
    `${overviewFreshness(d.generated_at)}<div class="metric-grid" id="overview-metrics">${metric("Active tournaments", ts.filter((t) => ["active", "published"].includes(t.status)).length, `${ts.length} total in this organization`, "trophy")}${metric("Registered teams", m.teams, "Reusable organization team records", "team")}${metric("Live games", m.live_games, `${m.scheduled_games ?? 0} games scheduled`, "court")}${metric("Awaiting approval", m.pending_results, "Results submitted for organizer review", "check")}</div><div class="dashboard-grid"><div class="stack"><section class="panel"><header class="panel-heading"><div><h2>Tournaments & leagues</h2><p>Your active competitions at a glance</p></div><a class="panel-link" href="#tournaments">View all ${icon("arrow")}</a></header><div class="tournament-list" id="overview-tournaments">${
      ts.length
        ? ts
            .slice(0, 4)
            .map((t) => eventRow(t, matches))
            .join("")
        : empty("No competitions yet")
    }</div></section><section class="panel"><header class="panel-heading"><div><h2>Live on court <span class="badge live" id="overview-live-count">${m.live_games ?? live.length} live</span></h2><p>Committed scores · Updates every 2 seconds</p></div><a class="panel-link" href="#schedule">All games ${icon("arrow")}</a></header><div class="live-cards" id="live-cards">${
      live.length
        ? live
            .slice(0, 4)
            .map((m) => organizerCard(m))
            .join("")
        : empty(
            "No live games",
            "Games appear here when a referee starts play.",
          )
    }</div></section><section class="panel"><header class="panel-heading"><div><h2>Upcoming fixtures</h2><p>Next scheduled games in your organization</p></div><a class="panel-link" href="#schedule">Full schedule ${icon("arrow")}</a></header><div id="overview-upcoming">${fixtureTable(upcomingMatches(matches).slice(0, 5))}</div><div class="table-footer"><span id="overview-schedule-summary">${m.scheduled_games ?? 0} scheduled fixtures · ${m.delayed_games ?? 0} delayed</span><a href="#schedule">View schedule →</a></div></section></div><div class="stack"><section class="panel"><header class="panel-heading"><div><h2>Court availability</h2><p id="overview-court-summary">${m.available_courts ?? 0} available · ${m.occupied_courts ?? 0} occupied</p></div><a class="panel-link" href="#venues">Manage ${icon("arrow")}</a></header><div class="court-list" id="overview-courts">${
      items(d.courts).length
        ? items(d.courts)
            .slice(0, 5)
            .map((c) => courtRow(c, items(d.venues)))
            .join("")
        : empty("No courts configured")
    }</div></section><section class="panel"><header class="panel-heading"><div><h2>Match progress</h2><p>All persisted fixtures in this organization</p></div></header><div id="overview-progress"><div class="chart-layout"><div class="donut" style="--complete:${total ? (completed / total) * 100 : 0}%;--live:${total ? ((completed + liveCount) / total) * 100 : 0}%" role="img" aria-label="${completed} completed, ${liveCount} live, ${total - completed - liveCount} other matches"><div class="donut-center"><strong>${total}</strong><span>Total matches</span></div></div><div class="chart-legend">${[
      ["Completed", completed, "var(--green)"],
      ["Live", liveCount, "#c9dec9"],
      ["Other", total - completed - liveCount, "var(--line)"],
    ]
      .map(
        ([name, n, c]) =>
          `<div class="legend-row"><span class="legend-color" style="--legend:${c}"></span>${name}<strong>${n}</strong></div>`,
      )
      .join(
        "",
      )}</div></div><details class="chart-alternative"><summary>View accessible data table</summary>${table(
      ["Status", "Matches"],
      Object.entries(status).map(([Status, Matches]) => ({
        Status: pretty(Status),
        Matches,
      })),
    )}</details></div></section><section class="panel"><header class="panel-heading"><div><h2>Needs your attention</h2><p>Operational exceptions and pending decisions</p></div></header><div class="panel-pad" id="overview-attention">${attention(d)}</div></section></div></div>`
  );
}
function courtRow(c, venues = []) {
  const status = c.status || (!c.available ? "unavailable" : "available");
  return `<div class="court-row"><span class="court-icon">${icon("court")}</span><div class="court-info"><strong>${esc(c.name)}</strong><p>${esc(venues.find((v) => v.id === c.venue_id)?.name || "Venue court")}</p></div><span class="court-state ${esc(status)}"><i></i>${esc(pretty(status))}</span></div>`;
}
function attention(d) {
  const ms = items(d.matches),
    delayed = ms.filter(
      (m) =>
        ["scheduled", "ready"].includes(m.status) &&
        m.planned_at &&
        Date.parse(m.planned_at) < Date.now(),
    ),
    pending = ms.filter((m) =>
      ["submitted", "pending"].includes(m.result_status),
    );
  return `<div class="notice-list">${pending
    .slice(0, 3)
    .map(
      (m) =>
        `<button class="notice" data-match="${esc(m.id)}" style="text-align:left;color:inherit"><strong>Result awaiting review</strong><p>${esc(teamName(m, "a"))} vs ${esc(teamName(m, "b"))} · ${esc(score(m))}</p></button>`,
    )
    .join("")}${items(d.notifications)
    .slice(0, 3)
    .map(
      (n) =>
        `<div class="notice"><strong>${esc(n.title || n.message || pretty(n.kind))}</strong><p>${esc(n.description || n.body || "")}</p></div>`,
    )
    .join("")}${delayed
    .slice(0, 2)
    .map(
      (m) =>
        `<button class="notice" data-match="${esc(m.id)}" style="text-align:left;color:inherit"><strong>Delayed scheduled game</strong><p>${esc(teamName(m, "a"))} vs ${esc(teamName(m, "b"))} · ${esc(dt(m.planned_at))}</p></button>`,
    )
    .join(
      "",
    )}${!pending.length && !items(d.notifications).length && !delayed.length ? `<p class="muted small no-margin">${icon("check")} No outstanding exceptions.</p>` : ""}</div>`;
}
function fixtureTable(matches) {
  matches = [...matches].sort((a, b) =>
    String(a.planned_at || "9999").localeCompare(
      String(b.planned_at || "9999"),
    ),
  );
  return table(
    [
      {
        label: "Game",
        render: (m) =>
          `<strong>${esc(teamName(m, "a"))}</strong><span class="muted"> vs </span><strong>${esc(teamName(m, "b"))}</strong>`,
      },
      {
        label: "Scheduled",
        render: (m) =>
          esc(
            dt(m.planned_at, {
              timeZone:
                tournament?.tournament.id === m.tournament_id
                  ? tournament.tournament.timezone
                  : items(dashboard?.tournaments).find(
                      (t) => t.id === m.tournament_id,
                    )?.timezone || org?.timezone,
            }),
          ),
      },
      { label: "Court", render: (m) => esc(courtName(m)) },
      { label: "Status", render: (m) => badge(m.status) },
      {
        label: "",
        render: (m) =>
          `<button class="row-action" data-match="${esc(m.id)}">Open game →</button>`,
      },
    ],
    matches,
  );
}
async function tournamentsPage() {
  const d = await getDashboard();
  return (
    heading(
      "Tournaments & leagues",
      "Create, organize, and publish competitions in your workspace.",
      can("manage")
        ? `<button class="button primary" data-create="tournament">${icon("plus")}Create tournament</button>`
        : "",
    ) +
    `<div class="filters">${field("search", "Search competitions", "search", "", 'id="record-search" placeholder="Name or format…"')}${select("status", "Status", [["", "All statuses"], "draft", "published", "active", "completed", "archived"], "", 'id="status-filter"')}</div><div class="record-grid" id="event-records">${
      items(d.tournaments)
        .map((t) => eventTile(t))
        .join("") || empty("Create your first competition")
    }</div>`
  );
}
function eventTile(t) {
  return `<article class="panel record-card" data-search="${esc((t.name + " " + t.competition_type).toLowerCase())}" data-status="${esc(t.status)}"><div class="record-top"><span class="tournament-symbol">${icon(t.competition_type === "league" ? "calendar" : "trophy")}</span>${badge(t.status)}</div><h2><a href="#tournaments/${esc(t.id)}/overview">${esc(t.name)}</a></h2><p>${esc(t.description || "Competition details and match operations.")}</p><div class="tournament-detail">${icon("calendar")}${esc(date(t.start_date))} – ${esc(date(t.end_date))}</div><div class="record-footer">${badge(t.visibility)}<a class="button secondary" href="#tournaments/${esc(t.id)}/overview">Manage competition ${icon("arrow")}</a></div></article>`;
}
function newTournament() {
  const today = localDateTime(new Date().toISOString(), org.timezone).slice(
    0,
    10,
  );
  modal(
    "Create a competition",
    `<div class="setup-steps"><div class="step active"><span>1</span> Essentials</div><div class="step"><span>2</span> Divisions & teams</div><div class="step"><span>3</span> Rules & schedule</div><div class="step"><span>4</span> Review</div></div><p class="muted small">Save a private draft, then add divisions, teams, venues, courts, and fixtures. Venue details can be added later from the tournament's Venues & courts tab.</p><div class="form-grid">${field("name", "Competition name", "text", "", 'required maxlength="180" placeholder="e.g. Autumn Open 2026"')}${select("competition_type", "Competition type", ["tournament", "league"])}${field("start_date", "Start date", "date", today, "required")}${field("end_date", "End date", "date", today, "required")}${field("timezone", "IANA timezone", "text", org.timezone || "America/New_York", "required")}<div class="full">${textarea("description", "Description")}</div></div>`,
    async (fd) => {
      const result = await api("/tournaments", {
        method: "POST",
        data: formObject(fd),
      });
      toast("Private draft created. Add your divisions and teams.");
      location.hash = `tournaments/${result.id || result.tournament?.id}/divisions`;
    },
    { submit: "Save private draft", wide: true },
  );
}
async function eventPage(id, tab) {
  tournament = await api(`/tournaments/${id}`);
  const t = tournament.tournament;
  const tabs = [
    "overview",
    "divisions",
    "venues",
    "rules",
    "schedule",
    "results",
    "standings",
    "analytics",
    "display",
    "review",
  ];
  let content;
  if (tab === "overview") content = eventOverview();
  else if (tab === "divisions") content = divisionsView();
  else if (tab === "venues") content = await eventVenuesView(id);
  else if (tab === "rules") content = rulesView();
  else if (tab === "schedule") content = scheduleView();
  else if (tab === "results") content = resultsView();
  else if (tab === "standings") content = standingsView();
  else if (tab === "analytics") content = await eventAnalytics();
  else if (tab === "display") content = displayView();
  else content = reviewView();
  return (
    heading(
      t.name,
      `${pretty(t.competition_type)} · ${date(t.start_date)} – ${date(t.end_date)} · ${t.timezone}`,
      `<div class="button-group">${t.visibility === "public" ? `<a class="button secondary" href="/t/${esc(t.slug)}" target="_blank" rel="noopener">${icon("link")}Public page</a>` : ""}${can("manage") ? `<button class="button primary" id="edit-event">${icon("settings")}Edit details</button>` : ""}</div>`,
    ) +
    `<div class="button-group" style="margin-bottom:18px">${badge(t.status)}${badge(t.visibility)}${org.demo ? badge("demo") : ""}<span class="muted small" id="event-summary-counts">${items(tournament.divisions).length} divisions · ${items(tournament.entries).length} entries · ${items(tournament.matches).length} fixtures</span></div><nav class="tabs" aria-label="Tournament sections">${tabs.map((x) => `<a href="#tournaments/${id}/${x}" class="tab ${x === tab ? "active" : ""}">${x === "venues" ? "Venues & courts" : pretty(x)}</a>`).join("")}</nav>${content}`
  );
}
function eventOverview(d = tournament) {
  const ms = items(d.matches),
    live = ms.filter((m) => ["in_progress", "paused"].includes(m.status));
  return `${overviewFreshness()}${!items(d.venues).length || !items(d.courts).length ? `<div class="venue-setup-notice"><div><strong>${items(d.venues).length ? "Add courts when your venue is ready" : "Venue details can be added later"}</strong><p>Set up this tournament's venues and courts before assigning games. Choose an address suggestion for venue weather.</p></div><a class="button secondary" href="#tournaments/${esc(d.tournament.id)}/venues">${can("schedule") ? "Set up venues & courts" : "View venues & courts"}${icon("arrow")}</a></div>` : ""}<div class="metric-grid" id="overview-metrics">${metric("Tournament entries", items(d.entries).length, "Entries across every division", "team")}${metric("Scheduled games", ms.filter((m) => ["scheduled", "ready"].includes(m.status)).length, "Pending fixture starts", "calendar")}${metric("Live games", live.length, "Active and paused matches", "court")}${metric("Results to approve", ms.filter((m) => m.result_status === "submitted").length, "Referee submitted results", "check")}</div><div class="dashboard-grid"><div class="stack"><section class="panel"><header class="panel-heading"><div><h2>Live games</h2><p>Authoritative scores updated every 2 seconds</p></div></header><div class="live-cards" id="live-cards">${live.map((m) => organizerCard(m)).join("") || empty("No games in progress")}</div></section><section class="panel"><header class="panel-heading"><div><h2>Upcoming games</h2></div></header><div id="overview-upcoming">${fixtureTable(upcomingMatches(ms).slice(0, 8))}</div></section></div><div class="stack"><section class="panel"><header class="panel-heading"><h2>Court operations</h2></header><div class="court-list" id="overview-courts">${
    items(d.courts)
      .map((c) => courtRow(c, items(d.venues)))
      .join("") || empty("No eligible courts")
  }</div></section><section class="panel"><header class="panel-heading"><h2>Announcements</h2>${can("manage") ? '<button class="row-action" id="create-announcement">Add notice</button>' : ""}</header><div class="panel-pad" id="overview-announcements">${
    items(d.announcements)
      .map(
        (a) =>
          `<div class="notice"><strong>${esc(a.title)}</strong><p>${esc(a.content || a.body || a.message)}</p>${badge(a.visibility || "public")}</div>`,
      )
      .join("") ||
    empty("No announcements", "Share schedule updates and event notices.")
  }</div></section><section class="panel" ${organizerLayout.show_weather === false ? "hidden" : ""}><header class="panel-heading"><h2>Venue weather</h2></header><div class="panel-pad" id="weather-panel"><p class="muted small">Loading available event forecasts…</p></div></section></div></div>`;
}
function divisionsView() {
  return `<div class="page-heading"><div><h2>Divisions, entries & stages</h2><p>Reusable teams enter each division with their own seed and snapshot.</p></div>${can("manage") ? `<button class="button primary" id="add-division">${icon("plus")}Add division</button>` : ""}</div><div class="stack">${
    items(tournament.divisions)
      .map((d) => {
        const es = items(tournament.entries).filter(
            (e) => e.division_id === d.id,
          ),
          ss = items(tournament.stages).filter((s) => s.division_id === d.id);
        return `<section class="panel"><header class="panel-heading"><div><h2>${esc(d.name)}</h2><p>${es.length} entries · ${ss.length} stages</p></div><div class="button-group">${can("manage") ? `<button class="button secondary small" data-division-edit="${esc(d.id)}">Edit division</button><button class="button secondary small" data-entry-add="${esc(d.id)}">Add team</button><button class="button primary small" data-stage-add="${esc(d.id)}">Add stage</button>` : ""}</div></header>${table(
          [
            { label: "Seed", key: "seed" },
            { label: "Team entry", key: "name" },
            { label: "Pool", render: (e) => esc(e.pool || "Unassigned") },
            { label: "Participation", render: (e) => badge(e.status) },
            {
              label: "Manage",
              render: (e) =>
                can("manage")
                  ? `<button class="row-action" data-entry-edit="${esc(e.id)}">Edit entry</button>`
                  : "Read only",
            },
          ],
          es,
        )}<div class="panel-pad"><h3>Stage structure</h3>${ss.length ? ss.map((s) => `<div class="notice" style="margin-bottom:10px"><div class="record-top"><div><strong>${s.position}. ${esc(s.name)}</strong><p>${pretty(s.format)} · ${items(tournament.matches).filter((m) => m.stage_id === s.id).length} fixtures</p></div><div class="button-group">${can("manage") ? `<button class="button secondary small" data-stage-edit="${esc(s.id)}">Edit stage</button><button class="button secondary small" data-stage-rules="${esc(s.id)}">Rules</button>` : ""}${can("schedule") ? `<button class="button primary small" data-generate="${esc(s.id)}">Generate fixtures</button>` : ""}</div></div></div>`).join("") : empty("Add a competition stage", "Choose round robin, pools, single elimination, or recurring league fixtures.")}</div></section>`;
      })
      .join("") ||
    empty(
      "Add your first division",
      "Divisions give teams independent schedules, rules, and standings.",
    )
  }</div>`;
}
function rulesSummary(r = {}) {
  return `<div class="rule-summary"><p><strong>${r.mode === "fixed" ? "Fixed sets" : "Best of"} ${(r.mode === "fixed" ? r.fixed_sets : r.best_of) || 3}</strong> · Regular sets to ${r.target || r.regular_target || 25} · Deciding set to ${r.deciding_target || 15}</p><p>Lead required to win: <strong>${r.win_by || 2} points</strong> · Hard cap: <strong>${r.cap || r.hard_cap || "None"}</strong></p><p>Timeouts per team per set: <strong>${r.timeouts ?? 2}</strong> · ${r.automatic_approval ? "Automatic finalization" : "Organizer approval required"}</p></div>`;
}
function rulesView() {
  return `<div class="split"><section class="panel panel-pad"><div class="record-top"><h2>Match scoring policy</h2>${can("manage") ? '<button class="button secondary" id="edit-rules">Edit rules</button>' : ""}</div><p class="muted small">Editable tournament defaults. Division and stage overrides take precedence; starting a game freezes its effective rules.</p>${rulesSummary(tournament.tournament.rules)}<div class="section-gap"><h3>Hard cap behavior</h3><p class="muted small">When configured, reaching the cap wins the set even without the normal winning margin. The winning margin is separate from ranking point differences.</p></div></section><section class="panel panel-pad"><h2>Standings & tie breaks</h2>${rankingSummary(tournament.tournament.rules)}<p class="muted small">Official standings use finalized results. Unresolved ties remain visible until an authorized resolution.</p></section></div><section class="panel panel-pad section-gap"><h2>Effective rule overrides</h2>${items(
    tournament.divisions,
  )
    .map(
      (d) =>
        `<div class="notice"><div class="record-top"><strong>${esc(d.name)}</strong>${can("manage") ? `<button class="row-action" data-division-rules="${esc(d.id)}">Edit override</button>` : ""}</div>${Object.keys(d.rules || {}).length ? rulesSummary({ ...tournament.tournament.rules, ...d.rules }) : "<p>Inherits tournament defaults.</p>"}</div>`,
    )
    .join("")}</section>`;
}
function rankingSummary(r) {
  const p = r.standings || r.points || {};
  const tb = r.tie_breaks || [
    "standings_points",
    "won",
    "head_to_head",
    "set_quotient",
    "point_quotient",
  ];
  return `<dl class="detail-list"><div><dt>Match win points</dt><dd>${esc(p.win ?? r.win_points ?? 2)}</dd></div><div><dt>Match loss points</dt><dd>${esc(p.loss ?? r.loss_points ?? 0)}</dd></div><div><dt>Draw points</dt><dd>${esc(p.draw ?? r.draw_points ?? 1)} · ${r.draw_allowed ? "Enabled" : "Only if draws are enabled"}</dd></div><div><dt>Forfeit policy</dt><dd>${esc(pretty(r.forfeit_policy || "Configured competition policy"))}</dd></div></dl><h3>Ordered tie break criteria</h3><ol class="muted small">${tb.map((x) => `<li>${esc(pretty(x))}</li>`).join("")}</ol><p class="muted small">Two-team head-to-head. Quotient zero denominators follow the configured policy; ranking explanations show applied metrics.</p>`;
}
function scheduleView() {
  return `<div class="page-heading"><div><h2>Fixtures & court operations</h2><p>Review schedules, assign eligible courts, and resolve conflicts.</p></div>${can("schedule") ? `<button class="button primary" id="manual-match">${icon("plus")}Add fixture</button>` : ""}</div>${matchFilters(tournament)}<section class="panel" id="fixture-panel">${fixtureTable(items(tournament.matches))}</section>`;
}
function matchFilters(d) {
  return `<div class="filters">${select("division", "Division", [["", "All divisions"], ...options(items(d.divisions))], "", 'id="match-division"')}${select("stage", "Stage", [["", "All stages"], ...options(items(d.stages))], "", 'id="match-stage"')}${select("status", "Game status", [["", "All statuses"], "scheduled", "ready", "in_progress", "paused", "ended", "postponed", "abandoned", "cancelled"], "", 'id="match-status"')}${field("date", "Day", "date", "", 'id="match-date"')}${select(
    "view",
    "Layout",
    [
      ["list", "List"],
      ["court", "Court view"],
    ],
    "",
    'id="schedule-layout"',
  )}</div>`;
}
function resultsView() {
  const ms = items(tournament.matches).filter(
    (m) =>
      ["ended", "abandoned", "cancelled"].includes(m.status) ||
      m.result_status !== "unsubmitted",
  );
  return `<div class="banner">${icon("shield")} Official standings include finalized results. Submitted games remain provisional until approved.</div><section class="panel">${table(
    [
      {
        label: "Match",
        render: (m) => `${esc(teamName(m, "a"))} vs ${esc(teamName(m, "b"))}`,
      },
      { label: "Set scores", render: (m) => esc(score(m)) },
      { label: "Outcome", render: (m) => esc(pretty(m.outcome || "Played")) },
      { label: "Approval", render: (m) => badge(m.result_status) },
      {
        label: "Review",
        render: (m) =>
          `<button class="row-action" data-match="${esc(m.id)}">Review result →</button>`,
      },
    ],
    ms,
  )}</section>`;
}
function standingsView() {
  const raw = tournament.standings,
    groups = Array.isArray(raw)
      ? raw
      : raw?.divisions ||
        Object.entries(raw || {}).map(([division_id, rows]) => ({
          division_id,
          rows,
        }));
  return `<div class="banner">${icon("check")} Official standings · Generation ${tournament.tournament.standings_generation || 0} · Finalized results only</div>${
    groups
      .map((g) => {
        const rows = Array.isArray(g)
          ? g
          : g.rows || g.standings || g.table || [];
        return `<section class="panel section-gap"><header class="panel-heading"><div><h2>${esc(g.division_name || g.name || items(tournament.divisions).find((d) => d.id === g.division_id)?.name || "Division standings")}</h2>${can("results") && rows.some((r) => r.tied) ? `<button class="row-action" data-resolve-tie="${esc(g.division_id)}">Resolve tied ranking</button>` : ""}<p>Actual tie break metrics · Unresolved ties remain marked</p></div></header>${standingsTable(rows)}</section>`;
      })
      .join("") ||
    empty(
      "No official standings yet",
      "Finalize match results to populate points and ranking metrics.",
    )
  }<section class="panel panel-pad section-gap">${rankingSummary(tournament.tournament.rules)}</section>`;
}
export function standingsTable(rows) {
  return table(
    [
      { label: "Rank", render: (r) => esc(r.rank ?? r.position ?? "—") },
      {
        label: "Team",
        render: (r) =>
          `<strong>${esc(r.name || r.team_name || r.entry_name)}</strong>${r.tied || r.unresolved ? badge("unresolved tie") : ""}`,
      },
      { label: "P", render: (r) => esc(r.played ?? r.matches_played ?? 0) },
      { label: "W", render: (r) => esc(r.won ?? r.wins ?? 0) },
      { label: "L", render: (r) => esc(r.lost ?? r.losses ?? 0) },
      {
        label: "Points",
        render: (r) =>
          `<strong>${esc(r.standings_points ?? r.points ?? 0)}</strong>`,
      },
      {
        label: "Sets +/−",
        render: (r) => `${esc(r.sets_won ?? 0)} / ${esc(r.sets_lost ?? 0)}`,
      },
      { label: "Set quotient", render: (r) => esc(r.set_quotient ?? "—") },
      { label: "Point quotient", render: (r) => esc(r.point_quotient ?? "—") },
      {
        label: "Ranking explanation",
        render: (r) =>
          esc(
            Array.isArray(r.explanation)
              ? r.explanation.join(" · ")
              : r.explanation || r.ranking_explanation || "Configured criteria",
          ),
      },
    ],
    rows,
  );
}
function reviewView() {
  const t = tournament.tournament;
  return `<div class="setup-steps">${["Essentials", "Divisions & teams", "Rules & fixtures", "Review & publish"].map((s, i) => `<div class="step ${i === 3 ? "active" : ""}"><span>${i + 1}</span>${s}</div>`).join("")}</div><div class="split"><section class="panel panel-pad"><h2>Competition review</h2><dl class="detail-list"><div><dt>Name</dt><dd>${esc(t.name)}</dd></div><div><dt>Type</dt><dd>${esc(pretty(t.competition_type))}</dd></div><div><dt>Date range</dt><dd>${esc(date(t.start_date))} – ${esc(date(t.end_date))}</dd></div><div><dt>Timezone</dt><dd>${esc(t.timezone)}</dd></div><div><dt>Divisions / entries</dt><dd>${items(tournament.divisions).length} / ${items(tournament.entries).length}</dd></div><div><dt>Stages / fixtures</dt><dd>${items(tournament.stages).length} / ${items(tournament.matches).length}</dd></div></dl>${rulesSummary(t.rules)}<button class="button secondary section-gap" id="validate-event">Validate competition</button><div id="validation-result" class="section-gap"></div></section><section class="panel panel-pad"><h2>Publication & lifecycle</h2><p class="muted small">Publication state and public visibility are separate. Public visibility makes the spectator page available without login.</p><form id="publish-form" class="auth-form">${select(
    "visibility",
    "Spectator visibility",
    [
      ["private", "Private — delegated referees only"],
      ["public", "Public — spectators can view"],
    ],
    t.visibility,
  )}${can("manage") ? '<button class="button primary" type="submit">Review and publish</button>' : ""}</form><hr style="border:0;border-top:1px solid var(--line);margin:24px 0"><form id="lifecycle-form" class="auth-form">${select("status", "Lifecycle state", ["draft", "published", "active", "completed", "archived"], t.status)}${can("manage") ? '<button class="button secondary">Apply lifecycle change</button>' : ""}</form><p class="form-help">Archive preserves results and history. Historical pages remain public only while visibility stays Public.</p></section></div>`;
}
export const DISPLAY_WIDGETS = Object.freeze([
  "live",
  "standings",
  "upcoming",
  "results",
  "brackets",
  "announcements",
  "weather",
  "sponsors",
]);
export function normalizeDisplayWidgets(value, fallback = []) {
  const widgets = Array.isArray(value) ? value : fallback;
  return [
    ...new Set(widgets.map((w) => (w === "schedule" ? "upcoming" : w))),
  ].filter((w) => DISPLAY_WIDGETS.includes(w));
}
export function displayWidgetSize(sizes, widget) {
  return (
    sizes?.[widget] || (widget === "upcoming" ? sizes?.schedule : undefined)
  );
}
function displayView() {
  const config = tournament.display || tournament.tournament.display || {};
  const widgets = normalizeDisplayWidgets(config.widgets, [
    "live",
    "standings",
    "upcoming",
    "results",
    "announcements",
    "weather",
  ]);
  return `<div class="split"><section class="panel panel-pad"><h2>Public presentation editor</h2><p class="muted small">These settings belong to the public TV/projector display. Organizer preferences are saved separately.</p><form id="display-form"><div id="widget-list">${[
    ...widgets,
    ...DISPLAY_WIDGETS.filter((w) => !widgets.includes(w)),
  ]
    .map(
      (w) =>
        `<div class="display-widget-row" data-widget="${w}"><label class="check-field"><input type="checkbox" name="widgets" value="${w}" ${widgets.includes(w) ? "checked" : ""}>${pretty(w)}</label><select data-size="${w}" aria-label="${w} widget size"><option value="normal" ${displayWidgetSize(config.sizes, w) !== "wide" ? "selected" : ""}>Normal</option><option value="wide" ${displayWidgetSize(config.sizes, w) === "wide" ? "selected" : ""}>Wide</option></select><div class="drag-controls"><button type="button" data-move="up" aria-label="Move ${w} up">↑</button><button type="button" data-move="down" aria-label="Move ${w} down">↓</button></div></div>`,
    )
    .join(
      "",
    )}</div><div class="form-grid section-gap">${field("rotation_seconds", "Rotation interval (seconds)", "number", config.rotation_seconds || 20, 'min="5" max="300"')}${field("text_scale", "Text scale", "number", config.text_scale || 1, 'min="0.8" max="2" step="0.1"')}${select("units", "Weather units", ["celsius", "fahrenheit"], config.units || "celsius")}${field("background", "Background color", "color", config.background || "#0e281e")}${field("logo", "Display logo URL", "url", config.logo || "")}${field("sponsor_name", "Sponsor name", "text", config.sponsor_name || "")}${field("sponsor_image", "Sponsor image URL", "url", config.sponsor_image || "")}${field("sponsor_url", "Sponsor website", "url", config.sponsor_url || "")}${select("division_id", "Division filter", [["", "All divisions"], ...options(items(tournament.divisions))], config.division_id || "")}${select("court_id", "Court filter", [["", "All courts"], ...options(items(tournament.courts))], config.court_id || "")}</div><label class="check-field section-gap"><input type="checkbox" name="public_reports" ${config.public_reports?.length ? "checked" : ""}>Enable public-safe report downloads</label>${can("manage") ? '<button class="button primary section-gap">Save display configuration</button>' : ""}</form></section><section class="panel panel-pad"><h2>Presentation preview</h2><p class="muted small">Open the dedicated clean display. Public visibility is required for anonymous access.</p><a class="button secondary" href="/display/${esc(tournament.tournament.slug)}" target="_blank" rel="noopener">Open public display ${icon("arrow")}</a><button class="button primary section-gap" type="button" id="private-display-preview">Preview selected content</button><p class="form-help">The display contains selected tournament information, freshness status, and the application credit. Device fullscreen setup may be needed to hide browser chrome.</p><h3 class="section-gap">Selected content</h3><div id="display-preview">${widgets.map((w) => `<div class="notice">${esc(pretty(w))}</div>`).join("")}</div></section></div>`;
}
async function teamsPage() {
  const ts = items(await api("/teams?archived=true&limit=500"));
  return (
    heading(
      "Team directory",
      "Reusable team records. Private contacts stay inside your organization.",
      can("teams")
        ? `<button class="button primary" data-create="team">${icon("plus")}Add team</button>`
        : "",
    ) +
    `<div class="filters">${field("search", "Search teams", "search", "", 'id="record-search" placeholder="Name or abbreviation…"')}</div><div class="record-grid" id="team-records">${ts.map((t) => `<article class="panel record-card" data-search="${esc((t.name + " " + t.abbreviation).toLowerCase())}"><div class="record-top">${imageTag(t.logo, t.name + " logo", "team-mark") || `<span class="team-mark" style="--team:${esc(t.color)}">${esc(t.abbreviation || t.name.slice(0, 2).toUpperCase())}</span>`}${badge(t.archived ? "archived" : "active")}</div><h2>${esc(t.name)}</h2><p>${esc(t.contact?.email || "No private contact recorded")}</p><div class="record-footer">${can("teams") ? `<button class="button secondary" data-edit-team="${esc(t.id)}">Edit team</button>` : ""}</div></article>`).join("") || empty("No teams yet")}</div>`
  );
}
async function venuesPage() {
  const [vs, cs] = await Promise.all([api("/venues"), api("/courts")]);
  dashboard = { ...dashboard, venues: items(vs), courts: items(cs) };
  return (
    heading(
      "Address book",
      "Saved venues and addresses for reuse. Add venues and courts to each tournament from its Venues & courts tab.",
      can("schedule")
        ? `<div class="button-group"><button class="button secondary" data-create="court">${icon("plus")}Add court</button><button class="button primary" data-create="venue">${icon("plus")}Add venue</button></div>`
        : "",
    ) +
    `<div class="record-grid">${
      items(vs)
        .map((v) => venueCard(v, items(cs)))
        .join("") ||
      empty(
        "Your address book is empty",
        "Save a venue here or add it directly to a tournament. Addresses are saved for your organization to reuse.",
      )
    }</div>`
  );
}
function venueCard(venue, courts = [], tournamentId = "") {
  const venueCourts = courts.filter((court) => court.venue_id === venue.id);
  const resolved = venue.latitude != null && venue.longitude != null && venue.settings?.coordinates_verified;
  return `<article class="panel record-card venue-card"><div class="record-top"><span class="tournament-symbol">${icon("location")}</span>${badge(venue.indoor ? "indoor" : "outdoor")}</div><h2>${esc(venue.name)}</h2><p>${esc(venue.address || "Address can be added later")}</p><p class="small muted">${esc(venue.timezone)} · ${resolved ? "Address ready for weather" : "Choose an address suggestion to enable weather"}</p><div class="court-list" style="padding:0">${venueCourts.map((court) => `<div>${courtRow(court, [venue])}${can("schedule") ? `<button class="row-action" data-edit-court="${esc(court.id)}">Edit availability & hours</button>` : ""}</div>`).join("") || '<p class="muted small">No courts yet. Add them when the venue details are ready.</p>'}</div><div class="record-footer">${can("schedule") ? `<button class="button secondary" data-edit-venue="${esc(venue.id)}">Edit venue</button>${tournamentId ? `<button class="button primary" data-tournament-add-court="${esc(venue.id)}">${icon("plus")}Add court</button><button class="button ghost" data-tournament-remove-venue="${esc(venue.id)}">Remove from tournament</button>` : ""}` : ""}${venue.address ? `<a class="button ghost" href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(venue.address)}" target="_blank" rel="noopener">Directions ${icon("arrow")}</a>` : ""}</div></article>`;
}
async function eventVenuesView(id) {
  tournamentLocations = await api(`/tournaments/${id}/venues`);
  const venues = items(tournamentLocations.items);
  const courts = venues.flatMap((venue) => items(venue.courts));
  const available = items(tournamentLocations.available).filter((venue) => !venues.some((linked) => linked.id === venue.id));
  return `<div class="page-heading"><div><h2>Venues & courts</h2><p>${venues.length} venues · ${courts.length} courts in this tournament. Add details now or return later.</p></div>${can("schedule") ? `<div class="button-group"><button class="button secondary" id="reuse-event-venue" ${available.length ? "" : "disabled"}>Use saved venue</button><button class="button primary" id="add-event-venue">${icon("plus")}Add venue</button></div>` : ""}</div><div class="record-grid">${venues.map((venue) => venueCard(venue, courts, id)).join("") || empty("Choose where this tournament will play", "Add a new venue or reuse one from your address book. You can leave this step until the venue is confirmed.")}</div><section class="panel panel-pad section-gap"><h2>Weather for this tournament</h2><p class="muted small">Forecasts use each venue's selected address and the tournament dates. Up to seven available event days are shown.</p><div id="weather-panel"><p class="muted small">Loading available event forecasts…</p></div></section>`;
}
function reuseEventVenue(id) {
  const attached = items(tournamentLocations?.items);
  const available = items(tournamentLocations?.available).filter((venue) => !attached.some((linked) => linked.id === venue.id));
  modal("Use a saved venue", `<p class="muted small">The venue and its courts become available to this tournament. Saved venue details are shared by tournaments that use them.</p>${select("venue_id", "Address book venue", available.map((venue) => [venue.id, `${venue.name}${venue.address ? " · " + venue.address : ""}`]), "", "required")}`, async (fd) => {
    await api(`/tournaments/${id}/venues`, { method: "POST", data: { venue_id: fd.get("venue_id") } });
    toast("Saved venue added to this tournament.");
    await render();
  }, { submit: "Add to tournament" });
}
function removeEventVenue(id, venueId) {
  const venue = items(tournamentLocations?.items).find((row) => row.id === venueId);
  modal("Remove venue from tournament", `<p>Remove ${esc(venue?.name || "this venue")} from this tournament? The saved venue and its courts stay in the address book.</p><p class="form-help">A venue with assigned games cannot be removed. Move those games to another tournament venue first.</p>`, async () => {
    await api(`/tournaments/${id}/venues/${venueId}`, { method: "DELETE" });
    toast("Venue removed from this tournament.");
    await render();
  }, { submit: "Remove from tournament" });
}
async function staffPage() {
  const staff = items(await api("/memberships"));
  return (
    heading(
      "Organizer staff",
      "Membership roles and tournament access are scoped to this organization.",
      org.role === "admin"
        ? `<button class="button primary" data-create="membership">${icon("plus")}Invite organizer</button>`
        : "",
    ) +
    `<section class="panel">${table(
      [
        {
          label: "Organizer",
          render: (m) =>
            `<strong>${esc(m.name || m.user?.name || m.email || m.user_id)}</strong><div class="muted small">${esc(m.email || m.user?.email || "")}</div>`,
        },
        { label: "Role", render: (m) => badge(m.role) },
        { label: "Membership", render: (m) => badge(m.status) },
        {
          label: "Tournament scope",
          render: (m) =>
            esc(
              m.tournament_ids?.length
                ? `${m.tournament_ids.length} assigned events`
                : "All permitted events",
            ),
        },
        {
          label: "Manage",
          render: (m) =>
            org.role === "admin"
              ? `<button class="row-action" data-edit-member="${esc(m.id)}">Edit access</button>`
              : "Read only",
        },
      ],
      staff,
    )}</section><div class="banner section-gap">${icon("shield")} One organizer identity may belong to multiple organizations. Suspending this membership preserves other organization access and audit history.</div>`
  );
}
async function schedulePage() {
  const d = await getDashboard();
  return (
    heading(
      "Schedule & game operations",
      "Assign available courts, prepare games, and review current match state.",
    ) +
    `<div class="filters">${select("tournament", "Competition", [["", "All competitions"], ...options(items(d.tournaments))], "", 'id="schedule-tournament"')}${select("status", "Status", [["", "All statuses"], "scheduled", "ready", "in_progress", "paused", "ended", "postponed", "abandoned", "cancelled"], "", 'id="match-status"')}${field("date", "Day", "date", "", 'id="match-date"')}${select(
      "view",
      "Layout",
      [
        ["list", "List"],
        ["court", "Court view"],
      ],
      "",
      'id="schedule-layout"',
    )}</div><section class="panel" id="fixture-panel">${fixtureTable(items(d.matches))}</section>`
  );
}
const reportTypes = [
  "summary",
  "matches",
  "standings",
  "team_performance",
  "schedule",
  "court_use",
  "referees",
  "outstanding_results",
  "audit",
  "history",
];
function supportCaseCards(rows, platform = false) {
  if (!rows.length) return empty("No support cases", platform ? "Organization tickets will appear here when they are submitted." : "Open a ticket if your organization needs help.");
  return rows.map((item) => `<article class="panel panel-pad section-gap support-case"><div class="panel-heading"><div><div class="eyebrow">${platform ? `${esc(item.organization_name)} · ` : ""}${esc(pretty(item.category))}</div><h2>${esc(item.subject)}</h2><p>${platform ? `Opened by ${esc(item.opened_by_name)} · ` : ""}${esc(dt(item.created_at))}</p></div>${badge(item.status)}</div><p class="support-case-description">${esc(item.description)}</p><div class="support-thread">${(item.messages || []).map((message) => `<div class="support-message"><div class="muted small"><strong>${esc(message.author_name)}</strong> · ${esc(message.author_type === "platform_admin" ? "Support" : "Organization staff")} · ${esc(dt(message.created_at))}</div><p>${esc(message.message)}</p></div>`).join("")}</div>${platform ? `<div class="filters">${select("status", "Case status", ["open", "in_progress", "waiting_on_organization", "resolved", "closed"], item.status, `data-case-status="${esc(item.id)}"`)}</div>` : ""}${!['resolved','closed'].includes(item.status) ? `<button type="button" class="button secondary" data-case-reply="${esc(item.id)}" data-platform-case="${platform}">${platform ? "Reply to organization" : "Reply to support"}</button>` : ""}</article>`).join("");
}
function supportPager(result, label = "cases", attribute = "data-support-page") {
  const pages = Math.max(1, Math.ceil((result.total || 0) / (result.page_size || 50)));
  if (pages < 2) return "";
  return `<div class="table-footer"><span>Page ${result.page} of ${pages} · ${result.total} ${label}</span><div class="button-group"><button class="button secondary" ${attribute}="${result.page - 1}" ${result.page <= 1 ? "disabled" : ""}>Previous</button><button class="button secondary" ${attribute}="${result.page + 1}" ${result.page >= pages ? "disabled" : ""}>Next</button></div></div>`;
}
async function logsPage() {
  const result = await api(`/audit?page=${organizationLogsPageNumber}`), logs = result.rows || [];
  return heading("Organization logs", "A record of organization activity, changes, and support actions.") +
    `<section class="panel">${table([
      { label: "When", render: (row) => esc(dt(row.at)) },
      { label: "Activity", render: (row) => `<span class="audit-action">${esc(pretty(row.action))}</span>` },
      { label: "Actor", render: (row) => esc(pretty(row.actor_type || row.actor_id || "System")) },
      { label: "Record", render: (row) => `${esc(pretty(row.entity_type))}${row.entity_id ? ` · ${esc(row.entity_id)}` : ""}` },
      { label: "Reason", render: (row) => esc(row.reason || "—") },
      { label: "Changes", render: (row) => row.changes && (row.changes.before || row.changes.after) ? `<details><summary>View</summary><pre>${esc(JSON.stringify(row.changes, null, 2))}</pre></details>` : "—" },
    ], logs, { emptyText: "No organization activity has been recorded yet." })}</section>${supportPager(result, "events", "data-logs-page")}`;
}
async function supportPage() {
  supportCurrentStatus = "";
  const result = await api(`/support-cases?page=${organizationSupportPage}`);
  return heading("Support", "Open a private support ticket and follow the conversation with the platform team.", '<button type="button" class="button primary" id="new-support-case">Open a ticket</button>') +
    `<p class="muted small">Ticket messages are visible to organization staff and platform support administrators.</p>${supportCaseCards(result.rows || [])}${supportPager(result)}`;
}
async function platformSupportPage() {
  const result = await api(`/platform/support-cases?page=${platformSupportPageNumber}&status=${encodeURIComponent(supportCurrentStatus)}`, { publicRequest: true });
  return heading("Support cases", "Review organization tickets, reply, and update case status.", '<button type="button" class="button secondary" data-refresh-platform>Refresh</button>') +
    `<p class="muted small">Platform support access to customer correspondence is recorded in the affected organization’s logs.</p><div class="filters">${select("support-status-filter", "Filter by status", [["", "All statuses"], "open", "in_progress", "waiting_on_organization", "resolved", "closed"], supportCurrentStatus, 'id="support-status-filter"')}</div>${supportCaseCards(result.rows || [], true)}${supportPager(result)}`;
}
async function reportsPage() {
  reportData = null;
  reportFilters = { type: "matches" };
  reportColumns = [];
  reportAllColumns = [];
  reportPage = 1;
  const [d, jobs, views] = await Promise.all([
    getDashboard(),
    api("/exports"),
    api("/preferences?kind=report"),
  ]);
  return (
    heading(
      "Reports & data",
      "Filtered persisted records, authorized exports, and reviewed imports.",
      `<div class="button-group"><button class="button secondary" id="open-import">${icon("plus")}Import CSV</button><button class="button primary" id="export-job">${icon("download")}Create export</button></div>`,
    ) +
    `<nav class="tabs"><button class="tab active" data-report-tab="browser">Report browser</button><button class="tab" data-report-tab="jobs">Export jobs</button><button class="tab" data-report-tab="audit">Audit history</button><button class="tab" data-report-tab="views">Saved views</button></nav><div id="report-browser"><form id="report-filters" class="filters">${select("type", "Report type", reportTypes, "matches")}${select("tournament_id", "Competition", [["", "All competitions"], ...options(items(d.tournaments))])}${select("division_id", "Division", [["", "All divisions"]])}${select("stage_id", "Stage", [["", "All stages"]])}${select("venue_id", "Venue", [["", "All venues"], ...options(items(d.venues))])}${select("court_id", "Court", [["", "All courts"], ...options(items(d.courts))])}${select("outcome", "Outcome", [["", "All outcomes"], "normal", "draw", "forfeit", "abandoned", "cancelled"])}${select("team_id", "Team", [["", "All teams"], ...options(items(d.teams))])}${select("result_status", "Result status", [["", "All results"], "unsubmitted", "submitted", "finalized"])}${field("from", "From date", "date")}${field("to", "To date", "date")}<button class="button primary">Run report</button></form><div class="filters">${field("search", "Search results", "search", "", 'id="report-search"')}${select("sort", "Sort column", [["", "Default order"]], "", 'id="report-sort"')}${select(
      "direction",
      "Sort direction",
      [
        ["asc", "Ascending"],
        ["desc", "Descending"],
      ],
      "asc",
      'id="report-direction"',
    )}<button class="button secondary" id="report-columns">Choose columns</button><button class="button secondary" id="save-report">Save view</button><button class="button secondary" id="print-report">Print</button><div class="button-group">${["csv", "xlsx", "pdf"].map((f) => `<button class="button secondary" data-report-download="${f}">${f.toUpperCase()}</button>`).join("")}</div></div><div class="banner" id="report-metadata">Choose filters and run a report. Export uses these exact filters.</div><section class="panel" id="report-table">${empty("Run your first report", "Review scope, filters, and result status before exporting.")}</section></div><div id="report-jobs" hidden><section class="panel">${jobsTable(items(jobs))}</section></div><div id="report-audit" hidden><div class="loading-bar"></div></div><div id="report-views" hidden><section class="panel panel-pad">${
      items(views)
        .map(
          (v) =>
            `<div class="notice"><strong>${esc(v.layout?.name || v.name || "Saved report view")}</strong><button class="row-action" data-saved-view="${esc(v.id)}" data-filters="${esc(JSON.stringify(v.layout?.filters || {}))}">Load view</button></div>`,
        )
        .join("") || empty("No saved report views")
    }</section></div>`
  );
}
function jobsTable(jobs) {
  return table(
    [
      {
        label: "Report",
        render: (j) => esc(pretty(j.type || j.kind || j.payload?.type)),
      },
      {
        label: "Format",
        render: (j) => esc((j.format || j.payload?.format || "").toUpperCase()),
      },
      { label: "Created", render: (j) => esc(dt(j.created_at)) },
      { label: "State", render: (j) => badge(j.status) },
      { label: "Progress", render: (j) => esc(`${j.progress ?? 0}%`) },
      {
        label: "Download",
        render: (j) =>
          ["completed", "complete"].includes(j.status)
            ? `<button class="row-action" data-job-download="${esc(j.id)}">Download file</button>`
            : ["queued", "running"].includes(j.status)
              ? `<button class="row-action" data-job-cancel="${esc(j.id)}">Cancel</button>`
              : esc(j.error || j.message || ""),
      },
    ],
    jobs,
  );
}
async function settingsPage() {
  const raw = await api("/organizations/current"),
    o = raw.organization || raw;
  return (
    heading(
      "Organization settings",
      "Branding, security policy, and private dashboard preferences.",
    ) +
    `<div class="split"><section class="panel panel-pad"><h2>Branding & contact</h2><form id="org-form" class="auth-form">${field("name", "Organization name", "text", o.name, "required")}${field("timezone", "Default IANA timezone", "text", o.timezone, "required")}${field("color", "Brand color", "color", o.color || "#216b50")}${field("logo", "Organization logo URL", "url", o.logo || "")}${field("email", "Contact email", "email", o.contact?.email || "")}${field("phone", "Contact phone", "tel", o.contact?.phone || "")}${org.role === "admin" ? '<button class="button primary">Save organization</button>' : ""}</form></section><section class="panel panel-pad"><h2>Security policy</h2><form id="security-form" class="auth-form"><label class="check-field"><input type="checkbox" name="require_mfa" ${o.settings?.require_mfa ? "checked" : ""}>Require MFA for organizer staff</label><label class="check-field"><input type="checkbox" name="auto_finalize" ${o.settings?.auto_finalize ? "checked" : ""}>Automatically finalize valid results</label>${select("units", "Weather temperature units", ["celsius", "fahrenheit"], o.settings?.units || "celsius")}${field("qr_admission_minutes", "QR admission lifetime (minutes)", "number", o.settings?.qr_admission_minutes || 15, 'min="1" max="120"')}<p class="form-help">QR admission lifetime is independent of its 10-second screen display and of an admitted referee session.</p>${org.role === "admin" ? '<button class="button primary">Save security policy</button>' : ""}</form><h2 class="section-gap">Dashboard preferences</h2><form id="preferences-form" class="auth-form">${select("theme", "Theme", ["light", "dark"], document.documentElement.dataset.theme)}${select("density", "Layout density", ["comfortable", "compact"], organizerLayout.density || "comfortable")}${organizerWidgetEditor()}<label class="check-field"><input name="show_weather" type="checkbox" ${organizerLayout.show_weather !== false ? "checked" : ""}>Show venue weather</label><div class="button-group"><button class="button secondary">Save my preferences</button><button class="button ghost" type="button" id="reset-preferences">Reset defaults</button></div></form>${org.role === "admin" ? '<h2 class="section-gap">Organization data portability</h2><p class="muted small">Download supported business records and configuration as JSON. Authentication secrets and operator backups are excluded.</p><button class="button secondary" id="organization-export">Export organization data</button>' : ""}</section></div>`
  );
}
async function profilePage() {
  if (!(await refreshIdentity())) return "";
  const sessions = items(await api("/auth/sessions"));
  return (
    heading(
      "Profile & security",
      platformMode()
        ? "Manage your platform administrator account and signed-in devices."
        : "Manage your global organizer identity and signed-in devices.",
    ) +
    invitationsPanel() +
    `<div class="split"><section class="panel panel-pad"><h2>Your profile</h2><form id="profile-form" class="auth-form">${field("name", "Display name", "text", identity.user.name, "required")}${field("email", "Email address", "email", identity.user.email, "readonly")}<button class="button primary">Save profile</button></form><h2 class="section-gap">Multi-factor authentication</h2><p class="muted small">${identity.user.mfa_enabled ? "MFA is enabled for this identity." : "Use an authenticator app to protect administrator access."}</p>${badge(identity.user.mfa_enabled ? "enabled" : "disabled")}${!identity.user.mfa_enabled ? '<button class="button secondary section-gap" id="mfa-setup">Set up authenticator</button>' : '<div class="button-group section-gap"><button class="button secondary" id="mfa-replace">Replace authenticator</button><button class="button secondary" id="mfa-recovery">Replace recovery codes</button><button class="button secondary" id="mfa-disable">Disable MFA</button></div>'}<h2 class="section-gap">Change password</h2><form id="password-form" class="auth-form">${field("current_password", "Current password", "password", "", 'required autocomplete="current-password"')}${field("password", "New password", "password", "", 'required minlength="12" autocomplete="new-password"')}${identity.user.mfa_enabled ? field("otp", "Authenticator or recovery code", "text", "", "required") : ""}<button class="button secondary">Update password</button></form></section><section class="panel"><header class="panel-heading"><h2>Signed-in devices</h2></header>${table(
      [
        {
          label: "Device",
          render: (s) =>
            `<strong>${esc(s.user_agent || "Browser session")}</strong>${s.current ? badge("current") : ""}`,
        },
        { label: "Last active", render: (s) => esc(dt(s.last_seen_at)) },
        { label: "Expires", render: (s) => esc(dt(s.expires_at)) },
        {
          label: "",
          render: (s) =>
            `<button class="row-action" data-revoke-session="${esc(s.id)}" data-current="${s.current ? "true" : "false"}">Revoke session</button>`,
        },
      ],
      sessions,
    )}</section></div>`
  );
}
function platformRefreshAction(extra = "") {
  return `<div class="button-group">${extra}<button type="button" class="button secondary" data-refresh-platform>${icon("refresh")}Refresh snapshot</button></div>`;
}
function platformSnapshot(operations) {
  return `<p class="muted small">Snapshot recorded ${esc(dt(operations.generated_at))}. Refresh to see the latest measurements.</p>`;
}
function measuredCount(value) {
  return Number.isFinite(value) ? new Intl.NumberFormat().format(value) : "Not available";
}
function measuredBytes(value) {
  if (!Number.isFinite(value)) return "Not available";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit++;
  }
  return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value)} ${units[unit]}`;
}
function operationRows(rows) {
  return table(
    [
      { label: "Measure", key: "label" },
      { label: "Current value", key: "value" },
      { label: "Details", key: "details" },
    ],
    rows.map(([label, value, details = ""]) => ({ label, value, details })),
  );
}
function platformOrganizationsTable(organizations) {
  return `<section class="panel">${table(
    [
      { label: "Organization", key: "name" },
      { label: "Slug", key: "slug" },
      { label: "Timezone", key: "timezone" },
      {
        label: "Organization administrator",
        render: (o) =>
          o.initial_admin
            ? `${esc(o.initial_admin.email)}<br>${badge(o.initial_admin.status)}`
            : "Not assigned",
      },
      { label: "State", render: (o) => badge(o.active ? "active" : "suspended") },
      { label: "Data", render: (o) => badge(o.demo ? "demo" : "organization") },
      {
        label: "Access setup",
        render: (o) =>
          o.can_setup_administrator
            ? `<button type="button" class="row-action" data-setup-organization-administrator="${esc(o.id)}" data-organization-name="${esc(o.name)}">Set up organization administrator</button>`
            : o.initial_admin?.can_issue_credentials
              ? `<button type="button" class="row-action" data-issue-initial-credentials="${esc(o.id)}" data-admin-email="${esc(o.initial_admin.email)}">Generate initial sign-in details</button>`
              : o.initial_admin?.can_renew_invitation
                ? `<button type="button" class="row-action" data-renew-initial-invitation="${esc(o.id)}" data-admin-email="${esc(o.initial_admin.email)}">Renew invitation</button>`
                : "",
      },
    ],
    items(organizations),
    { emptyText: "No organizations yet. Provision your first organization to get started." },
  )}</section>`;
}
async function platformPage(page = "platform") {
  if (!(await refreshIdentity())) return "";
  const provision = '<button type="button" class="button primary" data-create="organization">' + icon("plus") + "Provision organization</button>";
  if (page === "platform-support") return await platformSupportPage();
  if (page === "platform-organizations") {
    const organizations = await api("/platform/organizations", { publicRequest: true });
    return heading(
      "Organizations",
      "Create organization workspaces and set up their own administrators.",
      platformRefreshAction(provision),
    ) + platformOrganizationsTable(organizations);
  }
  const operations = await api("/operations", { publicRequest: true }),
    service = operations.service || {},
    performance = operations.performance || {},
    database = performance.database || {},
    pool = operations.database_pool || {},
    storage = operations.private_storage || {},
    backup = operations.backup || {},
    jobs = operations.jobs || {},
    snapshot = platformSnapshot(operations);
  if (page === "platform-backups")
    return heading(
      "Backups & recovery",
      "Review recorded backup status and recovery readiness.",
      platformRefreshAction(),
    ) + snapshot +
      `<div class="metric-grid">${metric("Backup status", pretty(backup.status || "unavailable"), "Recorded by the backup operator", "download")}${metric("Last successful backup", backup.last_success_at ? dt(backup.last_success_at) : "Not recorded", "A completed package recorded in backup monitoring", "clock")}${metric("Continuous archive", pretty(backup.wal_archive?.status || "not_configured"), "WAL archive monitoring status", "shield")}${metric("Recovery targets", operations.recovery_targets?.measured ? "Measured" : "Not measured", "Data loss and restore time need validation", "chart")}</div>` +
      `<section class="panel panel-pad"><h2>${backup.status === "not_configured" ? "Backup setup required" : "Backup monitoring"}</h2><p>${esc(backup.message || "Backup monitoring is unavailable.")}</p><p class="muted small">An operator must configure encrypted storage separate from this server, a backup schedule, and restore checks. Backup execution uses protected operator access.</p></section><section class="panel section-gap">${operationRows([
        ["Backup package type", backup.type || "Not recorded"],
        ["Current database archive mode", backup.current_archive_mode || "Not recorded"],
        ["Recent archive recorded", backup.pitr_enabled ? "Yes" : "No", "Based on current archive mode and a recent successful operator status record. Restore validation is separate."],
        ["Last WAL archive", backup.wal_archive?.last_archived_at ? dt(backup.wal_archive.last_archived_at) : "Not recorded"],
        ["Proposed data loss target", `${operations.recovery_targets?.proposed_rpo_minutes ?? "—"} minutes`, "Target only; confirm through backup and recovery validation."],
        ["Proposed restore time target", `${operations.recovery_targets?.proposed_rto_minutes ?? "—"} minutes`, "Target only; confirm through an isolated restore exercise."],
      ])}</section>`;
  if (page === "platform-performance")
    return heading(
      "Performance",
      "Current database measurements and cumulative activity counters.",
      platformRefreshAction(),
    ) + snapshot +
      `<div class="metric-grid">${metric("Database round trip", Number.isFinite(performance.database_round_trip_ms) ? `${performance.database_round_trip_ms} ms` : "Not available", "One SELECT 1 measurement from this request", "clock")}${metric("Database connections", measuredCount(database.connections), "All connected backends, including idle connections", "grid")}${metric("Application pool usage", Number.isFinite(pool.utilization_percent) ? `${pool.utilization_percent}%` : "Not available", "Connections in the current application process", "chart")}${metric("Database size", measuredBytes(database.size_bytes), "Current database storage usage", "download")}</div>` +
      `<section class="panel panel-pad"><h2>Measurement scope</h2><p>${esc(performance.measurement || "A historical performance series has not been configured.")}</p><p class="muted small">${esc(operations.monitoring || "Historical monitoring and alerts require configuration.")}</p></section><section class="panel section-gap">${operationRows([
        ["Committed transactions", measuredCount(database.transactions_committed), "Cumulative since database statistics reset."],
        ["Database transaction rollbacks", measuredCount(database.transactions_rolled_back), "Cumulative since database statistics reset. Includes read-only transaction cleanup; this is not an application error count."],
        ["Deadlocks", measuredCount(database.deadlocks), "Cumulative since database statistics reset."],
        ["Statistics reset", database.stats_reset_at ? dt(database.stats_reset_at) : "Not recorded"],
        ["Configured application pool size", measuredCount(pool.size), "Current application process only."],
        ["Checked-out connections", measuredCount(pool.checked_out), "Current application process only."],
        ["Current pool overflow", Number.isFinite(pool.overflow) ? measuredCount(Math.max(0, pool.overflow)) : "Not available", "Additional open connections beyond the configured pool size."],
        ["Free application storage", measuredBytes(storage.free_bytes), "Filesystem used for private application files."],
        ["Total application storage", measuredBytes(storage.total_bytes), "Filesystem used for private application files."],
      ])}</section>`;
  if (page === "platform-health")
    return heading(
      "Service health",
      "Application readiness, database connectivity, and background processing.",
      platformRefreshAction(),
    ) + snapshot +
      `<div class="metric-grid">${metric("Service readiness", pretty(service.status || "unavailable"), "Current application and database checks", "shield")}${metric("Background jobs pending", operations.jobs ? measuredCount((jobs.queued || 0) + (jobs.running || 0)) : "Not available", "Queued or running background jobs", "clock")}${metric("Pending events", measuredCount(operations.outbox?.pending), "Committed events awaiting publication", "refresh")}${metric("Free application storage", measuredBytes(storage.free_bytes), "Private files filesystem", "download")}</div>` +
      `<section class="panel panel-pad"><h2>Readiness checks</h2><p class="muted small">${esc(service.checks || "Readiness checks are unavailable.")}</p></section><section class="panel section-gap">${operationRows([
        ["Application", pretty(service.application || "unavailable"), "This authenticated application request."],
        ["Database", pretty(service.database || "unavailable"), "Database connection check."],
        ["Schema", pretty(service.schema || "unavailable"), "Migration revision and row security checks."],
        ["Runtime database permissions", service.runtime_role === "unsafe" ? "Unsafe" : service.status === "ready" ? "Validated" : "Not ready", "Runtime role must preserve database isolation."],
        ["Oldest pending event", operations.outbox?.oldest_pending_at ? dt(operations.outbox.oldest_pending_at) : "None recorded"],
        ["Backup monitoring", pretty(backup.status || "unavailable"), "Review Backups for recovery configuration."],
      ])}</section><section class="panel section-gap"><header class="panel-heading"><h2>Background jobs</h2></header>${table(
        [{ label: "State", render: (row) => badge(row.status) }, { label: "Count", key: "count" }],
        Object.entries(jobs).map(([status, count]) => ({ status, count })),
        { emptyText: "No background jobs recorded." },
      )}</section>`;
  return (
    heading(
      "Platform dashboard",
      "Manage organizations and keep the application service ready.",
      platformRefreshAction(provision),
    ) +
    snapshot +
    `<div class="metric-grid">${metric("Organizations", measuredCount(operations.organizations), "Registered organization workspaces", "team")}${metric("Service readiness", pretty(service.status || "unavailable"), "Application, database, and schema checks", "shield")}${metric("Backup status", pretty(backup.status || "unavailable"), "Recorded backup monitoring state", "download")}${metric("Database round trip", Number.isFinite(performance.database_round_trip_ms) ? `${performance.database_round_trip_ms} ms` : "Not available", "Current request measurement", "clock")}</div>` +
    `<div class="record-grid">${[
      ["platform-organizations", "Organizations", "Create workspaces and share administrator sign-in details.", "team"],
      ["platform-backups", "Backups & recovery", "Review backup monitoring and recovery setup.", "download"],
      ["platform-performance", "Performance", "Inspect database samples and cumulative counters.", "chart"],
      ["platform-health", "Service health", "Review readiness and background processing.", "shield"],
      ["platform-support", "Support cases", "Review organization support tickets and respond.", "help"],
    ].map(([id, title, description, ic]) => `<a class="panel record-card" href="#${id}">${icon(ic)}<h2>${title}</h2><p>${description}</p></a>`).join("")}</div>` +
    (backup.status === "not_configured" || backup.status === "unavailable" || backup.status === "stale"
      ? `<section class="panel panel-pad section-gap"><h2>Backup attention required</h2><p>${esc(backup.message || "Review backup monitoring and configuration.")}</p><a class="button secondary" href="#platform-backups">Review backups</a></section>`
      : "")
  );
}
function morePage() {
  const navigation = platformMode() ? platformNav : nav.slice(4);
  return (
    heading(
      platformMode() ? "Platform navigation" : "Workspace navigation",
      "Choose an administration or data destination.",
    ) +
    `<div class="record-grid">${[...navigation, ["profile", "Profile & security", "shield"], ["help", "Help & about", "help"]].map(([id, label, ic]) => `<a class="panel record-card" href="#${id}">${icon(ic)}<h2>${label}</h2></a>`).join("")}</div>`
  );
}
function helpPage() {
  if (platformMode())
    return heading("Platform help", "Organization setup, account security, and service operations.") +
      `<div class="help-grid">${[
        ["Organization administrators", "Provision an organization with a separate organization administrator email. Share the temporary sign-in details privately. The administrator changes their password and accepts the organization invitation."],
        ["Existing organizations", "Use Set up organization administrator when an organization has no staff administrator. Existing organizers keep their current password. Renew invitation provides a fresh invitation when the previous one expired."],
        ["Platform account", "This account manages organizations and application health. Organization staff use their own organizer accounts to run competitions. Protect platform access with an authenticator in Profile & security."],
        ["Backup readiness", "Backups shows recorded operator status. Configure encrypted storage separate from the server, scheduled backups, continuous archiving where required, and an isolated restore check before relying on recovery targets."],
        ["Performance measurements", "Database round trip is one current SELECT 1 sample. Database transaction and deadlock counters are cumulative since the statistics reset. Historical monitoring and alerts require configuration."],
        ["Application credit", "Designed and developed by Vamsi Pinnaka."],
      ].map(([title, description]) => `<section class="panel help-card"><h2>${esc(title)}</h2><p>${esc(description)}</p></section>`).join("")}</div>`;
  return (
    heading(
      "Help & about",
      "Practical guidance for running your volleyball competition.",
    ) +
    `<div class="help-grid">${[
      [
        "Competition setup",
        "Save a private draft. Add divisions, reusable team entries, stages, and scoring policies. Preview fixture generation, resolve conflicts, validate, and review before publication.",
      ],
      [
        "Court assignment",
        "Scheduled games may have no court. Assign an eligible available court before Start Game. Paused games continue occupying the court; explicit End Game releases it. Active court moves require an organizer reason.",
      ],
      [
        "Private referee QR",
        "The QR is hidden until Show QR Code. Each reveal lasts 10 seconds and hides when this page backgrounds. The admission grant lasts separately. Public links never provide scoring access.",
      ],
      [
        "Saved and recovery",
        "Saved means the server acknowledged a durable recorded event. Pending actions exist only on the scoring device until acknowledged. Re-scan to recover central scores; explicitly take scoring control to fence an old phone.",
      ],
      [
        "Screen awake & whistle",
        "Test the local whistle at the venue after a tap. Device volume determines loudness. Screen Wake Lock works only in supported, visible browsers; manual locking and device power settings can interrupt it.",
      ],
      [
        "Rankings & results",
        "Official standings include finalized results. Review points, ordered tie breaks, zero quotients, and cross-pool policies explicitly. Result corrections keep history and require a reason.",
      ],
      [
        "Privacy & publication",
        "Publishing and visibility are independent. Switching to Private blocks anonymous access. Previously viewed or downloaded public data cannot be recalled.",
      ],
      [
        "Application credit",
        "Designed and developed by Vamsi Pinnaka. Courtline is the Volleyball Manager organizer, referee, and spectator experience.",
      ],
    ]
      .map(
        ([t, p]) =>
          `<section class="panel help-card"><h2>${t}</h2><p>${p}</p></section>`,
      )
      .join("")}</div>`
  );
}
function bindPage(page, id, tab) {
  bindInvitations();
  document.querySelectorAll("[data-logs-page]").forEach((button) => button.onclick = async () => {
    organizationLogsPageNumber = Number(button.dataset.logsPage);
    await render();
  });
  document.querySelectorAll("[data-support-page]").forEach((button) => button.onclick = async () => {
    if (platformMode()) platformSupportPageNumber = Number(button.dataset.supportPage);
    else organizationSupportPage = Number(button.dataset.supportPage);
    await render();
  });
  document.querySelector("#support-status-filter")?.addEventListener("change", async (event) => {
    supportCurrentStatus = event.target.value;
    platformSupportPageNumber = 1;
    await render();
  });
  document.querySelector("#new-support-case")?.addEventListener("click", () => modal(
    "Open a support ticket",
    field("subject", "Subject", "text", "", 'required maxlength="180"') +
      select("category", "Category", [["account_access", "Account access"], ["tournament_setup", "Tournament setup"], ["scoring", "Scoring"], ["other", "Other"]]) +
      textarea("description", "How can we help?", "", 'required maxlength="12000"'),
    async (fd) => {
      await api("/support-cases", { method: "POST", data: formObject(fd) });
      organizationSupportPage = 1;
      toast("Support ticket opened.");
      await render();
    }, { submit: "Open ticket", wide: true },
  ));
  document.querySelectorAll("[data-case-reply]").forEach((button) => button.onclick = () => {
    const platform = button.dataset.platformCase === "true";
    modal("Reply to support case", textarea("message", "Message", "", 'required maxlength="12000"'), async (fd) => {
      const root = platform ? "/platform/support-cases" : "/support-cases";
      await api(`${root}/${encodeURIComponent(button.dataset.caseReply)}/messages`, { method: "POST", data: formObject(fd), publicRequest: platform });
      toast("Support reply sent.");
      await render();
    }, { submit: "Send reply", wide: true });
  });
  document.querySelectorAll("[data-case-status]").forEach((control) => control.onchange = async () => {
    try {
      await api(`/platform/support-cases/${encodeURIComponent(control.dataset.caseStatus)}`, { method: "PATCH", data: { status: control.value }, publicRequest: true });
      toast("Support case status updated.");
      await render();
    } catch (e) { toast(e.message, true); }
  });
  document.querySelectorAll("[data-refresh-platform]").forEach(
    (button) => (button.onclick = render),
  );
  document.querySelectorAll("[data-setup-organization-administrator]").forEach(
    (button) =>
      (button.onclick = () => modal(
        "Set up organization administrator",
        `<p>Create an organization staff administrator for <strong>${esc(button.dataset.organizationName)}</strong>. Use a separate account from the platform administrator.</p>` +
          field("email", "Organization administrator email", "email", "", 'required autocomplete="off"') +
          field("name", "Organization administrator name", "text", "", 'required maxlength="120"'),
        async (fd) => {
          const result = await api(
            `/platform/organizations/${encodeURIComponent(button.dataset.setupOrganizationAdministrator)}/administrator`,
            { method: "POST", data: formObject(fd), publicRequest: true },
          );
          showInitialCredentials(result);
          toast(result.message || "Organization administrator set up. Share their sign-in details privately.");
          await render();
        },
        { submit: "Set up administrator" },
      )),
  );
  document.querySelectorAll("[data-renew-initial-invitation]").forEach(
    (button) =>
      (button.onclick = () =>
        modal(
          "Renew initial administrator invitation",
          `<p>Create a fresh invitation for ${esc(button.dataset.adminEmail)}. Their current password stays the same; they can accept the invitation after signing in.</p>`,
          async () => {
            const result = await api(
              `/platform/organizations/${encodeURIComponent(button.dataset.renewInitialInvitation)}/initial-admin-invitation`,
              { method: "POST", data: {} },
            );
            showInitialCredentials(result);
            toast(result.message || "Invitation renewed. The administrator can accept it after signing in.");
            await render();
          },
          { submit: "Renew invitation" },
        )),
  );
  document.querySelectorAll("[data-issue-initial-credentials]").forEach(
    (button) =>
      (button.onclick = () =>
        modal(
          "Generate initial sign-in details",
          `<p>Create a temporary password for ${esc(button.dataset.adminEmail)}. Save the returned details and share them privately with this organizer.</p>`,
          async () => {
            const result = await api(
              `/platform/organizations/${encodeURIComponent(button.dataset.issueInitialCredentials)}/initial-admin-credentials`,
              { method: "POST", data: {} },
            );
            showInitialCredentials(result);
            await render();
          },
          { submit: "Generate sign-in details" },
        )),
  );
  document
    .querySelectorAll("[data-create]")
    .forEach((b) => (b.onclick = () => createRecord(b.dataset.create)));
  bindMatches();
  document
    .querySelector("#record-search")
    ?.addEventListener("input", filterRecords);
  document
    .querySelector("#status-filter")
    ?.addEventListener("change", filterRecords);
  document.querySelector("#edit-event")?.addEventListener("click", editEvent);
  document.querySelector("#add-event-venue")?.addEventListener("click", () =>
    recordForm("venue", {}, { tournamentId: id }),
  );
  document.querySelector("#reuse-event-venue")?.addEventListener("click", () => reuseEventVenue(id));
  document.querySelectorAll("[data-tournament-add-court]").forEach((button) =>
    (button.onclick = () => recordForm("court", {}, { tournamentId: id, venueId: button.dataset.tournamentAddCourt })),
  );
  document.querySelectorAll("[data-tournament-remove-venue]").forEach((button) =>
    (button.onclick = () => removeEventVenue(id, button.dataset.tournamentRemoveVenue)),
  );
  document.querySelector("#add-division")?.addEventListener("click", () =>
    modal(
      "Add division",
      field("name", "Division name", "text", "", "required") +
        field("category", "Category or skill level"),
      async (fd) => {
        await api(`/tournaments/${id}/divisions`, {
          method: "POST",
          data: formObject(fd),
        });
        toast("Division saved.");
        render();
      },
    ),
  );
  document
    .querySelectorAll("[data-division-edit]")
    .forEach((b) => (b.onclick = () => editDivision(b.dataset.divisionEdit)));
  document
    .querySelectorAll("[data-entry-edit]")
    .forEach((b) => (b.onclick = () => editEntry(b.dataset.entryEdit)));
  document
    .querySelectorAll("[data-stage-edit]")
    .forEach((b) => (b.onclick = () => editStage(b.dataset.stageEdit)));
  document
    .querySelectorAll("[data-entry-add]")
    .forEach((b) => (b.onclick = () => addEntry(b.dataset.entryAdd)));
  document
    .querySelectorAll("[data-stage-add]")
    .forEach((b) => (b.onclick = () => addStage(b.dataset.stageAdd)));
  document
    .querySelectorAll("[data-generate]")
    .forEach((b) => (b.onclick = () => generateFixtures(b.dataset.generate)));
  document
    .querySelector("#edit-rules")
    ?.addEventListener("click", () =>
      editRules(`/tournaments/${id}`, tournament.tournament.rules),
    );
  document
    .querySelectorAll("[data-division-rules]")
    .forEach(
      (b) =>
        (b.onclick = () =>
          editRules(
            `/divisions/${b.dataset.divisionRules}`,
            items(tournament.divisions).find(
              (d) => d.id === b.dataset.divisionRules,
            )?.rules,
          )),
    );
  document
    .querySelectorAll("[data-stage-rules]")
    .forEach(
      (b) =>
        (b.onclick = () =>
          editRules(
            `/stages/${b.dataset.stageRules}`,
            items(tournament.stages).find((d) => d.id === b.dataset.stageRules)
              ?.rules,
          )),
    );
  document
    .querySelector("#manual-match")
    ?.addEventListener("click", manualMatch);
  document
    .querySelectorAll("[data-resolve-tie]")
    .forEach((b) => (b.onclick = () => resolveTie(b.dataset.resolveTie)));
  document
    .querySelectorAll(
      "#match-division,#match-stage,#match-status,#match-date,#schedule-layout,#schedule-tournament",
    )
    .forEach(
      (f) =>
        (f.onchange = () =>
          filterMatches(page === "schedule" ? dashboard : tournament)),
    );
  document
    .querySelector("#create-announcement")
    ?.addEventListener("click", () =>
      modal(
        "Publish announcement",
        field("title", "Title", "text", "", "required") +
          textarea("content", "Announcement", "", "required") +
          select("visibility", "Audience", ["public", "private"]) +
          field("expires_at", "Optional expiry", "datetime-local"),
        async (fd) => {
          const v = formObject(fd);
          if (!v.expires_at) delete v.expires_at;
          else
            v.expires_at = zonedISO(
              v.expires_at,
              tournament.tournament.timezone,
            );
          await api(`/tournaments/${id}/announcements`, {
            method: "POST",
            data: v,
          });
          toast("Announcement saved.");
          render();
        },
      ),
    );
  document
    .querySelector("#validate-event")
    ?.addEventListener("click", () => validateEvent(id));
  document
    .querySelector("#publish-form")
    ?.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api(`/tournaments/${id}/publish`, {
          method: "POST",
          data: formObject(new FormData(e.target)),
        });
        toast("Publication saved.");
        render();
      } catch (err) {
        toast(err.message, true);
      }
    });
  document
    .querySelector("#lifecycle-form")
    ?.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (
        await confirm(
          "Change competition lifecycle",
          "Apply the selected lifecycle state? Results and historical records are preserved.",
        )
      )
        saveForm(e, `/tournaments/${id}`);
    });
  document.querySelectorAll("[data-move]").forEach(
    (b) =>
      (b.onclick = () => {
        const row = b.closest(".display-widget-row");
        if (b.dataset.move === "up" && row.previousElementSibling)
          row.parentNode.insertBefore(row, row.previousElementSibling);
        else if (b.dataset.move === "down" && row.nextElementSibling)
          row.parentNode.insertBefore(row.nextElementSibling, row);
      }),
  );
  document
    .querySelector("#private-display-preview")
    ?.addEventListener("click", previewDisplay);
  document
    .querySelector("#display-form")
    ?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const data = formObject(
        fd,
        ["rotation_seconds", "text_scale"],
        ["public_reports"],
      );
      data.sizes = {};
      e.target
        .querySelectorAll("[data-size]")
        .forEach((i) => (data.sizes[i.dataset.size] = i.value));
      data.public_reports = fd.has("public_reports")
        ? ["schedule", "matches", "standings"]
        : [];
      data.widgets = normalizeDisplayWidgets(
        [...e.target.querySelectorAll("input[name=widgets]:checked")].map(
          (i) => i.value,
        ),
      );
      try {
        await api(`/tournaments/${id}/display`, {
          method: "PATCH",
          data: { display: data },
        });
        toast("Public display settings saved.");
        render();
      } catch (err) {
        toast(err.message, true);
      }
    });
  if (
    page === "tournaments" &&
    id &&
    (((!tab || tab === "overview") && organizerLayout.show_weather !== false) || tab === "venues")
  )
    loadWeather(id);
  document
    .querySelectorAll("[data-edit-team]")
    .forEach((b) => (b.onclick = () => editRecord("team", b.dataset.editTeam)));
  document
    .querySelectorAll("[data-edit-venue]")
    .forEach(
      (b) => (b.onclick = () => editRecord("venue", b.dataset.editVenue)),
    );
  document
    .querySelectorAll("[data-edit-court]")
    .forEach(
      (b) => (b.onclick = () => editRecord("court", b.dataset.editCourt)),
    );
  document
    .querySelectorAll("[data-edit-member]")
    .forEach((b) => (b.onclick = () => editMember(b.dataset.editMember)));
  if (page === "reports") bindReports();
  document.querySelector("#org-form")?.addEventListener("submit", (e) =>
    saveForm(e, "/organizations/current", (v) => ({
      name: v.name,
      timezone: v.timezone,
      color: v.color,
      logo: v.logo,
      contact: { email: v.email, phone: v.phone },
    })),
  );
  document
    .querySelector("#security-form")
    ?.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const raw = await api("/organizations/current"),
          current = raw.organization || raw,
          fd = new FormData(e.target);
        await api("/organizations/current", {
          method: "PATCH",
          data: {
            settings: {
              ...current.settings,
              require_mfa: fd.has("require_mfa"),
              auto_finalize: fd.has("auto_finalize"),
              units: fd.get("units"),
              qr_admission_minutes: Number(fd.get("qr_admission_minutes")),
            },
          },
        });
        toast("Security policy saved.");
      } catch (err) {
        toast(err.message, true);
      }
    });
  document
    .querySelector("#preferences-form")
    ?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target),
        v = formObject(fd);
      v.widgets = [
        ...e.target.querySelectorAll("input[name=widgets]:checked"),
      ].map((i) => i.value);
      v.sizes = {};
      e.target
        .querySelectorAll("[data-size]")
        .forEach((i) => (v.sizes[i.dataset.size] = i.value));
      v.show_weather = fd.has("show_weather");
      organizerLayout = v;
      theme(v.theme);
      await api("/preferences", {
        method: "POST",
        data: { kind: "organizer", layout: v },
      });
      toast("Private dashboard preferences saved.");
    });
  document
    .querySelector("#reset-preferences")
    ?.addEventListener("click", async () => {
      theme("light");
      organizerLayout = {};
      await api("/preferences", {
        method: "POST",
        data: {
          kind: "organizer",
          layout: {
            theme: "light",
            density: "comfortable",
            show_weather: true,
          },
        },
      });
      toast("Preferences reset.");
      render();
    });
  document
    .querySelector("#organization-export")
    ?.addEventListener("click", () =>
      download("/organizations/export").catch((e) => toast(e.message, true)),
    );
  document
    .querySelector("#profile-form")
    ?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const result = await api("/auth/profile", {
        method: "POST",
        data: formObject(new FormData(e.target)),
      });
      identity.user = { ...identity.user, ...(result.user || result) };
      toast("Profile saved.");
    });
  document
    .querySelector("#mfa-setup")
    ?.addEventListener("click", () => setupMFA(false));
  document
    .querySelector("#mfa-replace")
    ?.addEventListener("click", () => setupMFA(true));
  document
    .querySelector("#mfa-recovery")
    ?.addEventListener("click", () => mfaAction("recovery-codes"));
  document
    .querySelector("#mfa-disable")
    ?.addEventListener("click", () => mfaAction("disable"));
  document
    .querySelector("#password-form")
    ?.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api("/auth/profile", {
          method: "POST",
          data: formObject(new FormData(e.target)),
        });
        e.target.reset();
        toast("Password changed.");
      } catch (err) {
        toast(err.message, true);
      }
    });
  document.querySelectorAll("[data-revoke-session]").forEach(
    (b) =>
      (b.onclick = async () => {
        if (
          await confirm(
            "Revoke signed-in device",
            "This device will need to sign in again.",
          )
        ) {
          await api(`/auth/sessions/${b.dataset.revokeSession}`, {
            method: "DELETE",
          });
          toast("Session revoked.");
          if (b.dataset.current === "true") location.href = "/login";
          else render();
        }
      }),
  );
}
function bindMatches() {
  document
    .querySelectorAll("[data-match]")
    .forEach((b) => (b.onclick = () => openMatch(b.dataset.match)));
}
function filterRecords() {
  const q = document.querySelector("#record-search")?.value.toLowerCase() || "",
    s = document.querySelector("#status-filter")?.value || "";
  document
    .querySelectorAll("[data-search]")
    .forEach(
      (c) =>
        (c.hidden =
          !c.dataset.search.includes(q) || (s && c.dataset.status !== s)),
    );
}
function filterMatches(d) {
  let ms = items(d.matches);
  const f = (id) => document.querySelector("#" + id)?.value || "";
  ms = ms.filter(
    (m) =>
      (!f("match-division") || m.division_id === f("match-division")) &&
      (!f("match-stage") || m.stage_id === f("match-stage")) &&
      (!f("match-status") || m.status === f("match-status")) &&
      (!f("schedule-tournament") ||
        m.tournament_id === f("schedule-tournament")) &&
      (!f("match-date") ||
        localDateTime(
          m.planned_at,
          tournament?.tournament.id === m.tournament_id
            ? tournament.tournament.timezone
            : items(dashboard?.tournaments).find(
                (t) => t.id === m.tournament_id,
              )?.timezone || org.timezone,
        ).slice(0, 10) === f("match-date")),
  );
  document.querySelector("#fixture-panel").innerHTML =
    f("schedule-layout") === "court"
      ? `<div class="panel-pad">${items(d.courts)
          .map(
            (c) =>
              `<h3>${esc(c.name)}</h3><div class="live-cards" style="padding:0;margin-bottom:20px">${
                ms
                  .filter((m) => m.court?.id === c.id)
                  .map((m) => organizerCard(m))
                  .join("") ||
                '<p class="muted small">No matching assignments.</p>'
              }</div>`,
          )
          .join(
            "",
          )}<h3>Court to be assigned</h3><div class="live-cards" style="padding:0">${ms
          .filter((m) => !m.court)
          .map((m) => organizerCard(m))
          .join("")}</div></div>`
      : fixtureTable(ms);
  bindMatches();
}
async function saveForm(e, path, transform = (v) => v) {
  e.preventDefault();
  try {
    const result = await api(path, {
      method: "PATCH",
      data: transform(formObject(new FormData(e.target))),
    });
    if (path === "/organizations/current") {
      org = { ...org, ...(result.organization || result) };
      identity.organizations = identity.organizations.map((item) =>
        item.id === org.id ? { ...item, ...org } : item,
      );
      applyBranding();
      shell();
    }
    toast("Changes saved.");
    render();
  } catch (err) {
    toast(err.message, true);
  }
}
async function createRecord(type) {
  if (type === "tournament") return newTournament();
  if (type === "membership") return inviteStaff();
  if (type === "organization")
    return modal(
      "Provision organization",
      field("name", "Organization name", "text", "", "required") +
        field("slug", "URL slug", "text", "", 'required pattern="[a-z0-9][a-z0-9-]{1,119}" maxlength="120"') +
        field(
          "timezone",
          "IANA timezone",
          "text",
          "America/New_York",
          "required",
        ) +
        field(
          "admin_email",
          "Organization administrator email",
          "email",
          "",
          "required",
        ) +
        field(
          "admin_name",
          "Organization administrator name",
          "text",
          "",
          'required maxlength="120"',
        ) +
        '<p class="form-help">A URL slug identifies the organization using lowercase letters, numbers, and hyphens, for example strive-volleyball.</p><p class="form-help">Use a separate organization staff account. Your platform account manages organization setup and service health. Share new organizers’ temporary sign-in details privately; existing organizers keep their current password.</p>',
      async (fd) => {
        const r = await api("/platform/organizations", {
          method: "POST",
          data: formObject(fd),
        });
        toast(
          r.message || "Organization created. Its administrator can accept the invitation after signing in.",
        );
        showInitialCredentials(r);
        await refreshIdentity();
        if (platformMode()) location.hash = "platform-organizations";
        await render();
      },
    );
  return recordForm(type);
}
async function editRecord(type, id) {
  const rows = items(
    await api(
      "/" +
        {
          team: "teams?archived=true&limit=500",
          venue: "venues?limit=500",
          court: "courts?limit=500",
        }[type],
    ),
  );
  recordForm(
    type,
    rows.find((r) => r.id === id),
  );
}
function closureFields(settings = {}) {
  const intervals = (settings.closures || []).filter(
    (closure) => closure.start && closure.end,
  );
  return `${textarea(
    "closed_dates",
    "Closed dates · one YYYY-MM-DD date per line",
    (settings.closures || [])
      .filter((closure) => closure.date)
      .map((closure) => closure.date)
      .join("\n"),
  )}<p class="form-help">These dates are interpreted in the venue’s timezone. Existing timed closure intervals are preserved.</p>${
    intervals.length
      ? table(
          [
            { key: "start", label: "Existing closure begins" },
            { key: "end", label: "Ends" },
          ],
          intervals,
        )
      : ""
  }`;
}
function openingHoursFields(settings = {}) {
  const days = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
  ];
  const hours = Object.keys(settings.opening_hours || {}).length
    ? settings.opening_hours
    : null;
  return `<h3 class="section-gap">Venue hours & closures</h3><label class="check-field"><input name="limit_opening_hours" type="checkbox" ${hours ? "checked" : ""}>Restrict assignments to weekly opening hours</label><div id="weekly-hours">${days
    .map((day, index) => {
      const daily = hours?.[String(index)] ?? hours?.default;
      return `<div class="form-grid availability-day"><label class="check-field full"><input type="checkbox" name="open_day_${index}" ${!hours || daily ? "checked" : ""}>${day} open</label>${field("open_" + index, day + " opens", "time", daily?.open || "08:00")}${field("close_" + index, day + " closes", "time", daily?.close || "22:00")}</div>`;
    })
    .join("")}</div>${closureFields(settings)}`;
}
function availabilitySettings(fd, previous = {}, venue = false) {
  const settings = { ...previous };
  const dates = String(fd.get("closed_dates") || "")
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean);
  for (const value of dates) {
    if (
      !/^\d{4}-\d{2}-\d{2}$/.test(value) ||
      Number.isNaN(Date.parse(value + "T12:00:00Z")) ||
      new Date(value + "T12:00:00Z").toISOString().slice(0, 10) !== value
    )
      throw new Error(
        "Closed dates must be valid calendar dates in YYYY-MM-DD format.",
      );
  }
  settings.closures = [
    ...(previous.closures || []).filter((closure) => !closure.date),
    ...[...new Set(dates)].map((date) => ({ date })),
  ];
  if (venue) {
    settings.opening_hours = {};
    if (fd.has("limit_opening_hours")) {
      settings.opening_hours = {};
      for (let index = 0; index < 7; index++) {
        if (!fd.has("open_day_" + index)) continue;
        const open = String(fd.get("open_" + index) || ""),
          close = String(fd.get("close_" + index) || "");
        if (!open || !close || open >= close)
          throw new Error(
            "Each open day needs an opening time earlier than its closing time.",
          );
        settings.opening_hours[String(index)] = { open, close };
      }
      if (!Object.keys(settings.opening_hours).length)
        throw new Error(
          "Select at least one open day, or turn off weekly hour restrictions.",
        );
    }
  }
  return settings;
}
async function recordForm(type, r = {}, context = {}) {
  const editing = !!r.id;
  let content;
  if (type === "team")
    content =
      field("name", "Team name", "text", r.name || "", "required") +
      field(
        "abbreviation",
        "Abbreviation",
        "text",
        r.abbreviation || "",
        'maxlength="20"',
      ) +
      field("color", "Team color", "color", r.color || "#2d8363") +
      field("logo", "Team logo URL", "url", r.logo || "") +
      field("email", "Private contact email", "email", r.contact?.email || "") +
      field("phone", "Private contact phone", "tel", r.contact?.phone || "") +
      `<label class="check-field"><input type="checkbox" name="archived" ${r.archived ? "checked" : ""}>Archive reusable team record</label><p class="form-help">Existing event entry names and historical results are preserved.</p>`;
  else if (type === "venue")
    content = `${editing ? '<p class="form-help">Changes update this saved venue and every tournament that uses it.</p>' : context.tournamentId ? '<p class="form-help">This venue will be added to the tournament and saved in your organization’s address book. Courts can be added later.</p>' : '<p class="form-help">Save this venue in your address book for use in tournaments.</p>'}<div class="form-grid">${field("name", "Venue name", "text", r.name || "", 'required maxlength="180"')}${field("timezone", "IANA timezone", "text", r.timezone || (context.tournamentId ? tournament?.tournament?.timezone : null) || org.timezone || "America/New_York", "required")}<div class="full">${addressFields(r.address || "")}</div></div><label class="check-field"><input name="indoor" type="checkbox" ${r.indoor !== false ? "checked" : ""}>Indoor venue</label>${openingHoursFields(r.settings)}`;
  else {
    const vs = context.venueId
      ? items(tournamentLocations?.items).filter((venue) => venue.id === context.venueId)
      : items(await api("/venues"));
    content =
      field("name", "Court name", "text", r.name || "", "required") +
      (context.venueId ? `<p class="form-help">Venue: ${esc(vs[0]?.name || "Selected tournament venue")}</p><input type="hidden" name="venue_id" value="${esc(context.venueId)}">` : select("venue_id", "Venue", options(vs), r.venue_id || "", "required")) +
      `<label class="check-field"><input name="available" type="checkbox" ${r.available !== false ? "checked" : ""}>Court available for eligible assignments</label>` +
      closureFields(r.settings) +
      '<p class="form-help">Operational occupancy is derived by the server. Changing availability does not release active matches.</p>';
  }
  const path = context.tournamentId && !editing
    ? type === "venue" ? `/tournaments/${context.tournamentId}/venues` : `/tournaments/${context.tournamentId}/venues/${context.venueId}/courts`
    : "/" + { team: "teams", venue: "venues", court: "courts" }[type];
  modal(
    `${editing ? "Edit" : "Add"} ${type}`,
    content,
    async (fd) => {
      let data = formObject(
        fd,
        [],
        ["archived", "indoor", "available"],
      );
      if (type === "team") {
        data.contact = { email: data.email, phone: data.phone };
        delete data.email;
        delete data.phone;
        delete data.indoor;
        delete data.available;
      }
      if (type === "venue") {
        const availability = availabilitySettings(fd, r.settings, true);
        data.settings = Object.fromEntries(Object.entries(availability).filter(([key]) => ["opening_hours", "closures", "layout_url", "reservation_minutes"].includes(key)));
        delete data.archived;
        delete data.available;
      }
      if (type === "court") {
        data.settings = availabilitySettings(fd, r.settings);
        delete data.archived;
        delete data.indoor;
      }
      delete data.closed_dates;
      delete data.limit_opening_hours;
      for (let index = 0; index < 7; index++) {
        delete data["open_day_" + index];
        delete data["open_" + index];
        delete data["close_" + index];
      }
      await api(path + (editing ? "/" + r.id : ""), {
        method: editing ? "PATCH" : "POST",
        data: context.tournamentId && type === "venue" && !editing ? { venue: data } : data,
      });
      toast(`${pretty(type)} saved.`);
      render();
    },
    { wide: type === "venue" },
  );
  if (type === "venue") {
    bindAddressSearch(document.querySelector("#modal-form"), { tournamentId: context.tournamentId || (route().startsWith("tournaments/") ? tournament?.tournament.id : undefined) });
    const enabled = document.querySelector("[name=limit_opening_hours]"),
      hours = document.querySelector("#weekly-hours");
    const update = () => {
      hours.hidden = !enabled.checked;
      hours
        .querySelectorAll("input")
        .forEach((input) => (input.disabled = !enabled.checked));
    };
    enabled.addEventListener("change", update);
    update();
  }
}
function tournamentAssignments(ts, selected = []) {
  return `<h3 class="section-gap">Tournament assignments</h3><p class="form-help">Leave all unchecked for all tournaments permitted by the role.</p>${ts.map((t) => `<label class="check-field"><input name="tournament_ids" type="checkbox" value="${esc(t.id)}" ${selected.includes(t.id) ? "checked" : ""}>${esc(t.name)}</label>`).join("")}`;
}
async function inviteStaff() {
  const ts = items(await api("/tournaments"));
  modal(
    "Invite organizer",
    field("email", "Organizer email", "email", "", "required") +
      field("name", "Name") +
      select(
        "role",
        "Organization role",
        ["admin", "manager", "scheduler", "results", "viewer"],
        "viewer",
      ) +
      tournamentAssignments(ts) +
      '<p class="form-help">New organizers receive temporary sign-in details to share privately. Existing organizers keep their current password and other memberships. They accept this organization invitation after signing in.</p>',
    async (fd) => {
      const v = formObject(fd);
      v.tournament_ids = fd.getAll("tournament_ids");
      const r = await api("/memberships", { method: "POST", data: v });
      toast(
        r.message || "Organizer invitation created. The organizer can accept it after signing in.",
      );
      showInitialCredentials(r);
      await render();
    },
    { submit: "Invite organizer" },
  );
}
async function editMember(id) {
  const ms = items(await api("/memberships")),
    m = ms.find((x) => x.id === id),
    ts = items(await api("/tournaments"));
  modal(
    "Edit organizer access",
    select(
      "role",
      "Organization role",
      ["admin", "manager", "scheduler", "results", "viewer"],
      m.role,
    ) +
      select(
        "status",
        "Membership state",
        ["active", "suspended", "removed"],
        m.status,
      ) +
      tournamentAssignments(ts, m.tournament_ids || []) +
      textarea("reason", "Reason for access change", "", "required") +
      '<p class="form-help">Removing this membership preserves the global identity and other organization access.</p>',
    async (fd) => {
      const v = formObject(fd);
      v.tournament_ids = fd.getAll("tournament_ids");
      await api(`/memberships/${id}`, { method: "PATCH", data: v });
      toast("Organization membership updated.");
      render();
    },
  );
}
async function editEvent() {
  const t = tournament.tournament;
  modal(
    "Competition details",
    `<div class="form-grid">${field("name", "Competition name", "text", t.name, "required")}${select("competition_type", "Type", ["tournament", "league"], t.competition_type, "disabled")}${field("start_date", "Start date", "date", t.start_date, "required")}${field("end_date", "End date", "date", t.end_date, "required")}${field("timezone", "IANA timezone", "text", t.timezone, "required")}<div class="full">${textarea("description", "Description", t.description)}</div>${field("rest_minutes", "Minimum rest between a team’s games (minutes)", "number", t.settings?.rest_minutes ?? 0, 'min="0" max="1440" required')}${field("buffer_minutes", "Court turnaround buffer (minutes)", "number", t.settings?.buffer_minutes ?? 0, 'min="0" max="1440" required')}<div class="full"><p class="form-help">Add or update venue and court details from this tournament’s Venues & courts tab. These details can be added later.</p><label class="check-field"><input type="checkbox" name="public_rules" ${t.settings?.public_rules !== false ? "checked" : ""}>Show scoring rules on the public tournament page</label><p class="form-help">Schedule checks use the rest and turnaround policies. Fixture duration and slot spacing are set for each stage. Dates and fixture times use the event timezone.</p></div></div>`,
    async (fd) => {
      const v = formObject(
        fd,
        ["rest_minutes", "buffer_minutes"],
        ["public_rules"],
      );
      v.settings = {
        ...t.settings,
        rest_minutes: v.rest_minutes,
        buffer_minutes: v.buffer_minutes,
        public_rules: v.public_rules,
      };
      delete v.rest_minutes;
      delete v.buffer_minutes;
      delete v.public_rules;
      await api(`/tournaments/${t.id}`, { method: "PATCH", data: v });
      toast("Competition details saved.");
      render();
    },
    { wide: true },
  );
}
async function addEntry(divisionId) {
  const ts = items(await api("/teams")).filter(
    (t) =>
      !t.archived &&
      !items(tournament.entries).some(
        (e) => e.team_id === t.id && e.division_id === divisionId,
      ),
  );
  modal(
    "Add team entry",
    select("team_id", "Reusable team", options(ts), "", "required") +
      field(
        "seed",
        "Seed",
        "number",
        items(tournament.entries).filter((e) => e.division_id === divisionId)
          .length + 1,
        'min="1" required',
      ) +
      field("pool", "Optional pool label") +
      textarea(
        "roster_names",
        "Private event roster · one player name per line",
        "",
      ) +
      '<p class="form-help">This event records an independent name and color snapshot. Rosters remain private.</p>',
    async (fd) => {
      const v = formObject(fd, ["seed"]);
      v.roster = parseRoster(v.roster_names);
      delete v.roster_names;
      await api(`/divisions/${divisionId}/entries`, {
        method: "POST",
        data: v,
      });
      toast("Team entered in division.");
      render();
    },
  );
}
const stageFormats = [
  ["round_robin", "Round robin"],
  ["pools_knockout", "Pools followed by knockout playoffs"],
  ["single_elimination", "Single elimination"],
  ["league", "Recurring league fixtures"],
];
const stageNumericKeys = [
  "repeat",
  "pool_count",
  "qualify_per_pool",
  "qualifier_count",
  "wildcard_places",
  "interval_minutes",
  "duration_minutes",
];
function rosterNames(roster = []) {
  return roster
    .map((player) => (typeof player === "string" ? player : player.name || ""))
    .filter(Boolean)
    .join("\n");
}
function parseRoster(text, previous = []) {
  return String(text || "")
    .split(/\r?\n/)
    .map((name) => name.trim())
    .filter(Boolean)
    .map(
      (name) =>
        previous.find(
          (player) => typeof player === "object" && player?.name === name,
        ) || name,
    );
}
function qualificationSources(stage) {
  return items(tournament.stages).filter(
    (candidate) =>
      candidate.division_id === stage.division_id &&
      candidate.id !== stage.id &&
      candidate.position < stage.position &&
      (["round_robin", "league"].includes(candidate.format) ||
        (candidate.format === "pools_knockout" &&
          candidate.id === stage.settings?.source_stage_id)),
  );
}
function stageSettingsFields(stage, generation = false) {
  const settings = stage.settings || {},
    entries = items(tournament.entries).filter(
      (e) => e.division_id === stage.division_id && e.status === "active",
    ).length;
  const sources = qualificationSources(stage),
    source = sources.find((s) => s.id === settings.source_stage_id);
  const poolCount =
    settings.pool_count ?? Math.max(1, Math.min(2, Math.floor(entries / 2)));
  return `<div class="form-grid stage-options" data-stage-format="${esc(stage.format || "round_robin")}" data-source-format="${esc(source?.format || "")}">
  ${!generation ? `<div class="full" data-stage-option="source">${select("source_stage_id", "Qualification source", [["", "Seed this bracket directly from division teams"], ...options(sources)], settings.source_stage_id || "")}<p class="form-help">An earlier stage in this division can supply official ranked qualifiers. Pending places stay TBD until results and ties are resolved.</p></div>` : ""}
  <div data-stage-option="repeat">${field("repeat", "Encounters per team pairing", "number", settings.repeat ?? 1, 'min="1" max="10" required')}</div>
  <div data-stage-option="pools">${field("pool_count", "Number of pools", "number", poolCount, `min="1" max="${Math.max(1, Math.min(16, Math.floor(entries / 2)))}" required`)}</div>
  <div data-stage-option="pool-qualifiers">${field("qualify_per_pool", "Automatic qualifying places per pool", "number", settings.qualify_per_pool ?? Math.max(1, Math.min(2, Math.floor(entries / poolCount))), 'min="1" max="128" required')}</div>
  <div data-stage-option="ranked-qualifiers">${field("qualifier_count", "Top ranked teams advancing to playoffs", "number", settings.qualifier_count ?? Math.max(2, Math.min(4, entries)), `min="2" max="128" required ${generation ? "readonly" : ""}`)}</div>
  <div data-stage-option="wildcards">${field("wildcard_places", "Additional wildcard places across pools", "number", settings.wildcard_places ?? 0, 'min="0" max="128" required')}</div>
  ${field("interval_minutes", "Fixture slot interval (minutes)", "number", settings.interval_minutes ?? 75, 'min="1" max="10080" required')}
  ${field("duration_minutes", "Planned game duration (minutes)", "number", settings.duration_minutes ?? 60, 'min="1" max="1440" required')}
  <p class="form-help full">Review generated fixtures before saving. Tournament rest and court turnaround policies are configured in Competition details. ${stage.format === "pools_knockout" ? "Pools automatically create a linked knockout playoff stage." : ""}</p></div>`;
}
function bindStageOptions(stage, generation = false) {
  const form = document.querySelector("#modal-form"),
    format = form.querySelector("[name=format]"),
    position = form.querySelector("[name=position]"),
    source = form.querySelector("[name=source_stage_id]");
  const update = () => {
    const currentFormat = format?.value || stage.format,
      currentPosition = Number(position?.value || stage.position);
    if (source) {
      for (const option of source.options) {
        if (!option.value) continue;
        const candidate = items(tournament.stages).find(
          (s) => s.id === option.value,
        );
        option.disabled = candidate.position >= currentPosition;
      }
      if (source.selectedOptions[0]?.disabled) source.value = "";
    }
    const sourceId = source?.value ?? stage.settings?.source_stage_id;
    const sourceFormat = items(tournament.stages).find(
      (s) => s.id === sourceId,
    )?.format;
    const pools = currentFormat === "pools_knockout",
      bracketSource = currentFormat === "single_elimination" && !!sourceId;
    const visible = {
      source: currentFormat === "single_elimination",
      repeat: ["round_robin", "league", "pools_knockout"].includes(
        currentFormat,
      ),
      pools,
      "pool-qualifiers":
        pools || (bracketSource && sourceFormat === "pools_knockout"),
      "ranked-qualifiers":
        bracketSource && ["round_robin", "league"].includes(sourceFormat),
      wildcards: pools || (bracketSource && sourceFormat === "pools_knockout"),
    };
    form.querySelectorAll("[data-stage-option]").forEach((group) => {
      group.hidden = !visible[group.dataset.stageOption];
      group
        .querySelectorAll("input,select")
        .forEach((control) => (control.disabled = group.hidden));
    });
  };
  format?.addEventListener("change", update);
  position?.addEventListener("input", update);
  source?.addEventListener("change", update);
  update();
}
function stageForm(stage) {
  return `<div class="form-grid">${field("name", "Stage name", "text", stage.name || "", "required")}${select("format", "Competition format", stageFormats, stage.format || "round_robin")}${field("position", "Stage order", "number", stage.position, 'min="1" max="100" required')}</div><h3>Qualification & scheduling</h3>${stageSettingsFields(stage)}`;
}
function stageValues(fd, previous = {}) {
  const values = formObject(fd, ["position", ...stageNumericKeys]);
  values.settings = { ...previous };
  for (const key of stageNumericKeys) {
    if (key in values) values.settings[key] = values[key];
    delete values[key];
  }
  values.settings.source_stage_id =
    values.format === "single_elimination"
      ? values.source_stage_id || null
      : null;
  delete values.source_stage_id;
  return values;
}
function addStage(divisionId) {
  const stage = {
    division_id: divisionId,
    format: "round_robin",
    position:
      Math.max(
        0,
        ...items(tournament.stages)
          .filter((s) => s.division_id === divisionId)
          .map((s) => s.position),
      ) + 1,
    settings: {},
  };
  modal(
    "Add competition stage",
    stageForm(stage),
    async (fd) => {
      const v = stageValues(fd);
      await api(`/divisions/${divisionId}/stages`, { method: "POST", data: v });
      toast("Competition stage saved.");
      render();
    },
    { wide: true },
  );
  bindStageOptions(stage);
}
function editRules(path, overrides = {}) {
  const defaults = {
    mode: "best_of",
    best_of: 3,
    fixed_sets: 3,
    sets_to_win: 2,
    regular_target: 25,
    deciding_target: 15,
    win_by: 2,
    cap: null,
    timeouts: 2,
    draw_allowed: false,
    win_points: 2,
    loss_points: 0,
    draw_points: 1,
    forfeit_win_points: 2,
    forfeit_loss_points: 0,
    include_forfeit_scores: false,
    tie_breaks: [
      "standings_points",
      "won",
      "head_to_head",
      "set_quotient",
      "point_quotient",
    ],
    head_to_head: "two_team",
    restart_tie_breaks: true,
    quotient_zero: "infinity",
    cross_pool: "points_per_match",
    automatic_approval: false,
  };
  const scopeType = path.startsWith("/stages/")
    ? "stage"
    : path.startsWith("/divisions/")
      ? "division"
      : "tournament";
  const scopeStage =
    scopeType === "stage"
      ? items(tournament.stages).find(
          (stage) => stage.id === path.split("/").pop(),
        )
      : null;
  const scopeDivision = scopeStage
    ? items(tournament.divisions).find(
        (division) => division.id === scopeStage.division_id,
      )
    : null;
  const r = {
    ...defaults,
    ...tournament.tournament.rules,
    ...(scopeDivision?.rules || {}),
    ...overrides,
  };
  modal(
    "Scoring & ranking policy",
    `<p class="muted small">Editing the ${scopeType} policy. These fields include inherited defaults; saving records the values shown for this scope. Started games retain their frozen rules. Review scoring and ranking before official play.</p><h3>Match scoring</h3><div class="form-grid">${select(
      "mode",
      "Set format",
      [
        ["best_of", "Best of N sets"],
        ["fixed", "Fixed number of sets"],
      ],
      r.mode,
    )}${field("best_of", "Best of N", "number", r.best_of, 'min="1" max="9"')}${field("fixed_sets", "Fixed set count", "number", r.fixed_sets, 'min="1" max="9"')}${field("sets_to_win", "Sets needed to win", "number", r.sets_to_win, 'min="1"')}${field("regular_target", "Regular set target", "number", r.regular_target, 'min="1"')}${field("deciding_target", "Deciding set target", "number", r.deciding_target, 'min="1"')}${field("win_by", "Lead required to win set", "number", r.win_by, 'min="1"')}${field("cap", "Hard cap (empty = none)", "number", r.cap ?? "", 'min="1"')}${field("timeouts", "Timeouts per team per set", "number", r.timeouts, 'min="0" max="10"')}</div><label class="check-field"><input type="checkbox" name="draw_allowed" ${r.draw_allowed ? "checked" : ""}>Allow drawn fixed-set matches</label><p class="form-help">At a hard cap, the team reaching the cap wins even without the regular margin.</p><h3 class="section-gap">Standings points</h3><div class="form-grid">${["win_points", "loss_points", "draw_points", "forfeit_win_points", "forfeit_loss_points"].map((k) => field(k, pretty(k), "number", r[k], 'min="0"')).join("")}${select("head_to_head", "Head-to-head scope", ["two_team", "mini_table"], r.head_to_head)}${select("quotient_zero", "Zero denominator quotient", ["infinity", "zero"], r.quotient_zero)}${select("cross_pool", "Cross-pool comparison", ["points_per_match", "total_points"], r.cross_pool)}</div>${field("tie_breaks", "Ordered tie breaks (comma separated)", "text", r.tie_breaks.join(", "), "required")}<label class="check-field"><input name="restart_tie_breaks" type="checkbox" ${r.restart_tie_breaks ? "checked" : ""}>Restart criteria after a tied group splits</label><label class="check-field"><input name="include_forfeit_scores" type="checkbox" ${r.include_forfeit_scores ? "checked" : ""}>Include configured forfeit scores in statistics</label><label class="check-field"><input name="automatic_approval" type="checkbox" ${r.automatic_approval ? "checked" : ""}>Automatically finalize valid submitted results</label>`,
    async (fd) => {
      const v = formObject(
        fd,
        [
          "best_of",
          "fixed_sets",
          "sets_to_win",
          "regular_target",
          "deciding_target",
          "win_by",
          "cap",
          "timeouts",
          "win_points",
          "loss_points",
          "draw_points",
          "forfeit_win_points",
          "forfeit_loss_points",
        ],
        [
          "draw_allowed",
          "restart_tie_breaks",
          "include_forfeit_scores",
          "automatic_approval",
        ],
      );
      v.tie_breaks = v.tie_breaks
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      try {
        await api(path, { method: "PATCH", data: { rules: v } });
        toast("Policy saved. Active game snapshots are preserved.");
        render();
      } catch (e) {
        if (e.code !== "ranking_review_required") throw e;
        const [, plural, scope_id] = path.split("/"),
          scope_type = plural.slice(0, -1),
          impact = await api("/rules/preview", {
            method: "POST",
            data: { scope_type, scope_id, rules: v },
          });
        setTimeout(
          () =>
            modal(
              "Review historical ranking impact",
              rankingImpactPreview(impact) +
                textarea(
                  "reason",
                  "Required policy change reason",
                  "",
                  "required",
                ) +
                '<label class="check-field"><input type="checkbox" name="reviewed" required>I reviewed the historical ranking impact</label>',
              async (fd) => {
                await api(path, {
                  method: "PATCH",
                  data: {
                    rules: v,
                    recalculate_history: true,
                    reason: fd.get("reason"),
                  },
                });
                toast(
                  "Reviewed ranking policy saved and historical standings recalculated.",
                );
                render();
              },
              { submit: "Apply reviewed policy", wide: true },
            ),
          0,
        );
      }
    },
    { wide: true },
  );
}
function generateFixtures(stageId) {
  const stage = items(tournament.stages).find((s) => s.id === stageId);
  modal(
    "Preview fixture generation",
    field(
      "start_at",
      "First fixture time (" + tournament.tournament.timezone + ")",
      "datetime-local",
      `${tournament.tournament.start_date}T09:00`,
      "required",
    ) +
      `<h3>${esc(stage.name)} · ${esc(pretty(stage.format))}</h3>` +
      stageSettingsFields(stage, true),
    async (fd) => {
      const data = formObject(fd, stageNumericKeys);
      data.start_at = zonedISO(data.start_at, tournament.tournament.timezone);
      const p = await api(`/stages/${stageId}/generate`, {
        method: "POST",
        data: { ...data, preview: true },
      });
      setTimeout(
        () =>
          modal(
            "Review generated fixtures",
            generationPreview(p),
            async () => {
              await api(`/stages/${stageId}/generate`, {
                method: "POST",
                data: { ...data, preview: false },
              });
              toast("Generated fixtures saved.");
              render();
            },
            { submit: "Commit reviewed fixtures", wide: true },
          ),
        0,
      );
    },
    { submit: "Generate preview", wide: true },
  );
  bindStageOptions(stage, true);
}
function manualMatch() {
  modal(
    "Create fixture",
    select(
      "division_id",
      "Division",
      options(items(tournament.divisions)),
      "",
      "required",
    ) +
      select(
        "stage_id",
        "Stage",
        options(items(tournament.stages)),
        "",
        "required",
      ) +
      select(
        "entry_a_id",
        "Team A entry",
        options(items(tournament.entries)),
        "",
        "required",
      ) +
      select(
        "entry_b_id",
        "Team B entry",
        options(items(tournament.entries)),
        "",
        "required",
      ) +
      field(
        "planned_at",
        "Planned time (" + tournament.tournament.timezone + ")",
        "datetime-local",
        "",
        "required",
      ) +
      field(
        "planned_duration",
        "Estimated duration (minutes)",
        "number",
        60,
        'min="1"',
      ) +
      field("round_number", "Round", "number", 1, 'min="1"'),
    async (fd) => {
      const v = formObject(fd, ["planned_duration", "round_number"]);
      v.tournament_id = tournament.tournament.id;
      v.planned_at = zonedISO(
        v.planned_at,
        tournament?.tournament.timezone || org.timezone,
      );
      await api("/matches", { method: "POST", data: v });
      toast("Fixture created. Assign a court when ready.");
      render();
    },
  );
  bindFixtureSelections();
}
async function validateEvent(id) {
  const node = document.querySelector("#validation-result");
  try {
    const v = await api(`/tournaments/${id}/validate`, {
      method: "POST",
      data: {},
    });
    node.innerHTML = `<div class="banner ${v.valid === false ? "danger" : ""}"><strong>${esc(v.valid === false ? "Resolve validation errors before activation." : "Setup validation passed. Review scoring defaults and visibility before publishing.")}</strong>${v.errors?.length ? `<ul>${v.errors.map((error) => `<li>${esc(typeof error === "string" ? error : error.message || pretty(error.code))}</li>`).join("")}</ul>` : ""}</div>`;
  } catch (e) {
    node.innerHTML = `<div class="banner danger">${errorText(e)}</div>`;
  }
}
let activeMatchId;
function hideQR() {
  qrGeneration += 1;
  clearInterval(qrTimer);
  qrDeadline = 0;
  const q = document.querySelector("#qr-content");
  if (q) {
    q.replaceChildren();
    q.hidden = true;
  }
  const message = document.querySelector("#qr-message");
  if (message) {
    message.replaceChildren();
    message.hidden = true;
  }
}
async function openMatch(id, { notice = "", expectedForm } = {}) {
  const routeAtOpen = location.hash;
  const originalFormCurrent = () => location.hash === routeAtOpen && (!expectedForm || (expectedForm.isConnected && document.querySelector("#dialog").open && document.querySelector("#dialog form") === expectedForm));
  if (!originalFormCurrent()) return;
  hideQR();
  activeMatchId = id;
  const detail = await api(`/matches/${id}`);
  const m = detail.match || detail;
  if (activeMatchId !== id || !originalFormCurrent()) return;
  const courts = await api(`/courts?tournament_id=${encodeURIComponent(m.tournament_id)}`);
  if (activeMatchId !== id || !originalFormCurrent()) return;
  const eligibleCourts = items(courts).filter((court) => court.status === "available" || court.id === m.court?.id);
  if (m.court && !eligibleCourts.some((court) => court.id === m.court.id)) eligibleCourts.push(m.court);
  const matchTimezone =
    tournament?.tournament.id === m.tournament_id
      ? tournament.tournament.timezone
      : items(dashboard?.tournaments).find((t) => t.id === m.tournament_id)
          ?.timezone || org.timezone;
  modal(
    "Game preparation & result review",
    `<div class="record-top">${badge(m.status)}${badge(m.result_status)}<span class="muted small">Version ${m.version}</span></div><h2 class="section-gap">${esc(teamName(m, "a"))} <span class="muted">vs</span> ${esc(teamName(m, "b"))}</h2><dl class="detail-list"><div><dt>Court</dt><dd>${esc(courtName(m))}</dd></div><div><dt>Planned time</dt><dd>${esc(dt(m.planned_at, { timeZone: tournament?.tournament.id === m.tournament_id ? tournament.tournament.timezone : items(dashboard?.tournaments).find((t) => t.id === m.tournament_id)?.timezone || org?.timezone }))}</dd></div><div><dt>Set scores</dt><dd>${esc(score(m))}</dd></div><div><dt>Referee</dt><dd>${esc(m.scorer?.name || m.referee_name || "No unverified referee name")}${m.scorer ? '<div class="muted small">' + (m.scorer.connected ? "Connected" : "Disconnected — game remains active") + " · " + esc(dt(m.scorer.last_seen_at)) + "</div>" : ""}</dd></div></dl>${rulesSummary(m.rules)}${can("manage") && !m.rule_snapshot ? '<button type="button" class="button secondary section-gap" id="match-rule-override">Override game scoring rules</button>' : ""}${can("schedule") ? `<div class="section-gap"><h3>Court assignment</h3><div id="court-message" class="form-help" role="status" ${notice ? "" : "hidden"}>${esc(notice)}</div>${!eligibleCourts.length ? `<p class="form-help">${items(courts).length ? "No tournament courts are currently available. Review court availability or release an unused reservation." : "Add a venue and court in this tournament first, from its Venues & courts tab."}</p>` : ""}<div class="filters">${select("court_id", "Eligible court", options(eligibleCourts), m.court?.id || "", 'id="assign-court"' + (eligibleCourts.length ? "" : " disabled"))}<button type="button" class="button secondary" id="assign-court-action" ${eligibleCourts.length ? "" : "disabled"}>Assign court</button>${m.status === "ready" ? '<button type="button" class="button ghost" id="release-court">Release reservation</button>' : ""}</div></div>` : ""}${can("manage") ? `<div class="section-gap"><h3>Delegated referee access</h3><p class="muted small">Assign a court, then show this game's referee QR for 10 seconds. Hiding the QR does not end a referee's scoring session.</p><div class="button-group"><button type="button" class="button primary" id="show-qr">Show QR Code</button><button type="button" class="button secondary" id="revoke-referee">Revoke referee access</button></div><div class="form-error" id="qr-message" role="alert" hidden></div><div class="qr-box" id="qr-content" role="region" aria-label="Private referee QR code" hidden></div></div>` : ""}${can("results") && m.status === "ended" ? `<div class="section-gap"><h3>Result review</h3><p class="muted small">${esc(score(m))} · ${esc(pretty(m.outcome || "played"))} · ${esc(pretty(m.result_status))}</p><div class="button-group">${m.result_status === "submitted" ? '<button type="button" class="button primary" id="approve-result">Approve official result</button>' : ""}${!m.winner_id && m.team_a && m.team_b ? '<button type="button" class="button secondary" id="resolve-winner">Resolve winner</button>' : ""}<button type="button" class="button secondary" id="correct-result">Correct with revision</button></div></div>` : ""}${can("results") || can("schedule") ? `<div class="section-gap"><h3>Game operations</h3><div class="button-group">${can("results") && m.result_status !== "finalized" ? '<button type="button" class="button secondary" id="match-outcome">Record exceptional outcome</button>' : ""}${can("schedule") ? '<button type="button" class="button secondary" id="edit-fixture">Revise fixture</button>' : ""}</div></div>` : ""}`,
    null,
    { wide: true },
  );
  const dialog = document.querySelector("#dialog");
  if (notice) dialog.querySelector("#court-message")?.scrollIntoView({ block: "nearest" });
  const onMatchClose = () => {
    if (dialog.open) return;
    dialog.removeEventListener("close", onMatchClose);
    hideQR();
    if (activeMatchId === id) activeMatchId = null;
  };
  dialog.addEventListener("close", onMatchClose);
  document
    .querySelector("#match-rule-override")
    ?.addEventListener("click", () => editMatchRules(m));
  document
    .querySelector("#show-qr")
    ?.addEventListener("click", () => revealQR(id));
  document
    .querySelector("#assign-court-action")
    ?.addEventListener("click", async () => {
      const form = dialog.querySelector("form"),
        button = form.querySelector("#assign-court-action"),
        picker = form.querySelector("#assign-court"),
        qrButton = form.querySelector("#show-qr"),
        message = form.querySelector("#court-message"),
        selectedCourt = picker.value,
        routeAtAssignment = location.hash;
      const current = () => dialog.open && form.isConnected && dialog.querySelector("form") === form && activeMatchId === id && location.hash === routeAtAssignment;
      hideQR();
      if (["in_progress", "paused"].includes(m.status)) {
        const routeAtMove = location.hash;
        const ok = await confirm(
          "Move active game",
          "Moving a game preserves scores and timing and atomically checks court availability.",
        );
        if (location.hash !== routeAtMove) return;
        if (!ok) {
          await openMatch(id);
          return;
        }
        let moveForm;
        modal(
          "Reason for court move",
          textarea("reason", "Required reason", "", "required"),
          async (fd) => {
            await api(`/matches/${id}/court`, {
              method: "POST",
              data: { court_id: selectedCourt, reason: fd.get("reason") },
            });
            if (!dialog.open || dialog.querySelector("form") !== moveForm || location.hash !== routeAtMove) return;
            await openMatch(id, { notice: "Court assignment updated. The game's scores and timing are preserved.", expectedForm: moveForm });
          },
        );
        moveForm = dialog.querySelector("form");
        return;
      }
      button.disabled = true;
      picker.disabled = true;
      if (qrButton) qrButton.disabled = true;
      message.textContent = "Assigning court…";
      message.className = "form-help";
      message.setAttribute("role", "status");
      message.hidden = false;
      try {
        await api(`/matches/${id}/court`, {
          method: "POST",
          data: { court_id: selectedCourt },
        });
        if (!current()) return;
        await openMatch(id, { notice: "Court assigned. Choose Show QR Code below when the referee is ready to scan.", expectedForm: form });
      } catch (error) {
        if (!current()) return;
        message.textContent = error.message;
        message.className = "form-error";
        message.setAttribute("role", "alert");
        message.hidden = false;
        message.scrollIntoView({ block: "nearest" });
      } finally {
        if (button.isConnected) button.disabled = false;
        if (picker.isConnected) picker.disabled = false;
        if (qrButton?.isConnected) qrButton.disabled = false;
      }
    });
  document
    .querySelector("#release-court")
    ?.addEventListener("click", () =>
      matchMutation(id, "release", {}, "Court reservation released."),
    );
  document
    .querySelector("#revoke-referee")
    ?.addEventListener("click", async () => {
      if (
        await confirm(
          "Revoke referee access",
          "Revoke admission and scorer access for this game? The game and court remain active.",
        )
      )
        matchMutation(id, "revoke", {}, "Referee access revoked.");
    });
  document
    .querySelector("#approve-result")
    ?.addEventListener("click", async () => {
      if (
        await confirm(
          "Approve official result",
          `Finalize ${score(m)}? Standings and qualifiers will update from this result.`,
        )
      )
        matchMutation(id, "approve", {}, "Official result approved.");
    });
  document.querySelector("#resolve-winner")?.addEventListener("click", () =>
    modal(
      "Resolve match winner",
      select("winner_id", "Adjudicated winner", [
        [m.team_a.id, teamName(m, "a")],
        [m.team_b.id, teamName(m, "b")],
      ]) +
        textarea("reason", "Adjudication reason", "", "required") +
        '<p class="form-help">Preserves set scores and records a new result revision before knockout advancement.</p>',
      async (fd) => {
        await api(`/matches/${id}/resolve-winner`, {
          method: "POST",
          data: formObject(fd),
        });
        toast("Audited winner resolution recorded.");
        render();
      },
    ),
  );
  document
    .querySelector("#correct-result")
    ?.addEventListener("click", () => correctResult(m));
  document.querySelector("#match-outcome")?.addEventListener("click", () => {
    modal(
      "Record exceptional outcome",
      select("outcome", "Outcome", [
        ...(m.team_a && m.team_b
          ? [["forfeit", "Forfeit with an explicit winner"]]
          : []),
        ["abandoned", "Abandoned without a result"],
        ["cancelled", "Cancelled without a result"],
      ]) +
        `<div id="outcome-winner">${select(
          "winner_id",
          "Forfeit winning team",
          [
            [m.team_a?.id, teamName(m, "a")],
            [m.team_b?.id, teamName(m, "b")],
          ].filter(([id]) => id),
          "",
          "required",
        )}</div>` +
        textarea("reason", "Required reason", "", "required") +
        '<p class="form-help">An explicit outcome stops the match and releases its court. A forfeit records the winner without inventing rally or set scores.</p>',
      async (fd) => {
        const data = formObject(fd);
        if (data.outcome !== "forfeit") delete data.winner_id;
        await api(`/matches/${id}/outcome`, {
          method: "POST",
          data,
        });
        toast("Audited outcome recorded.");
        render();
      },
    );
    const outcome = document.querySelector("[name=outcome]"),
      winner = document.querySelector("#outcome-winner");
    const update = () => {
      winner.hidden = outcome.value !== "forfeit";
      winner.querySelector("select").disabled = winner.hidden;
    };
    outcome.addEventListener("change", update);
    update();
  });
  document.querySelector("#edit-fixture")?.addEventListener("click", () =>
    modal(
      "Revise fixture",
      field(
        "planned_at",
        "Revised planned time",
        "datetime-local",
        localDateTime(m.planned_at, matchTimezone),
      ) +
        field(
          "planned_duration",
          "Planned duration (minutes)",
          "number",
          m.planned_duration || 60,
          'min="1"',
        ) +
        field(
          "referee_name",
          "Unverified referee name",
          "text",
          m.referee_name || "",
        ) +
        (["scheduled", "postponed"].includes(m.status)
          ? select(
              "status",
              "Scheduling state",
              [
                ["scheduled", "Scheduled"],
                ["postponed", "Postponed"],
              ],
              m.status,
            )
          : "") +
        textarea("reason", "Reason for revision", "", "required"),
      async (fd) => {
        const v = formObject(fd, ["planned_duration"]);
        if (v.planned_at) v.planned_at = zonedISO(v.planned_at, matchTimezone);
        else v.planned_at = null;
        await api(`/matches/${id}`, { method: "PATCH", data: v });
        toast("Fixture revision saved.");
        render();
      },
    ),
  );
}
async function revealQR(id) {
  hideQR();
  const dialog = document.querySelector("#dialog");
  const button = dialog?.querySelector("#show-qr");
  const q = dialog?.querySelector("#qr-content");
  const message = dialog?.querySelector("#qr-message");
  if (!dialog?.open || !button || !q || !message || activeMatchId !== id)
    return;
  const generation = qrGeneration;
  const current = () =>
    generation === qrGeneration &&
    !document.hidden &&
    dialog.open &&
    activeMatchId === id &&
    dialog.querySelector("#show-qr") === button &&
    dialog.querySelector("#qr-content") === q;
  button.disabled = true;
  try {
    const grant = await api(`/matches/${id}/qr`, { method: "POST", data: {} });
    if (!current()) return;
    const parsed = new DOMParser().parseFromString(
      grant.qr_svg,
      "image/svg+xml",
    );
    const svg = parsed.documentElement;
    if (
      svg.localName === "svg" &&
      !svg.querySelector("script,foreignObject,image,use,a") &&
      ![...svg.querySelectorAll("*")].some((n) =>
        [...n.attributes].some(
          (a) => a.name.startsWith("on") || a.name.endsWith("href"),
        ),
      )
    ) {
      qrDeadline = performance.now() + 10000;
      q.innerHTML = `<div class="qr-count"><span id="qr-seconds">10</span> <small>seconds remaining</small></div><div id="qr-image"></div><p class="small">Private game access · Keep this screen visible while the referee scans.</p>`;
      q.querySelector("#qr-image").append(document.importNode(svg, true));
      q.hidden = false;
      q.scrollIntoView({ block: "nearest" });
    } else
      throw new Error("Unable to render private QR. Try revealing it again.");
    qrTimer = setInterval(() => {
      const sec = Math.ceil((qrDeadline - performance.now()) / 1000);
      if (sec <= 0 || !current()) hideQR();
      else q.querySelector("#qr-seconds").textContent = sec;
    }, 100);
  } catch (e) {
    if (!current()) return;
    hideQR();
    message.textContent = e.message;
    message.hidden = false;
    message.scrollIntoView({ block: "nearest" });
  } finally {
    if (button.isConnected) button.disabled = false;
  }
}
async function matchMutation(id, action, data, msg) {
  try {
    await api(`/matches/${id}/${action}`, { method: "POST", data });
    closeDialog();
    toast(msg);
    render();
  } catch (e) {
    toast(e.message, true);
  }
}
function resultSetFields(sets) {
  return sets
    .map(
      (s, i) =>
        `<div class="form-grid result-set"><h3 class="full">Set ${i + 1}</h3>${field("a_" + i, "Team A points", "number", s.a, 'required min="0" max="1000"')}${field("b_" + i, "Team B points", "number", s.b, 'required min="0" max="1000"')}</div>`,
    )
    .join("");
}
function correctResult(m) {
  const original = (m.state?.sets || []).map((s) => ({ a: s.a, b: s.b }));
  modal(
    "Preview result correction",
    `<p class="muted small">Current result: ${esc(score(m))}. Corrections preserve revision history.</p><div id="corrected-sets">${resultSetFields(original.length ? original : [{ a: 0, b: 0 }])}</div><div class="button-group section-gap"><button type="button" class="button secondary" id="add-result-set">Add set</button><button type="button" class="button secondary" id="remove-result-set">Remove last set</button></div>${textarea("reason", "Correction reason", "", "required")}<p class="form-help">Review affected standings and qualifiers before committing.</p>`,
    async (fd, form) => {
      const sets = [...form.querySelectorAll(".result-set")].map((row, i) => ({
        a: Number(fd.get("a_" + i)),
        b: Number(fd.get("b_" + i)),
      }));
      const data = { sets, reason: fd.get("reason") };
      const preview = await api(`/matches/${m.id}/correct`, {
        method: "POST",
        data: { ...data, preview: true },
      });
      setTimeout(
        () =>
          modal(
            "Review correction impact",
            `<h3>Corrected set summary</h3>${table(
              [
                { label: "Set", render: (r) => esc(r.set) },
                { label: teamName(m, "a"), key: "a" },
                { label: teamName(m, "b"), key: "b" },
              ],
              sets.map((s, i) => ({ ...s, set: i + 1 })),
            )}<h3 class="section-gap">Affected rankings & qualifiers</h3>${correctionImpact(preview.preview || preview.impact || preview)}`,
            async () => {
              await api(`/matches/${m.id}/correct`, {
                method: "POST",
                data: { ...data, preview: false },
              });
              toast("Result revision saved.");
              render();
            },
            { submit: "Commit reviewed correction", wide: true },
          ),
        0,
      );
    },
    { submit: "Preview affected rankings", wide: true },
  );
  const form = document.querySelector("#modal-form");
  const current = () =>
    [...form.querySelectorAll(".result-set")].map((row, i) => ({
      a: Number(form.elements["a_" + i].value),
      b: Number(form.elements["b_" + i].value),
    }));
  document.querySelector("#add-result-set").onclick = () => {
    const sets = current();
    if (sets.length >= 9)
      return toast("At most nine sets are supported.", true);
    sets.push({ a: 0, b: 0 });
    document.querySelector("#corrected-sets").innerHTML = resultSetFields(sets);
  };
  document.querySelector("#remove-result-set").onclick = () => {
    const sets = current();
    if (sets.length <= 1) return toast("At least one set is required.", true);
    sets.pop();
    document.querySelector("#corrected-sets").innerHTML = resultSetFields(sets);
  };
}
function correctionImpact(impact) {
  const entryName = (id) =>
    items(tournament.entries).find((e) => e.id === id)?.name || "TBD";
  const downstream = (impact.downstream || []).map((item) => {
    const fixture = items(tournament.matches).find((m) => m.id === item.id);
    return {
      ...item,
      game: fixture
        ? `${fixture.stage_name || "Playoff"} · Round ${fixture.round_number}`
        : "Downstream playoff fixture",
      teams: `${entryName(item.current_a)} vs ${entryName(item.current_b)}`,
      action: item.blocked
        ? "Participants preserved; organizer adjudication may be required"
        : "Eligible qualifier slot updates when the official result changes",
    };
  });
  return `<div class="notice"><strong>Proposed winner: ${esc(impact.winner_id ? entryName(impact.winner_id) : "Draw")}</strong><p>${impact.standings_will_recalculate ? "Official standings will be recalculated after this revision." : "This submitted result remains subject to organizer approval."}</p>${impact.requires_adjudication ? '<p class="form-error">A downstream game has already started. Its played participants are preserved and require adjudication.</p>' : "<p>Started downstream participants are preserved.</p>"}</div>${
    downstream.length
      ? table(
          [
            { key: "game", label: "Game" },
            { key: "teams", label: "Current participants" },
            { key: "status", label: "Status" },
            { key: "action", label: "Revision effect" },
          ],
          downstream,
        )
      : '<p class="muted small">No direct downstream knockout fixtures are affected.</p>'
  }`;
}
async function showNotifications() {
  if (!dashboard && org) await getDashboard();
  modal("Workspace notifications", attention(dashboard || {}), null);
  bindMatches();
}
function startRefresh(page, id, tab) {
  const token = routeToken,
    organizationId = org?.id,
    dialog = document.querySelector("#dialog"),
    isOverview =
      page === "overview" ||
      (page === "tournaments" && (!tab || tab === "overview"));
  let busy = false,
    freshenAfterRequest = false;
  const current = () => token === routeToken && organizationId === org?.id;
  const poll = async () => {
    if (!current() || document.hidden) return;
    if (dialog?.open) {
      if (isOverview) setOverviewFreshness("paused");
      return;
    }
    if (busy) {
      freshenAfterRequest = true;
      return;
    }
    busy = true;
    try {
      const d =
        page === "tournaments"
          ? await api(`/tournaments/${id}`)
          : await api("/dashboard");
      if (!current() || document.hidden) return;
      if (dialog?.open) {
        if (isOverview) setOverviewFreshness("paused");
        return;
      }
      if (page === "tournaments") tournament = d;
      else dashboard = d;
      if (isOverview) {
        refreshOverviewRegions(d, page === "tournaments");
        setOverviewFreshness(
          "fresh",
          d.generated_at || new Date().toISOString(),
        );
      }
      if (page === "schedule" || tab === "schedule") {
        const fixtures = document.querySelector("#fixture-panel");
        if (!fixtures?.contains(document.activeElement)) filterMatches(d);
      }
    } catch {
      if (current() && isOverview)
        setOverviewFreshness(dialog?.open ? "paused" : "stale");
    } finally {
      busy = false;
      if (freshenAfterRequest && current()) {
        freshenAfterRequest = false;
        poll();
      }
    }
  };
  const onClose = () => {
    if (!dialog?.open) poll();
  };
  const onVisibility = () => {
    if (!document.hidden) poll();
  };
  const onOffline = () => {
    if (current() && isOverview) setOverviewFreshness("stale");
  };
  dialog?.addEventListener("close", onClose);
  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("online", poll);
  window.addEventListener("offline", onOffline);
  refreshCleanup = () => {
    dialog?.removeEventListener("close", onClose);
    document.removeEventListener("visibilitychange", onVisibility);
    window.removeEventListener("online", poll);
    window.removeEventListener("offline", onOffline);
  };
  refreshTimer = setInterval(poll, 2000);
}
function stopOrganizerRefresh() {
  clearInterval(refreshTimer);
  refreshTimer = undefined;
  refreshCleanup?.();
  refreshCleanup = undefined;
}
function freshnessTime(stamp) {
  const parsed = new Date(stamp || Date.now());
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  }).format(Number.isNaN(parsed.getTime()) ? new Date() : parsed);
}
function overviewFreshness(stamp) {
  return `<div class="overview-freshness" data-state="fresh"><span id="overview-refresh-status" role="status">Live updates · Every 2 seconds</span><span id="overview-updated" aria-live="off">Updated ${esc(freshnessTime(stamp))}</span></div>`;
}
function setOverviewFreshness(state, stamp) {
  const status = document.querySelector("#overview-refresh-status"),
    updated = document.querySelector("#overview-updated"),
    messages = {
      fresh: "Live updates · Every 2 seconds",
      paused: "Updates paused while a dialog is open",
      stale: "Unable to refresh · Values may be stale",
    };
  if (!status) return;
  status.parentElement.dataset.state = state;
  if (status.textContent !== messages[state])
    status.textContent = messages[state];
  if (stamp && updated) updated.textContent = `Updated ${freshnessTime(stamp)}`;
}
function refreshOverviewRegions(d, eventMode) {
  const template = document.createElement("template");
  template.innerHTML = eventMode
    ? eventOverview(d)
    : dashboardOverviewMarkup(d);
  const selection = document.getSelection();
  for (const id of [
    "overview-metrics",
    "overview-tournaments",
    "live-cards",
    "overview-upcoming",
    "overview-courts",
    "overview-progress",
    "overview-attention",
    "overview-announcements",
    "overview-live-count",
    "overview-court-summary",
    "overview-schedule-summary",
  ]) {
    const node = document.getElementById(id),
      replacement = template.content.querySelector(`#${id}`);
    if (!node || !replacement || node.innerHTML === replacement.innerHTML)
      continue;
    if (
      node.contains(document.activeElement) ||
      (selection &&
        !selection.isCollapsed &&
        (node.contains(selection.anchorNode) ||
          node.contains(selection.focusNode)))
    )
      continue;
    const details = [...node.querySelectorAll("details")].map((n) => n.open),
      scroll = [...node.querySelectorAll(".table-scroll,.bracket")].map((n) => [
        n.scrollLeft,
        n.scrollTop,
      ]);
    node.innerHTML = replacement.innerHTML;
    node.querySelectorAll("details").forEach((n, i) => {
      if (details[i] !== undefined) n.open = details[i];
    });
    node.querySelectorAll(".table-scroll,.bracket").forEach((n, i) => {
      if (scroll[i]) [n.scrollLeft, n.scrollTop] = scroll[i];
    });
  }
  const counts = document.querySelector("#event-summary-counts");
  if (eventMode && counts)
    counts.textContent = `${items(d.divisions).length} divisions · ${items(d.entries).length} entries · ${items(d.matches).length} fixtures`;
  bindMatches();
}
async function loadWeather(id) {
  const node = document.querySelector("#weather-panel");
  if (!node) return;
  try {
    const w = await api(
      `/tournaments/${id}/weather?units=${org.settings?.units || "celsius"}`,
    );
    if (node.isConnected && node === document.querySelector("#weather-panel")) node.innerHTML = renderWeather(w);
  } catch (e) {
    if (node.isConnected && node === document.querySelector("#weather-panel"))
      node.innerHTML = `<p class="muted small">Weather unavailable. ${esc(e.message)}</p>`;
  }
}
export function renderWeather(w) {
  if (Array.isArray(w.items)) {
    if (!w.items.length)
      return `<p class="muted small">${esc(w.message || "No venue forecast available.")}</p>`;
    return w.items
      .map(renderWeather)
      .join(
        '<hr style="border:0;border-top:1px solid var(--line);margin:18px 0">',
      );
  }
  const days = items(w.days);
  return `<h3>${esc(w.venue?.name || w.venue_name || "Venue weather")}</h3>${w.message ? `<p class="muted small">${esc(w.message)}</p>` : ""}${
    days.length
      ? `<div class="weather-grid">${days
          .slice(0, 7)
          .map(
            (f) =>
              `<div class="weather-day">${esc(date(f.date))}<strong>${esc(f.temperature_high ?? "—")}°${esc(w.temperature_unit || "C")}</strong>${esc(f.temperature_low ?? "—")}° low<p>${esc(f.condition || "")}</p><small>${esc(f.rain_probability ?? "—")}% rain<br>${esc(f.wind_speed ?? "—")} ${esc(w.wind_unit || "km/h")} wind</small></div>`,
          )
          .join("")}</div>`
      : !w.message
        ? '<p class="muted small">Weather unavailable.</p>'
        : ""
  }<p class="small muted">${esc(pretty(w.status || "unavailable"))} · ${esc(w.timezone || "Venue timezone")} ${w.provider ? "· " + esc(w.provider) : ""} ${w.updated_at ? "· Updated " + esc(dt(w.updated_at)) : ""}</p>${w.attribution ? `<p class="muted small">${esc(w.attribution)}</p>` : ""}`;
}
async function setupMFA(replace = false) {
  modal(
    replace ? "Replace authenticator" : "Set up authenticator",
    field(
      "password",
      "Confirm current password",
      "password",
      "",
      'required autocomplete="current-password"',
    ) +
      (replace
        ? field(
            "otp",
            "Current authenticator or recovery code",
            "text",
            "",
            "required",
          )
        : ""),
    async (fd) => {
      const r = await api("/auth/mfa/setup", {
        method: "POST",
        data: { password: fd.get("password"), replace, otp: fd.get("otp") },
      });
      setTimeout(
        () =>
          modal(
            "Confirm authenticator setup",
            `<p class="muted small">Add this setup secret in your authenticator app, then enter the generated code. Store recovery codes privately.</p><pre>${esc(r.secret)}</pre>${field("otp", "Authenticator code", "text", "", 'required inputmode="numeric" autocomplete="one-time-code"')}`,
            async (code) => {
              const result = await api("/auth/mfa/confirm", {
                method: "POST",
                data: { enrollment_id: r.enrollment_id, otp: code.get("otp") },
              });
              identity.user.mfa_enabled = true;
              setTimeout(
                () =>
                  modal(
                    "Save your recovery codes",
                    `<p class="muted small">These codes are displayed once. Store them privately; each code can be used once.</p><pre>${esc((result.recovery_codes || []).join("\n"))}</pre>`,
                    null,
                  ),
                0,
              );
              toast("MFA enabled.");
            },
          ),
        0,
      );
    },
    { submit: "Continue" },
  );
}
let reportData,
  reportFilters = {},
  reportColumns = [],
  reportAllColumns = [],
  reportOffset = 0,
  reportPage = 1,
  reportSearchTimer,
  reportRequest = 0;
const reportDefaultColumns = {
  matches: [
    "team_a",
    "team_b",
    "tournament",
    "division",
    "court",
    "planned_at",
    "status",
    "result_status",
    "sets",
    "winner",
  ],
  schedule: [
    "planned_at",
    "team_a",
    "team_b",
    "tournament",
    "division",
    "stage",
    "court",
    "planned_minutes",
    "status",
    "referee_name",
  ],
  referees: [
    "referee_name",
    "planned_at",
    "team_a",
    "team_b",
    "tournament",
    "division",
    "court",
    "status",
    "actual_minutes",
  ],
  outstanding_results: [
    "team_a",
    "team_b",
    "tournament",
    "division",
    "court",
    "status",
    "result_status",
    "sets",
    "winner",
  ],
  summary: [
    "tournament",
    "competition_type",
    "start_date",
    "end_date",
    "teams",
    "games",
    "live",
    "finalized",
    "completion_percent",
  ],
  history: [
    "tournament",
    "start_date",
    "end_date",
    "teams",
    "games",
    "finalized",
    "average_actual_minutes",
    "finalized_sets",
    "finalized_rally_points",
  ],
  standings: [
    "division",
    "rank",
    "name",
    "played",
    "won",
    "lost",
    "standings_points",
    "sets_won",
    "sets_lost",
    "tied",
  ],
  team_performance: [
    "team",
    "tournament",
    "division",
    "played",
    "wins",
    "losses",
    "draws",
    "sets_won",
    "sets_lost",
    "points_scored",
    "points_conceded",
  ],
  court_use: [
    "court",
    "status",
    "reserved_at",
    "started_at",
    "released_at",
    "occupied_minutes",
  ],
  audit: ["at", "actor_type", "action", "entity_type", "reason"],
};
function reportView() {
  return {
    search: document.querySelector("#report-search")?.value || "",
    sort: document.querySelector("#report-sort")?.value || "",
    columns: reportColumns.join(","),
    direction: document.querySelector("#report-direction")?.value || "asc",
  };
}
function reportFilterSummary(filters = exportFilters()) {
  const form = document.querySelector("#report-filters");
  return Object.entries(filters)
    .filter(
      ([, value]) => value !== "" && value !== undefined && value !== null,
    )
    .map(([key, value]) => {
      const control = form?.elements[key],
        option =
          control?.tagName === "SELECT"
            ? [...control.options].find((item) => item.value === String(value))
            : null,
        label =
          control?.closest("label")?.querySelector("span")?.textContent ||
          pretty(key);
      const display = option
        ? option.textContent
        : key === "columns"
          ? String(value).split(",").map(pretty).join(", ")
          : key === "direction"
            ? value === "asc"
              ? "Ascending"
              : "Descending"
            : reportScopeName(key, value) || value;
      return { filter: label, value: display };
    });
}
function reportScopeName(key, value, metadata = reportData?.metadata || {}) {
  const records = {
    tournament_id: [
      ...items(metadata.tournaments),
      ...items(dashboard?.tournaments),
    ],
    division_id: items(tournament?.divisions),
    stage_id: items(tournament?.stages),
    team_id: [...items(dashboard?.teams), ...items(tournament?.entries)],
    venue_id: items(dashboard?.venues),
    court_id: items(dashboard?.courts),
  }[key];
  return records?.find((record) => record.id === value)?.name;
}
function reportSets(value) {
  if (!Array.isArray(value)) return value == null ? "—" : String(value);
  if (!value.length) return "No recorded sets";
  return value
    .map(
      (set) =>
        `${set.a ?? 0}–${set.b ?? 0}${set.complete === false ? " (current)" : ""}`,
    )
    .join(" · ");
}
function reportRuleSummary(value) {
  if (!value || typeof value !== "object" || !Object.keys(value).length)
    return "Inherited competition policy; no frozen snapshot";
  const rules = value,
    summary = [];
  if (rules.mode)
    summary.push(
      rules.mode === "fixed"
        ? `${rules.fixed_sets} fixed sets`
        : `Best of ${rules.best_of}`,
    );
  if (rules.regular_target !== undefined)
    summary.push(
      `Target ${rules.regular_target}${rules.deciding_target !== undefined ? " / deciding " + rules.deciding_target : ""}`,
    );
  if (rules.win_by !== undefined) summary.push(`Lead ${rules.win_by}`);
  if ("cap" in rules) summary.push(rules.cap ? "Cap " + rules.cap : "No cap");
  if (rules.timeouts !== undefined)
    summary.push(rules.timeouts + " timeouts per set");
  return (
    summary.join(" · ") ||
    Object.entries(rules)
      .map(
        ([key, item]) =>
          pretty(key) +
          ": " +
          (typeof item === "boolean"
            ? item
              ? "Yes"
              : "No"
            : Array.isArray(item)
              ? item.map(pretty).join(" → ")
              : (item ?? "None")),
      )
      .join(" · ")
  );
}
function reportCell(key, value, row) {
  if (key === "sets") return esc(reportSets(value));
  if (key === "rules_used")
    return `<div class="report-cell-summary">${esc(reportRuleSummary(value))}</div>`;
  if (value == null || value === "") return "—";
  if (["status", "result_status", "outcome", "competition_type"].includes(key))
    return badge(value);
  if (key === "tied") return value ? "Unresolved tie" : "Ranked";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (key.endsWith("_date")) return esc(date(value));
  if (
    key === "at" ||
    key.endsWith("_at") ||
    ["actual_start", "actual_end"].includes(key)
  )
    return esc(
      dt(value, { timeZone: reportData.metadata?.timezone || org.timezone }),
    );
  if (key === "completion_percent" || key === "win_percentage")
    return esc(value) + "%";
  if (key === "outcomes" && typeof value === "object")
    return esc(
      Object.entries(value)
        .map(([outcome, count]) => pretty(outcome) + ": " + count)
        .join(" · ") || "No outcomes",
    );
  if (typeof value === "object") {
    if (key === "changes") {
      const before = value.before || {},
        after = value.after || {};
      const fields = [
        ...new Set([...Object.keys(before), ...Object.keys(after)]),
      ].filter(
        (field) =>
          JSON.stringify(before[field]) !== JSON.stringify(after[field]),
      );
      return `<div class="report-cell-summary">${esc(fields.length ? "Changed: " + fields.map(pretty).join(", ") : "Recorded audited state")}</div>`;
    }
    return `<div class="report-cell-summary">${esc(
      Array.isArray(value)
        ? value
            .map((item) =>
              typeof item === "string"
                ? pretty(item)
                : typeof item === "object"
                  ? Object.entries(item)
                      .map(
                        ([field, itemValue]) =>
                          pretty(field) + ": " + itemValue,
                      )
                      .join(", ")
                  : item,
            )
            .join(" · ")
        : Object.entries(value)
            .map(
              ([field, item]) =>
                pretty(field) +
                ": " +
                (typeof item === "object" ? "Recorded details" : item),
            )
            .join(" · "),
    )}</div>`;
  }
  return esc(value);
}
function reportSourceDetails(metadata) {
  const competitions = items(metadata.tournaments),
    generations = Object.entries(metadata.source_generations || {}).map(
      ([id, generation]) => ({
        competition:
          competitions.find((item) => item.id === id)?.name ||
          reportScopeName("tournament_id", id, metadata) ||
          "Authorized competition",
        generation,
      }),
    );
  const sourceGames = Object.entries(metadata.source_match_versions || {});
  const scopeRows = [
    {
      selection: "Organization",
      value: metadata.organization?.name || org.name,
    },
    {
      selection: "Competition scope",
      value:
        competitions.map((item) => item.name).join(" · ") ||
        "No matching competitions",
    },
    {
      selection: "Generated",
      value: dt(metadata.generated_at, {
        timeZone: metadata.timezone || org.timezone,
        year: "numeric",
        timeZoneName: "short",
      }),
    },
    { selection: "Timezone", value: metadata.timezone || org.timezone },
    {
      selection: "Result policy",
      value: metadata.official_status || "Scoped persisted records",
    },
    ...reportFilterSummary()
      .filter((row) => !["Columns", "Direction"].includes(row.filter))
      .map((row) => ({ selection: row.filter, value: row.value })),
  ];
  return `<details class="report-source-details"><summary>Source & generation details</summary>${table(
    [
      { key: "selection", label: "Scope" },
      { key: "value", label: "Selection" },
    ],
    scopeRows,
  )}${
    generations.length
      ? `<h3>Official standings generations</h3>${table(
          [
            { key: "competition", label: "Competition" },
            { key: "generation", label: "Generation" },
          ],
          generations,
        )}`
      : ""
  }<p class="muted small">${sourceGames.length} persisted game versions contributed to this report’s source snapshot. Exports preserve original recorded values.</p>${
    Object.keys(metadata.metric_definitions || {}).length
      ? `<details><summary>Metric definitions</summary>${table(
          [
            { key: "metric", label: "Metric" },
            { key: "definition", label: "Definition" },
          ],
          Object.entries(metadata.metric_definitions).map(
            ([key, definition]) => ({ metric: pretty(key), definition }),
          ),
        )}</details>`
      : ""
  }</details>`;
}
function exportFilters() {
  return {
    ...reportFilters,
    ...Object.fromEntries(Object.entries(reportView()).filter(([, v]) => v)),
  };
}
function bindReports() {
  document.querySelectorAll("[data-report-tab]").forEach(
    (b) =>
      (b.onclick = async () => {
        document
          .querySelectorAll("[data-report-tab]")
          .forEach((t) => t.classList.toggle("active", t === b));
        ["browser", "jobs", "audit", "views"].forEach(
          (t) =>
            (document.querySelector("#report-" + t).hidden =
              t !== b.dataset.reportTab),
        );
        if (b.dataset.reportTab === "audit") {
          try {
            const logs = (await api("/audit")).rows || [];
            document.querySelector("#report-audit").innerHTML =
              `<section class="panel">${table(
                [
                  { label: "When", render: (a) => esc(dt(a.at)) },
                  {
                    label: "Action",
                    render: (a) =>
                      `<span class="audit-action">${esc(a.action)}</span>`,
                  },
                  {
                    label: "Actor",
                    render: (a) =>
                      esc(a.actor_type || a.actor_id || "Organizer"),
                  },
                  {
                    label: "Entity",
                    render: (a) =>
                      `${esc(a.entity_type)} · ${esc(a.entity_id)}`,
                  },
                  { label: "Reason", render: (a) => esc(a.reason || "—") },
                ],
                logs,
              )}</section>`;
          } catch (e) {
            toast(e.message, true);
          }
        }
      }),
  );
  document.querySelector("#report-filters").onsubmit = async (e) => {
    e.preventDefault();
    const prior = reportFilters.type;
    reportFilters = Object.fromEntries(
      [...new FormData(e.target)].filter(([, v]) => v),
    );
    reportPage = 1;
    if (prior !== reportFilters.type) {
      reportColumns = [];
      reportAllColumns = [];
      document.querySelector("#report-sort").value = "";
    }
    await runReport();
  };
  document.querySelectorAll("[data-report-download]").forEach(
    (b) =>
      (b.onclick = () => {
        if (!reportData) return toast("Run and review a report first.", true);
        download(
          `/reports/export?${new URLSearchParams({ ...exportFilters(), format: b.dataset.reportDownload })}`,
        ).catch((e) => toast(e.message, true));
      }),
  );
  document.querySelector("#export-job").onclick = () => {
    if (!reportData) return toast("Run and review a report first.", true);
    modal(
      "Create filtered export job",
      select("format", "Download format", ["csv", "xlsx", "pdf"]) +
        `<p class="muted small">The export contains the full filtered report with your current search, sorting, and selected columns.</p>${table(
          [
            { key: "filter", label: "Selection" },
            { key: "value", label: "Value" },
          ],
          reportFilterSummary(),
        )}`,
      async (fd) => {
        const view = reportView();
        await api("/exports", {
          method: "POST",
          data: {
            type: reportFilters.type || "matches",
            format: fd.get("format"),
            filters: { ...reportFilters, search: view.search },
            columns: reportColumns,
            sort: view.sort,
            direction: view.direction,
          },
        });
        toast("Export job recorded.");
        render();
      },
      { submit: "Create export job" },
    );
  };
  bindJobActions();
  document.querySelector("#save-report").onclick = () =>
    modal(
      "Save report view",
      field("name", "View name", "text", "", "required"),
      async (fd) => {
        await api("/preferences", {
          method: "POST",
          data: {
            kind: "report",
            name: fd.get("name"),
            layout: { name: fd.get("name"), filters: exportFilters() },
          },
        });
        toast("Scoped report view saved.");
      },
    );
  document.querySelectorAll("[data-saved-view]").forEach(
    (b) =>
      (b.onclick = async () => {
        const saved = JSON.parse(b.dataset.filters);
        reportColumns = saved.columns?.split(",") || [];
        reportAllColumns = [];
        reportPage = 1;
        const form = document.querySelector("#report-filters");
        if (saved.tournament_id) {
          const competition = await api(`/tournaments/${saved.tournament_id}`);
          for (const [key, list] of [
            ["division_id", competition.divisions],
            ["stage_id", competition.stages],
          ]) {
            form.elements[key].innerHTML =
              `<option value="">All ${key === "division_id" ? "divisions" : "stages"}</option>` +
              options(items(list))
                .map(
                  ([id, label]) =>
                    `<option value="${esc(id)}">${esc(label)}</option>`,
                )
                .join("");
          }
        }
        form.reset();
        Object.entries(saved).forEach(([k, v]) => {
          if (form.elements[k]) form.elements[k].value = v;
        });
        document.querySelector("#report-search").value = saved.search || "";
        document.querySelector("#report-sort").innerHTML =
          `<option value="">Default order</option>${saved.sort ? `<option selected value="${esc(saved.sort)}">${esc(pretty(saved.sort))}</option>` : ""}`;
        document.querySelector("#report-direction").value =
          saved.direction || "asc";
        reportFilters = Object.fromEntries(
          [...new FormData(form)].filter(([, value]) => value),
        );
        document.querySelector("[data-report-tab=browser]").click();
        await runReport();
      }),
  );
  document.querySelector("#print-report").onclick = () => window.print();
  document.querySelector("#report-search").oninput = () => {
    clearTimeout(reportSearchTimer);
    reportSearchTimer = setTimeout(() => {
      reportPage = 1;
      runReport();
    }, 350);
  };
  document.querySelector("#report-sort").onchange = () => {
    reportPage = 1;
    runReport();
  };
  document.querySelector("#report-direction").onchange = () => {
    reportPage = 1;
    runReport();
  };
  document.querySelector("#report-columns").onclick = () => {
    if (!reportData) return toast("Run a report first.", true);
    modal(
      "Select report columns",
      reportAllColumns
        .map(
          (c) =>
            `<label class="check-field"><input type="checkbox" name="columns" value="${esc(c)}" ${reportColumns.includes(c) ? "checked" : ""}>${esc(pretty(c))}</label>`,
        )
        .join(""),
      async (fd) => {
        const selected = fd.getAll("columns");
        if (!selected.length) throw new Error("Select at least one column.");
        reportColumns = selected;
        if (!selected.includes(document.querySelector("#report-sort").value))
          document.querySelector("#report-sort").value = "";
        reportPage = 1;
        await runReport();
      },
    );
  };
  document.querySelector("#open-import").onclick = openImport;
  clearInterval(refreshTimer);
  refreshTimer = setInterval(async () => {
    if (document.hidden || document.querySelector("#report-jobs")?.hidden)
      return;
    try {
      const jobs = items(await api("/exports"));
      document.querySelector("#report-jobs").innerHTML =
        `<section class="panel">${jobsTable(jobs)}</section>`;
      bindJobActions();
    } catch {}
  }, 2000);
  document.querySelector("#report-filters [name=tournament_id]").onchange =
    async (e) => {
      const id = e.target.value;
      const d = id ? await api(`/tournaments/${id}`) : {};
      ["division_id", "stage_id"].forEach((k) => {
        const list = k === "division_id" ? d.divisions : d.stages;
        const el = document.querySelector(`#report-filters [name=${k}]`);
        if (el)
          el.innerHTML =
            '<option value="">All ' +
            (k === "division_id" ? "divisions" : "stages") +
            "</option>" +
            options(items(list))
              .map(([v, n]) => `<option value="${esc(v)}">${esc(n)}</option>`)
              .join("");
      });
    };
}
function bindJobActions() {
  document
    .querySelectorAll("[data-job-download]")
    .forEach(
      (b) =>
        (b.onclick = () =>
          download(`/exports/${b.dataset.jobDownload}/download`).catch((e) =>
            toast(e.message, true),
          )),
    );
  document.querySelectorAll("[data-job-cancel]").forEach(
    (b) =>
      (b.onclick = async () => {
        if (await confirm("Cancel export", "Cancel this pending export job?")) {
          await api(`/exports/${b.dataset.jobCancel}`, { method: "DELETE" });
          toast("Export cancelled.");
          render();
        }
      }),
  );
}
async function runReport() {
  const request = ++reportRequest;
  try {
    const values = { ...exportFilters(), page: reportPage, page_size: 25 };
    const response = await api(`/reports?${new URLSearchParams(values)}`);
    if (request !== reportRequest || !document.querySelector("#report-table"))
      return;
    reportData = response;
    const columns = reportData.columns.map((c) =>
      typeof c === "string" ? c : c.key,
    );
    if (!reportAllColumns.length) {
      if (reportColumns.length) {
        const allValues = { ...values };
        delete allValues.columns;
        allValues.page_size = 1;
        const complete = await api(
          `/reports?${new URLSearchParams(allValues)}`,
        );
        if (
          request !== reportRequest ||
          !document.querySelector("#report-table")
        )
          return;
        reportAllColumns = complete.columns.map((column) =>
          typeof column === "string" ? column : column.key,
        );
      } else reportAllColumns = columns;
    }
    if (!reportColumns.length) {
      const defaults =
        reportDefaultColumns[reportFilters.type || "matches"] || columns;
      reportColumns = defaults.filter((column) => columns.includes(column));
      if (!reportColumns.length) reportColumns = columns;
      const selectedResponse = await api(
        `/reports?${new URLSearchParams({ ...values, columns: reportColumns.join(",") })}`,
      );
      if (request !== reportRequest || !document.querySelector("#report-table"))
        return;
      reportData = selectedResponse;
    }
    const meta = reportData.metadata || {};
    document.querySelector("#report-metadata").innerHTML =
      `<div><strong>${esc(org.name)} · ${reportData.total} filtered records</strong><p>${esc(meta.official_status || "Scoped persisted records")} · ${esc(meta.timezone || org.timezone)} · Generated ${esc(dt(meta.generated_at, { timeZone: meta.timezone || org.timezone }))}</p>${reportSourceDetails(meta)}</div>`;
    const selected = document.querySelector("#report-sort").value;
    document.querySelector("#report-sort").innerHTML =
      '<option value="">Default order</option>' +
      reportColumns
        .map(
          (c) =>
            `<option value="${esc(c)}" ${c === selected ? "selected" : ""}>${esc(pretty(c))}</option>`,
        )
        .join("");
    renderReportTable();
  } catch (e) {
    toast(e.message, true);
  }
}
function renderReportTable() {
  if (!reportData) return;
  const columns = reportData.columns.map((c) => {
      const column = typeof c === "string" ? { key: c, label: pretty(c) } : c;
      return {
        ...column,
        render: (row) => reportCell(column.key, row[column.key], row),
      };
    }),
    from = (reportPage - 1) * 25;
  document.querySelector("#report-table").innerHTML =
    table(columns, reportData.rows) +
    `<div class="table-footer"><span>${Math.min(from + 1, reportData.total)}–${Math.min(from + reportData.rows.length, reportData.total)} of ${reportData.total} filtered records</span><div class="button-group"><button class="button secondary small" id="report-prev" ${reportPage > 1 ? "" : "disabled"}>Previous</button><button class="button secondary small" id="report-next" ${from + 25 >= reportData.total ? "disabled" : ""}>Next</button></div></div>${
      Object.keys(reportData.totals || {}).length
        ? `<div class="panel-pad small muted">${Object.entries(
            reportData.totals,
          )
            .map(([key, value]) => `${esc(pretty(key))}: ${esc(value)}`)
            .join(" · ")}</div>`
        : ""
    }`;
  document.querySelector("#report-prev").onclick = () => {
    reportPage = Math.max(1, reportPage - 1);
    runReport();
  };
  document.querySelector("#report-next").onclick = () => {
    reportPage++;
    runReport();
  };
}
function openImport() {
  modal(
    "Preview CSV import",
    select(
      "type",
      "Import type",
      ["teams", "entries", "fixtures", "referees"],
      "teams",
      'id="import-type"',
    ) +
      '<button class="button secondary section-gap" type="button" id="import-template">Download template</button>' +
      field("file", "CSV file", "file", "", 'required accept=".csv,text/csv"') +
      '<p class="form-help">Preview row validation before committing. Imports cannot silently overwrite finalized match results.</p>',
    async (fd) => {
      const preview = await api("/imports/preview", {
        method: "POST",
        body: fd,
      });
      setTimeout(
        () =>
          modal(
            "Review import preview",
            importPreview(preview),
            preview.errors?.length
              ? null
              : async () => {
                  await api(`/imports/${preview.id || preview.job_id}/commit`, {
                    method: "POST",
                    data: {},
                  });
                  toast("Reviewed import committed.");
                  render();
                },
            { submit: "Commit validated import", wide: true },
          ),
        0,
      );
    },
    { submit: "Validate & preview" },
  );
  document.querySelector("#import-template").onclick = () =>
    download(
      `/imports/template?type=${document.querySelector("#import-type").value}`,
    ).catch((e) => toast(e.message, true));
}
function importPreview(preview) {
  const records = preview.preview || [],
    errors = preview.errors || [];
  const keys = [...new Set(records.flatMap((record) => Object.keys(record)))];
  return `<div class="banner ${errors.length ? "danger" : ""}"><strong>${esc(pretty(preview.type))} import · ${preview.row_count ?? records.length} rows</strong><p>${errors.length ? `${errors.length} validation errors. Correct the CSV and preview again; no records have been imported.` : "Validation passed. Review the records before committing this import."}</p></div>${
    errors.length
      ? `<h3>Row errors</h3>${table(
          [
            { key: "row", label: "CSV row" },
            { key: "message", label: "Required correction" },
          ],
          errors,
        )}`
      : ""
  }<h3 class="section-gap">Validated records</h3><p class="muted small">Showing the first ${records.length} validated rows. The commit rechecks every row and applies the entire file together.</p>${table(
    keys.map((key) => ({ key, label: pretty(key) })),
    records,
  )}`;
}

function mfaAction(kind) {
  modal(
    kind === "disable"
      ? "Disable multi-factor authentication"
      : "Replace recovery codes",
    field("password", "Current password", "password", "", "required") +
      field("otp", "Authenticator or recovery code", "text", "", "required") +
      '<p class="form-help">Organization security policy may require MFA. Replacing codes invalidates all previous recovery codes.</p>',
    async (fd) => {
      const r = await api("/auth/mfa/" + kind, {
        method: "POST",
        data: formObject(fd),
      });
      if (kind === "disable") {
        identity.user.mfa_enabled = false;
        toast("MFA disabled.");
        render();
      } else
        setTimeout(
          () =>
            modal(
              "Save new recovery codes",
              "<p>Save these codes privately. They appear once.</p><pre>" +
                esc((r.recovery_codes || []).join("\n")) +
                "</pre>",
              null,
            ),
          0,
        );
    },
  );
}

const ORGANIZER_WIDGETS = [
  "tournaments",
  "live",
  "upcoming",
  "courts",
  "progress",
  "attention",
];
async function loadOrganizerPrefs() {
  if (!org) return;
  try {
    const rows = items(await api("/preferences?kind=organizer"));
    organizerLayout = rows.find((p) => p.name === "Default")?.layout || {};
    if (organizerLayout.theme) theme(organizerLayout.theme);
  } catch {
    organizerLayout = {};
  }
}
function organizerWidgetEditor() {
  const order = organizerLayout.widgets || ORGANIZER_WIDGETS;
  return `<h3 class="section-gap">My dashboard widgets</h3><p class="muted small">Choose content and use arrows to change order. These preferences are private to you in this organization.</p>${[...order, ...ORGANIZER_WIDGETS.filter((w) => !order.includes(w))].map((w) => `<div class="display-widget-row" data-widget="${w}"><label class="check-field"><input name="widgets" value="${w}" type="checkbox" ${order.includes(w) ? "checked" : ""}>${pretty(w)}</label><select data-size="${w}" aria-label="${w} widget size"><option value="normal" ${organizerLayout.sizes?.[w] === "normal" ? "selected" : ""}>Normal</option><option value="wide" ${organizerLayout.sizes?.[w] === "wide" ? "selected" : ""}>Wide</option></select><button type="button" data-move="up" aria-label="Move ${w} up">↑</button><button type="button" data-move="down" aria-label="Move ${w} down">↓</button></div>`).join("")}`;
}
function applyOrganizerLayout() {
  document.documentElement.dataset.density =
    organizerLayout.density || "comfortable";
  const panels = [
      ...document.querySelectorAll(".dashboard-grid>.stack>.panel"),
    ],
    mapping = {
      tournaments: "Tournaments & leagues",
      live: "Live on court",
      upcoming: "Upcoming fixtures",
      courts: "Court availability",
      progress: "Match progress",
      attention: "Needs your attention",
    };
  if (!organizerLayout.widgets) return;
  const grid = document.querySelector(".dashboard-grid");
  grid.classList.add("custom-widgets");
  for (const w of ORGANIZER_WIDGETS) {
    const node = panels.find((p) =>
      p.querySelector("h2")?.textContent.startsWith(mapping[w]),
    );
    if (!node) continue;
    node.hidden = !organizerLayout.widgets.includes(w);
    node.style.gridColumn =
      organizerLayout.sizes?.[w] === "wide" ? "1 / -1" : "";
    node.style.order = organizerLayout.widgets.indexOf(w);
    grid.append(node);
  }
  grid.querySelectorAll(":scope>.stack").forEach((n) => n.remove());
}

async function previewDisplay() {
  const form = document.querySelector("#display-form"),
    fd = new FormData(form),
    selected = normalizeDisplayWidgets(fd.getAll("widgets")),
    division = fd.get("division_id"),
    court = fd.get("court_id");
  const background = /^#[0-9a-f]{6}$/i.test(fd.get("background") || "")
    ? fd.get("background")
    : "#0e281e";
  const scale = Math.max(0.8, Math.min(2, Number(fd.get("text_scale")) || 1));
  const sizes = Object.fromEntries(
    [...form.querySelectorAll("[data-size]")].map((control) => [
      control.dataset.size,
      control.value,
    ]),
  );
  const ms = items(tournament.matches).filter(
      (m) =>
        (!division || m.division_id === division) &&
        (!court || m.court?.id === court),
    ),
    standings = items(tournament.standings).filter(
      (g) => !division || g.division_id === division,
    );
  let forecast;
  try {
    forecast = await api(
      `/tournaments/${tournament.tournament.id}/weather?units=${fd.get("units") || "celsius"}`,
    );
  } catch {
    forecast = { status: "unavailable", message: "Weather unavailable." };
  }
  const content = {
    live: `<h2>Live games</h2><div class="live-cards">${
      ms
        .filter((m) => ["in_progress", "paused"].includes(m.status))
        .map((m) =>
          matchCard(m, {
            publicView: true,
            timezone: tournament.tournament.timezone,
          }),
        )
        .join("") || empty("No live games")
    }</div>`,
    standings: `<h2>Official standings</h2>${
      standings
        .map((g) => {
          const divisionName =
            g.division_name ||
            items(tournament.divisions).find((d) => d.id === g.division_id)
              ?.name ||
            g.name ||
            "Division standings";
          return `<section class="display-standings-group"><header class="display-standings-heading"><h3>${esc(divisionName)}</h3><p>Finalized results only</p></header>${standingsTable(g.rows || g.table || g.standings || [])}</section>`;
        })
        .join("") || empty("No official standings")
    }`,
    upcoming: `<h2>Up next</h2>${table(
      [
        {
          label: "Teams",
          render: (m) => esc(teamName(m, "a") + " vs " + teamName(m, "b")),
        },
        { label: "Court", render: (m) => esc(courtName(m)) },
        {
          label: "Scheduled",
          render: (m) =>
            esc(dt(m.planned_at, { timeZone: tournament.tournament.timezone })),
        },
      ],
      ms.filter((m) => ["scheduled", "ready"].includes(m.status)).slice(0, 6),
    )}`,
    results: `<h2>Recent results</h2>${table(
      [
        {
          label: "Game",
          render: (m) => esc(teamName(m, "a") + " vs " + teamName(m, "b")),
        },
        { label: "Sets", render: (m) => esc(score(m)) },
      ],
      ms.filter((m) => m.status === "ended").slice(-6),
    )}`,
    announcements: `<h2>Public announcements</h2>${
      items(tournament.announcements)
        .filter((a) => a.visibility === "public")
        .map(
          (a) => "<h3>" + esc(a.title) + "</h3><p>" + esc(a.content) + "</p>",
        )
        .join("") || "<p>No public announcements.</p>"
    }`,
    weather: `<h2>Venue weather</h2>${renderWeather(forecast)}`,
    sponsors: `<h2>Event sponsor</h2>${imageTag(fd.get("sponsor_image"), fd.get("sponsor_name") || "Sponsor", "sponsor-logo")}<h3>${esc(fd.get("sponsor_name") || "No sponsor configured")}</h3>${/^https:\/\//i.test(fd.get("sponsor_url") || "") ? `<a href="${esc(fd.get("sponsor_url"))}" target="_blank" rel="noopener noreferrer">Sponsor website</a>` : ""}`,
    brackets: `<h2>Knockout progression</h2><div class="bracket">${
      items(tournament.stages)
        .filter(
          (s) =>
            s.format === "single_elimination" &&
            (!division || s.division_id === division),
        )
        .map(
          (s) =>
            `<div class="bracket-round"><h3>${esc(s.name)}</h3>${ms
              .filter((m) => m.stage_id === s.id)
              .map(
                (m) =>
                  `<div class="bracket-match"><p>${esc(teamName(m, "a"))} vs ${esc(teamName(m, "b"))}</p><p>${esc(score(m))} · ${esc(pretty(m.status))}</p></div>`,
              )
              .join("")}</div>`,
        )
        .join("") || empty("No knockout stage")
    }</div>`,
  };
  modal(
    "Private presentation preview",
    `<p class="muted small">Private preview of your current form selections. This snapshot uses persisted tournament data. The public display refreshes live scores and rotates selected widgets every ${esc(fd.get("rotation_seconds"))} seconds.</p><div class="display-shell" style="padding:20px;background:${background};--display-ink:${contrastText(background)};--display-scale:${scale};font-size:${scale}em"><header class="display-header"><div>${imageTag(fd.get("logo") || org.logo, org.name + " logo")}<h1>${esc(tournament.tournament.name)}</h1></div></header><div class="display-grid section-gap">${selected.map((w) => `<section class="panel panel-pad ${sizes[w] === "wide" ? "wide-widget" : ""}">` + (content[w] || "") + "</section>").join("")}</div>${credit()}</div>`,
    null,
    { wide: true },
  );
}
function analyticsPanel(title, description, content, rows, columns) {
  return `<section class="panel chart-figure"><h2>${esc(title)}</h2><p class="muted small">${esc(description)}</p>${content}<details class="chart-details"><summary>Accessible data table</summary>${table(columns, rows)}</details></section>`;
}
function bars(rows, label, value, color = "var(--green)") {
  const max = Math.max(1, ...rows.map((r) => Number(r[value]) || 0));
  return rows.length
    ? rows
        .map(
          (r) =>
            `<div class="data-bar"><span>${esc(r[label])}</span><div class="data-bar-track"><div class="data-bar-fill" style="width:${(100 * (Number(r[value]) || 0)) / max}%;background:${color}"></div></div><strong>${esc(r[value])}</strong></div>`,
        )
        .join("")
    : empty("No data for this metric");
}
function lineFigure(rows, value, label) {
  if (rows.length < 2)
    return empty(
      "More recorded activity needed",
      "At least two dates are needed to show a meaningful trend.",
    );
  const max = Math.max(1, ...rows.map((r) => r[value])),
    coords = rows
      .map(
        (r, i) =>
          `${35 + (i * 320) / (rows.length - 1)},${135 - (r[value] * 110) / max}`,
      )
      .join(" ");
  return `<figure><svg viewBox="0 0 390 175" role="img" aria-label="${esc(label)}; table below provides exact values"><line x1="35" y1="135" x2="355" y2="135" class="chart-grid-line"/><line x1="35" y1="80" x2="355" y2="80" class="chart-grid-line"/><line x1="35" y1="25" x2="355" y2="25" class="chart-grid-line"/><polyline points="${coords}" class="chart-lines"/>${rows.map((r, i) => `<circle cx="${35 + (i * 320) / (rows.length - 1)}" cy="${135 - (r[value] * 110) / max}" r="3" fill="var(--green)"/>`).join("")}<text x="5" y="28" class="line-chart-label">${Math.round(max)}</text><text x="12" y="138" class="line-chart-label">0</text><text x="35" y="159" class="line-chart-label">${esc(date(rows[0].day))}</text><text x="355" y="159" text-anchor="end" class="line-chart-label">${esc(date(rows.at(-1).day))}</text></svg><figcaption>${esc(label)} · ${rows.length} recorded dates</figcaption></figure>`;
}
async function eventAnalytics() {
  const d = tournament,
    ms = items(d.matches),
    rows = items(d.standings).flatMap((g) => g.rows || []),
    completed = ms.filter((m) => m.status === "ended").length,
    division = items(d.divisions).map((v) => ({
      division: v.name,
      entries: items(d.entries).filter((e) => e.division_id === v.id).length,
    })),
    teams = rows.map((r) => ({
      team: r.name,
      wins: r.won,
      losses: r.lost,
      points: r.points_for,
      sets: r.sets_won,
    }));
  let usage = [];
  try {
    const report = await api(
      `/reports?type=court_use&tournament_id=${d.tournament.id}&page_size=200`,
    );
    const grouped = {};
    for (const r of report.rows) {
      const key = r.court || "Unassigned";
      grouped[key] = (grouped[key] || 0) + (Number(r.occupied_minutes) || 0);
    }
    usage = Object.entries(grouped).map(([court, minutes]) => ({
      court,
      minutes: Math.round(minutes * 10) / 10,
    }));
  } catch {}
  const activities = {},
    durations = {};
  for (const m of ms) {
    if (m.actual_start) {
      const day = localDateTime(m.actual_start, d.tournament.timezone).slice(
        0,
        10,
      );
      activities[day] = (activities[day] || 0) + 1;
    }
    if (m.actual_end && m.actual_start) {
      const day = localDateTime(m.actual_end, d.tournament.timezone).slice(
        0,
        10,
      );
      (durations[day] ||= []).push(m.elapsed_seconds / 60);
    }
  }
  const ar = Object.entries(activities)
      .sort()
      .map(([day, count]) => ({ day, count })),
    dr = Object.entries(durations)
      .sort()
      .map(([day, values]) => ({
        day,
        minutes:
          Math.round((values.reduce((a, b) => a + b, 0) / values.length) * 10) /
          10,
        sample: values.length,
      }));
  return `<div class="banner">${icon("chart")} Metrics use persisted fixtures, finalized standings, and authorized court assignments. Sample sizes are shown with each chart.</div><div class="analytics-grid">${analyticsPanel(
    "Competition completion",
    `${ms.length} fixtures · Ended games / all fixtures`,
    `<div class="chart-layout"><div class="donut" style="--complete:${ms.length ? (completed / ms.length) * 100 : 0}%;--live:${ms.length ? (completed / ms.length) * 100 : 0}%"><div class="donut-center"><strong>${ms.length ? Math.round((completed / ms.length) * 100) : 0}%</strong><span>Complete</span></div></div><p>${completed} ended<br>${ms.length - completed} open / other</p></div>`,
    [
      { status: "Ended", games: completed },
      { status: "Other", games: ms.length - completed },
    ],
    ["status", "games"],
  )}${analyticsPanel("Teams by division", `${items(d.entries).length} event entries across ${division.length} divisions`, distributionDonut(division), division, ["division", "entries"])}${analyticsPanel("Team wins & losses", `${rows.length} entries · Official finalized results`, teams.map((r) => `<div class="notice"><strong>${esc(r.team)}</strong><p>Wins ${r.wins} · Losses ${r.losses}</p>${bars([r], "team", "wins")}${bars([r], "team", "losses", "#a07c5d")}</div>`).join("") || empty("No finalized results"), teams, ["team", "wins", "losses"])}${analyticsPanel("Team rally points", `${rows.length} entries · Official finalized result totals`, bars(teams, "team", "points"), teams, ["team", "points", "sets"])}${analyticsPanel("Court usage", `${usage.length} courts · Recorded occupation minutes · Up to 200 authorized assignment records`, bars(usage, "court", "minutes"), usage, ["court", "minutes"])}${analyticsPanel("Match activity", `${ms.filter((m) => m.actual_start).length} actual starts · Tournament local dates`, lineFigure(ar, "count", "Actual match starts by date"), ar, ["day", "count"])}${analyticsPanel("Match duration trend", `${ms.filter((m) => m.actual_end && m.actual_start).length} ended games · Average recorded playing minutes`, lineFigure(dr, "minutes", "Average match duration by date"), dr, ["day", "minutes", "sample"])}</div>`;
}

function resolveTie(divisionId) {
  const group = items(tournament.standings).find(
      (g) => g.division_id === divisionId,
    ),
    rows = (group?.rows || []).filter((r) => r.tied);
  modal(
    "Resolve tied ranking",
    `<p class="muted small">Assign a different priority to each tied team: 1 advances first. This audited decision applies after the configured sporting tie breaks.</p>${rows.map((row, index) => field("rank_" + row.entry_id, `${row.name} · shared rank ${row.rank}`, "number", index + 1, `min="1" max="${rows.length}" required`)).join("")}${textarea("reason", "Resolution reason", "", "required")}`,
    async (fd) => {
      const ordered = rows
        .map((row) => ({
          id: row.entry_id,
          rank: Number(fd.get("rank_" + row.entry_id)),
        }))
        .sort((a, b) => a.rank - b.rank);
      if (new Set(ordered.map((row) => row.rank)).size !== rows.length)
        throw new Error("Assign a different priority to every tied team.");
      await api(`/divisions/${divisionId}/resolve-tie`, {
        method: "POST",
        data: {
          order: ordered.map((row) => row.id),
          reason: fd.get("reason"),
        },
      });
      toast("Audited tie resolution saved.");
      render();
    },
    { submit: "Record ranking resolution", wide: true },
  );
}
function editDivision(id) {
  const d = items(tournament.divisions).find((d) => d.id === id);
  modal(
    "Edit division",
    field("name", "Division name", "text", d.name, "required") +
      field("category", "Category", "text", d.category || ""),
    async (fd) => {
      const v = formObject(fd);
      await api(`/divisions/${id}`, { method: "PATCH", data: v });
      toast("Division updated.");
      render();
    },
  );
}
function editEntry(id) {
  const e = items(tournament.entries).find((e) => e.id === id);
  modal(
    "Edit event team entry",
    field("name", "Historical event display name", "text", e.name, "required") +
      field("seed", "Seed", "number", e.seed, 'min="1"') +
      field("pool", "Pool", "text", e.pool || "") +
      select(
        "status",
        "Participation",
        ["active", "withdrawn", "archived"],
        e.status,
      ) +
      textarea(
        "roster_names",
        "Private event roster · one player name per line",
        rosterNames(e.roster),
      ),
    async (fd) => {
      const v = formObject(fd, ["seed"]);
      v.roster = parseRoster(v.roster_names, e.roster);
      delete v.roster_names;
      await api(`/entries/${id}`, { method: "PATCH", data: v });
      toast("Event entry updated.");
      render();
    },
  );
}
function editStage(id) {
  const s = items(tournament.stages).find((s) => s.id === id);
  modal(
    "Edit competition stage",
    stageForm(s),
    async (fd) => {
      const v = stageValues(fd, s.settings);
      await api(`/stages/${id}`, { method: "PATCH", data: v });
      toast("Stage saved.");
      render();
    },
    { wide: true },
  );
  bindStageOptions(s);
}

function distributionDonut(rows) {
  const total = rows.reduce((n, r) => n + r.entries, 0),
    colors = ["#216b50", "#7eae88", "#abcaa4", "#bd9a61", "#6a94a4", "#9c86b3"];
  let accumulated = 0;
  const gradient = rows
    .map((r, i) => {
      const from = accumulated;
      accumulated += total ? (100 * r.entries) / total : 0;
      return `${colors[i % colors.length]} ${from}% ${accumulated}%`;
    })
    .join(",");
  return `<div class="chart-layout"><div class="donut" style="background:${total ? "conic-gradient(" + gradient + ")" : "var(--line)"}" role="img" aria-label="Team entries by division; exact values in table below"><div class="donut-center"><strong>${total}</strong><span>Entries</span></div></div><div class="chart-legend">${rows.map((r, i) => `<div class="legend-row"><span class="legend-color" style="--legend:${colors[i % colors.length]}"></span>${esc(r.division)}<strong>${r.entries}</strong></div>`).join("")}</div></div>`;
}

function editMatchRules(m) {
  const r = m.rules;
  modal(
    "Override this game scoring policy",
    `<p class="muted small">This game only. Rules freeze at Start Game; a consequential override requires a reason.</p><div class="form-grid">${select("mode", "Set mode", ["best_of", "fixed"], r.mode)}${field("best_of", "Best of N", "number", r.best_of, 'min="1" max="9"')}${field("fixed_sets", "Fixed set count", "number", r.fixed_sets, 'min="1" max="9"')}${field("sets_to_win", "Sets needed to win", "number", r.sets_to_win, 'min="1"')}${field("regular_target", "Regular-set target", "number", r.regular_target, 'min="1"')}${field("deciding_target", "Deciding-set target", "number", r.deciding_target, 'min="1"')}${field("win_by", "Winning lead", "number", r.win_by, 'min="1"')}${field("cap", "Hard cap (empty for none)", "number", r.cap ?? "", 'min="1"')}${field("timeouts", "Timeouts per set", "number", r.timeouts, 'min="0" max="10"')}</div><label class="check-field"><input name="draw_allowed" type="checkbox" ${r.draw_allowed ? "checked" : ""}>Draws allowed for fixed-set matches</label>${textarea("reason", "Override reason", "", "required")}`,
    async (fd) => {
      const values = formObject(
        fd,
        [
          "best_of",
          "fixed_sets",
          "sets_to_win",
          "regular_target",
          "deciding_target",
          "win_by",
          "cap",
          "timeouts",
        ],
        ["draw_allowed"],
      );
      const reason = values.reason;
      delete values.reason;
      await api(`/matches/${m.id}`, {
        method: "PATCH",
        data: { rules: values, reason },
      });
      toast("Authorized match scoring override saved.");
      render();
    },
    { wide: true },
  );
}

function bindFixtureSelections() {
  const form = document.querySelector("#modal-form"),
    division = form.querySelector("[name=division_id]");
  const update = () => {
    const selected = division.value;
    for (const name of ["stage_id", "entry_a_id", "entry_b_id"]) {
      const source =
        name === "stage_id"
          ? items(tournament.stages)
          : items(tournament.entries);
      const el = form.querySelector("[name=" + name + "]"),
        previous = el.value;
      el.innerHTML = options(source.filter((r) => r.division_id === selected))
        .map(([v, n]) => `<option value="${esc(v)}">${esc(n)}</option>`)
        .join("");
      if ([...el.options].some((o) => o.value === previous))
        el.value = previous;
    }
    const b = form.querySelector("[name=entry_b_id]");
    if (
      b.options.length > 1 &&
      b.value === form.querySelector("[name=entry_a_id]").value
    )
      b.selectedIndex = 1;
  };
  division.onchange = update;
  update();
}

function upcomingMatches(ms) {
  return ms
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
}

function generationPreview(p) {
  const entries = items(tournament.entries);
  const name = (v, source, bye) => {
    if (v)
      return (
        entries.find((e) => e.id === v)?.name ||
        (v.startsWith("qualifier:")
          ? v.split(":")[2] === "season"
            ? "Official season rank " + v.split(":")[1]
            : "Pool " + v.split(":")[2] + " qualifier " + v.split(":")[1]
          : v.startsWith("wildcard:")
            ? "Wildcard qualifier " + v.split(":")[1]
            : v)
      );
    return source !== undefined
      ? "Winner of game " + (source + 1)
      : bye
        ? "BYE"
        : "TBD";
  };
  const rows = [...items(p.fixtures), ...items(p.playoff_fixtures)];
  return `<p class="muted small">No fixtures have been saved yet. ${p.total ?? rows.length} games · ${esc(pretty(p.format))}. Review participants, qualifiers, and planned local times before saving.</p>${items(
    p.warnings,
  )
    .map((w) => '<div class="banner danger">' + esc(w.message || w) + "</div>")
    .join("")}${table(
    [
      { label: "Round", key: "round" },
      { label: "Team A", render: (r) => esc(name(r.a, r.source_a, r.bye)) },
      { label: "Team B", render: (r) => esc(name(r.b, r.source_b, r.bye)) },
      { label: "Pool", render: (r) => esc(r.pool || "—") },
      {
        label: "Planned",
        render: (r) =>
          esc(dt(r.planned_at, { timeZone: tournament.tournament.timezone })),
      },
    ],
    rows,
  )}<p class="form-help">Courts are assigned operationally. Team rest, referee overlaps, court bookings, and stage dependencies are validated by the server.</p>`;
}
function rankingImpactPreview(impact) {
  return `<p class="muted small">This policy affects finalized results. Review current and proposed official standings and qualification changes before recalculating.</p><h3>Current standings</h3>${items(
    impact.before,
  )
    .map((g) => standingsTable(g.rows || []))
    .join("")}<h3 class="section-gap">Proposed standings</h3>${items(
    impact.after,
  )
    .map((g) => standingsTable(g.rows || []))
    .join(
      "",
    )}<p class="form-help">Current standings generation ${impact.current_generation}. Started downstream games require adjudication if a qualifying participant changes.</p>`;
}

function organizerCard(m) {
  const timezone =
    tournament?.tournament.id === m.tournament_id
      ? tournament.tournament.timezone
      : items(dashboard?.tournaments).find((t) => t.id === m.tournament_id)
          ?.timezone || org?.timezone;
  return matchCard(m, { timezone });
}
