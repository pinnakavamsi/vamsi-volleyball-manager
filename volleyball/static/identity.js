import {
  api,
  setContext,
  esc,
  credit,
  icon,
  field,
  toast,
  errorText,
} from "./common.js";
let admissionToken = "";
export async function start() {
  admissionToken =
    location.hash.slice(1) ||
    new URLSearchParams(location.search).get("token") ||
    "";
  if (admissionToken) history.replaceState(null, "", location.pathname);
  const me = await api("/auth/me", { publicRequest: true });
  setContext(me.csrf_token, "");
  if (me.user && location.pathname === "/login") {
    location.href = "/";
    return;
  }
  render();
}
function render() {
  const invite =
      location.pathname === "/invite" || location.pathname === "/invitation",
    reset = location.pathname === "/reset";
  const completing = reset && !!admissionToken;
  const title = invite
    ? "Join your organizer workspace"
    : completing
      ? "Set a new password"
      : reset
        ? "Reset your password"
        : "Welcome back";
  const desc = invite
    ? "Accept your organization invitation with a global organizer identity."
    : completing
      ? "Choose a unique password with at least 12 characters."
      : reset
        ? "Enter your email to request a password reset."
        : "Sign in to your platform administration or organization workspace.";
  document.querySelector("#app").innerHTML =
    `<div class="auth-layout"><aside class="auth-brand"><span class="brand"><img src="/static/brand.svg" alt=""><span>Courtline<span class="product">VOLLEYBALL MANAGER</span></span></span><div><span class="eyebrow" style="color:#b6d3b1">EVERY COURT. EVERY POINT.</span><h1>Good games.<br>Great organization.</h1><p>A clear view of your competitions, from the first fixture to the final whistle.</p></div>${credit()}</aside><main id="main" class="auth-main"><div class="auth-box"><a href="/login" class="brand" style="margin-bottom:40px"><img src="/static/brand.svg" alt="">Courtline</a><span class="eyebrow">ORGANIZER ACCESS</span><h1 style="margin-top:9px">${title}</h1><p>${desc}</p><form id="identity-form" class="auth-form">${invite ? field("name", "Your name", "text", "", 'required autocomplete="name"') : ""}${!completing && !invite ? field("email", "Email address", "email", "", 'required autocomplete="username"') : ""}${!reset || completing ? field("password", invite || completing ? "Create password" : "Password", "password", "", 'required autocomplete="' + (invite || completing ? "new-password" : "current-password") + '" ' + (invite || completing ? 'minlength="12"' : "")) : ""}${!reset && !invite ? field("otp", "Authenticator or recovery code (if enabled)", "text", "", 'autocomplete="one-time-code"') : ""}${invite && !admissionToken ? '<div class="form-error">Open the invitation link sent to you to accept membership.</div>' : ""}<div class="form-error" id="auth-error" role="alert" hidden></div><button class="button primary" ${invite && !admissionToken ? "disabled" : ""}>${invite ? "Accept invitation" : completing ? "Set password" : reset ? "Send reset instructions" : "Sign in"} ${icon("arrow")}</button></form><div class="auth-bottom">${!reset && !invite ? '<a href="/reset">Forgot your password?</a>' : '<a href="/login">Back to sign in</a>'}</div><p class="form-help" style="text-align:center;margin-top:25px">Spectators view published tournaments without an account. Referees scan a private game QR.</p></div>${credit()}</main></div>`;
  document.querySelector("#identity-form").onsubmit = async (e) => {
    e.preventDefault();
    const b = e.target.querySelector("button"),
      err = document.querySelector("#auth-error");
    b.disabled = true;
    err.hidden = true;
    const data = Object.fromEntries(new FormData(e.target));
    try {
      if (invite) {
        await api("/auth/invitations/accept", {
          method: "POST",
          data: { ...data, token: admissionToken },
          publicRequest: true,
        });
        toast("Invitation accepted. Sign in to your workspace.");
        location.href = "/login";
      } else if (completing) {
        await api("/auth/reset/complete", {
          method: "POST",
          data: { ...data, token: admissionToken },
          publicRequest: true,
        });
        toast("Password updated. Sign in with the new password.");
        location.href = "/login";
      } else if (reset) {
        const r = await api("/auth/reset/request", {
          method: "POST",
          data,
          publicRequest: true,
        });
        document.querySelector(".auth-box").innerHTML =
          `<h1>Check your reset instructions</h1><p class="muted">${esc(r.message || "If a matching account exists, reset instructions will be delivered by the configured email adapter.")}</p><a href="/login" class="button primary">Back to sign in</a>`;
      } else {
        await api("/auth/login", { method: "POST", data, publicRequest: true });
        location.href = "/";
      }
    } catch (error) {
      err.innerHTML = errorText(error);
      err.hidden = false;
      b.disabled = false;
    }
  };
}
