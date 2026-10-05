export const CREDIT = "Designed and developed by Vamsi Pinnaka";
export const credit = () => `<footer class="credit">${CREDIT}</footer>`;
export const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
export const items = (v) => (Array.isArray(v) ? v : v?.items || []);
export const pretty = (v) =>
  String(v ?? "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
export const dt = (v, options = {}) =>
  v
    ? new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        ...options,
      }).format(new Date(v))
    : "Not scheduled";
export const date = (v) =>
  v
    ? new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      }).format(new Date(/^\d{4}-\d{2}-\d{2}$/.test(v) ? v + "T12:00:00" : v))
    : "Dates to be confirmed";
export const badge = (v, cls = "") =>
  `<span class="badge ${esc(cls || v)}">${esc(pretty(v))}</span>`;
export const empty = (title, desc = "Add your first record to get started.") =>
  `<div class="empty-state"><span class="empty-icon" aria-hidden="true">◇</span><h3>${esc(title)}</h3><p>${esc(desc)}</p></div>`;
export const icon = (name) =>
  `<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${{ grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>', trophy: '<path d="M8 3h8v7a4 4 0 0 1-8 0V3ZM8 5H4v3a4 4 0 0 0 4 4m8-7h4v3a4 4 0 0 1-4 4M12 14v6m-4 1h8"/>', team: '<circle cx="9" cy="7" r="3"/><path d="M3 20v-3a6 6 0 0 1 12 0v3m2-16a3 3 0 0 1 0 6m2 4a5 5 0 0 1 2 4v2"/>', court: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M12 5v14M3 9h18M3 15h18"/>', calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 11h18"/>', chart: '<path d="M4 3v17h17M8 16v-5m5 5V7m5 9V4"/>', settings: '<circle cx="12" cy="12" r="3"/><path d="m9 3 6 0 .8 3 2.7 1.5 3 1-3 7-2.5 2L15 21H9l-1-3-3-2-2-7 3-1 2-2Z"/>', arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>', bell: '<path d="M6 9a6 6 0 0 1 12 0v6l2 3H4l2-3V9m4 12h4"/>', search: '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>', plus: '<path d="M12 5v14M5 12h14"/>', logout: '<path d="M9 4H4v16h5m5-13 5 5-5 5M9 12h12"/>', check: '<path d="m5 12 4 4L19 6"/>', sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1"/>', shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>', help: '<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 0 1 6 0c0 2-3 2-3 4m0 4h.01"/>', menu: '<path d="M4 6h16M4 12h16M4 18h16"/>', refresh: '<path d="M20 7v5h-5M4 17v-5h5M5 7a8 8 0 0 1 14-1M19 17A8 8 0 0 1 5 18"/>', download: '<path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4"/>', link: '<path d="m10 13 4-4m-6 7-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2 1 1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0"/>', clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', location: '<path d="M19 10c0 6-7 11-7 11S5 16 5 10a7 7 0 0 1 14 0Z"/><circle cx="12" cy="10" r="2"/>' }[name] || '<circle cx="12" cy="12" r="8"/>'}</svg>`;

export let csrf = "";
export let orgId = "";
export function setContext(token, org) {
  if (token) csrf = token;
  if (org !== undefined) orgId = org;
}
export class APIError extends Error {
  constructor(error, status) {
    super(error?.message || "The request could not be completed.");
    this.status = status;
    this.code = error?.code;
    this.details = error?.details;
  }
}
export async function api(
  path,
  {
    method = "GET",
    data,
    body,
    headers = {},
    signal,
    publicRequest = false,
  } = {},
) {
  const h = { ...headers };
  if (orgId && !publicRequest) h["X-Organization-ID"] = orgId;
  if (method !== "GET") h["X-CSRF-Token"] = csrf;
  if (data !== undefined) {
    h["Content-Type"] = "application/json";
    body = JSON.stringify(data);
  }
  let response;
  try {
    response = await fetch(`/api/v1${path}`, {
      method,
      body,
      headers: h,
      credentials: "same-origin",
      cache: "no-store",
      signal,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new APIError(
      {
        code: "offline",
        message: "Connection interrupted. Check your connection and try again.",
      },
      0,
    );
  }
  const result =
    response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok)
    throw new APIError(
      result.error || {
        message:
          response.status === 401
            ? "Please sign in to continue."
            : "The request could not be completed.",
      },
      response.status,
    );
  if (result.csrf_token) csrf = result.csrf_token;
  return result;
}
let toastTimer;
export function toast(message, error = false) {
  const node = document.querySelector("#toast");
  node.textContent = message;
  node.className = `toast visible ${error ? "error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("visible"), 5000);
}
export function errorText(e) {
  return `${esc(e.message)}${e.details ? `<details><summary>View details</summary><pre>${esc(JSON.stringify(e.details, null, 2))}</pre></details>` : ""}`;
}
export function closeDialog() {
  document.querySelector("#dialog").close();
  document.querySelector("#dialog").innerHTML = "";
}
export function modal(
  title,
  content,
  onSubmit,
  { submit = "Save changes", wide = false } = {},
) {
  const dialog = document.querySelector("#dialog");
  dialog.className = wide ? "wide" : "";
  dialog.innerHTML = `<form id="modal-form"><header class="dialog-header"><div><span class="eyebrow">COURTLINE WORKSPACE</span><h2 id="dialog-title">${esc(title)}</h2></div><button type="button" class="icon-button" aria-label="Close dialog" id="dialog-close">×</button></header><div class="dialog-content">${content}<div class="form-error" role="alert" hidden></div></div><footer class="dialog-footer"><button type="button" class="button secondary" id="dialog-cancel">Cancel</button>${onSubmit ? `<button class="button primary" type="submit">${esc(submit)}</button>` : ""}</footer></form>`;
  const form = dialog.querySelector("form");
  const dismiss = () => {
    if (dialog.querySelector("form") !== form) return;
    dialog.close();
    dialog.innerHTML = "";
  };
  dialog.querySelector("#dialog-close").onclick = dismiss;
  dialog.querySelector("#dialog-cancel").onclick = dismiss;
  dialog.onclose = () => {
    if (!dialog.open) dialog.innerHTML = "";
  };
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (!onSubmit) return;
    const btn = dialog.querySelector("[type=submit]");
    btn.disabled = true;
    const error = dialog.querySelector(".form-error");
    error.hidden = true;
    try {
      await onSubmit(new FormData(event.target), event.target);
      dismiss();
    } catch (e) {
      if (dialog.querySelector("form") !== form || !dialog.open) return;
      error.innerHTML = errorText(e);
      error.hidden = false;
      btn.disabled = false;
    }
  };
  if (!dialog.open) dialog.showModal();
}
export async function confirm(title, text, action = "Confirm") {
  return new Promise((resolve) => {
    modal(title, `<p>${esc(text)}</p>`, async () => resolve(true), {
      submit: action,
    });
    const d = document.querySelector("#dialog");
    const onClose = () => {
      if (d.open) return;
      d.removeEventListener("close", onClose);
      resolve(false);
    };
    d.addEventListener("close", onClose);
  });
}
export function field(name, label, type = "text", value = "", extra = "") {
  return `<label class="field"><span>${esc(label)}</span><input name="${esc(name)}" type="${type}" value="${esc(value)}" ${extra}></label>`;
}
export function select(name, label, options, value = "", extra = "") {
  return `<label class="field"><span>${esc(label)}</span><select name="${esc(name)}" ${extra}>${options
    .map((o) => {
      const [v, l] = Array.isArray(o) ? o : [o, pretty(o)];
      return `<option value="${esc(v)}" ${String(v) === String(value) ? "selected" : ""}>${esc(l)}</option>`;
    })
    .join("")}</select></label>`;
}
export const textarea = (name, label, value = "", extra = "") =>
  `<label class="field"><span>${esc(label)}</span><textarea name="${esc(name)}" rows="3" ${extra}>${esc(value)}</textarea></label>`;
export function formObject(fd, numeric = [], boolean = []) {
  const obj = Object.fromEntries(fd);
  numeric.forEach((k) => {
    if (k in obj) obj[k] = obj[k] === "" ? null : Number(obj[k]);
  });
  boolean.forEach((k) => {
    obj[k] = fd.get(k) === "on" || fd.get(k) === "true";
  });
  return obj;
}
export const teamName = (m, side) =>
  m[`team_${side}`]?.name || m[`team_${side}_name`] || "TBD";
export const courtName = (m) =>
  m.court?.name || m.court_name || "Court to be assigned";
export function score(m) {
  const ss = m.state?.sets || m.sets || [];
  return ss.length ? ss.map((s) => `${s.a}–${s.b}`).join(" · ") : "—";
}
export function matchCard(m, { publicView = false, timezone } = {}) {
  const current = m.state?.sets?.[m.state?.set_index || 0];
  return `<${publicView ? "div" : "button"} ${publicView ? "" : `type="button" data-match="${esc(m.id)}"`} class="match-card"><div class="match-meta">${badge(m.status)}<span>${esc(courtName(m))}</span></div><div class="match-team"><span><i class="team-dot" style="--team:${esc(m.team_a?.color || "#35a27d")}"></i>${esc(teamName(m, "a"))}</span><strong>${current?.a ?? "—"}</strong></div><div class="match-team"><span><i class="team-dot" style="--team:${esc(m.team_b?.color || "#597dc5")}"></i>${esc(teamName(m, "b"))}</span><strong>${current?.b ?? "—"}</strong></div><div class="match-foot"><span>${esc(dt(m.planned_at, timezone ? { timeZone: timezone } : {}))}</span><span>${esc(score(m))}</span></div></${publicView ? "div" : "button"}>`;
}
export function table(
  columns,
  rows,
  { emptyText = "No records match these filters.", rowAttrs = () => "" } = {},
) {
  return `<div class="table-scroll" tabindex="0" role="region" aria-label="Records table; scroll for more columns"><table><thead><tr>${columns.map((c) => `<th scope="col">${esc(typeof c === "string" ? c : c.label || "Actions")}</th>`).join("")}</tr></thead><tbody>${rows.length ? rows.map((r) => `<tr ${rowAttrs(r)}>${columns.map((c) => `<td>${c.render ? c.render(r) : esc(typeof r[c.key || c] === "object" ? JSON.stringify(r[c.key || c]) : r[c.key || c])}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${columns.length}" class="table-empty">${esc(emptyText)}</td></tr>`}</tbody></table></div>`;
}
export async function download(path) {
  const h = {};
  if (orgId) h["X-Organization-ID"] = orgId;
  const r = await fetch(`/api/v1${path}`, {
    headers: h,
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    throw new APIError(e.error, r.status);
  }
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download =
    r.headers.get("Content-Disposition")?.match(/filename="?([^";]+)/)?.[1] ||
    "courtline-export";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function theme(value) {
  document.documentElement.dataset.theme = value;
  try {
    localStorage.setItem("courtline-theme", value);
  } catch {}
}
try {
  theme(localStorage.getItem("courtline-theme") || "light");
} catch {
  theme("light");
}
export function zonedISO(value, timeZone) {
  if (!value) return null;
  const [calendar, clock] = value.split("T"),
    parts = calendar.split("-").map(Number),
    time = clock.split(":").map(Number);
  const desired = Date.UTC(
    parts[0],
    parts[1] - 1,
    parts[2],
    time[0],
    time[1],
    time[2] || 0,
  );
  let instant = desired;
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  for (let i = 0; i < 3; i++) {
    const p = Object.fromEntries(
      formatter.formatToParts(new Date(instant)).map((x) => [x.type, x.value]),
    );
    const wall = Date.UTC(
      Number(p.year),
      Number(p.month) - 1,
      Number(p.day),
      Number(p.hour),
      Number(p.minute),
      Number(p.second),
    );
    instant += desired - wall;
  }
  const p = Object.fromEntries(
    formatter.formatToParts(new Date(instant)).map((x) => [x.type, x.value]),
  );
  if (
    Number(p.year) !== parts[0] ||
    Number(p.month) !== parts[1] ||
    Number(p.day) !== parts[2] ||
    Number(p.hour) !== time[0] ||
    Number(p.minute) !== time[1]
  )
    throw new Error(
      "This local time does not exist in the selected timezone. Choose a valid time.",
    );
  return new Date(instant).toISOString();
}
export function localDateTime(value, timeZone) {
  if (!value) return "";
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(new Date(value))
      .map((x) => [x.type, x.value]),
  );
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
}

export function contrastText(color) {
  if (!/^#[0-9a-f]{6}$/i.test(color || "")) return "#fff";
  const channels = [1, 3, 5]
    .map((i) => parseInt(color.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  const l = channels.reduce(
    (a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i],
    0,
  );
  return l > 0.179 ? "#000" : "#fff";
}
export function imageTag(url, alt, className = "org-logo") {
  if (!url || (!/^https:\/\//i.test(url) && !/^\/(static|uploads)\//.test(url)))
    return "";
  return `<img src="${esc(url)}" alt="${esc(alt)}" class="${esc(className)}" loading="lazy" referrerpolicy="no-referrer">`;
}
