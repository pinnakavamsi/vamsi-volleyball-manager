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
let setupRequired = false;
export async function start() {
  admissionToken =
    location.hash.slice(1) ||
    new URLSearchParams(location.search).get("token") ||
    "";
  if (admissionToken) history.replaceState(null, "", location.pathname);
  const me = await api("/auth/me", { publicRequest: true });
  setContext(me.csrf_token, "");
  setupRequired = Boolean(me.setup_required);
  if (setupRequired && ["/login", "/setup"].includes(location.pathname)) {
    if (location.pathname !== "/setup") history.replaceState(null, "", "/setup");
    renderSetup();
    return;
  }
  if (location.pathname === "/setup") {
    location.replace(me.user ? "/" : "/login");
    return;
  }
  if (me.user && location.pathname === "/login") {
    location.href = "/";
    return;
  }
  render();
}

function renderSetup() {
  document.querySelector("#app").innerHTML =
    `<div class="auth-layout"><aside class="auth-brand"><span class="brand"><img src="/static/brand.svg" alt=""><span>Courtline<span class="product">VOLLEYBALL MANAGER</span></span></span><div><span class="eyebrow" style="color:#b6d3b1">YOUR PLATFORM STARTS HERE</span><h1>Ready for<br>your first serve.</h1><p>Create your owner account, then add organizations and competitions when you are ready.</p></div>${credit()}</aside><main id="main" class="auth-main"><div class="auth-box"><span class="eyebrow">FIRST ACCESS</span><h1>Create your owner account</h1><p>This account manages platform administration. Choose your own credentials; organizations and tournament data begin empty.</p><form id="setup-form" class="auth-form">${field("name", "Your name", "text", "", 'required maxlength="120" autocomplete="name"')}${field("email", "Email address", "email", "", 'required maxlength="254" autocomplete="username"')}${field("password", "Create password", "password", "", 'required minlength="12" maxlength="256" autocomplete="new-password" aria-describedby="setup-password-help"')}<p id="setup-password-help" class="form-help">Use a unique password or passphrase with at least 12 characters. You can enroll an authenticator in Profile and security after setup.</p>${field("confirm_password", "Confirm password", "password", "", 'required minlength="12" maxlength="256" autocomplete="new-password"')}<div class="form-error" id="setup-error" role="alert" hidden></div><button class="button primary">Create owner account ${icon("arrow")}</button></form><p class="form-help" style="margin-top:25px">First-access setup closes permanently after a platform administrator is created. Keep your credentials private.</p></div>${credit()}</main></div>`;
  document.querySelector("#setup-form").onsubmit = async (event) => {
    event.preventDefault();
    const form = event.target, button = form.querySelector("button"), errorBox = document.querySelector("#setup-error");
    const data = Object.fromEntries(new FormData(form));
    errorBox.hidden = true;
    if (data.password !== data.confirm_password) {
      errorBox.textContent = "Passwords do not match. Enter the same password twice.";
      errorBox.hidden = false;
      form.elements.confirm_password.focus();
      return;
    }
    button.disabled = true;
    button.textContent = "Creating owner account…";
    try {
      await api("/auth/setup", {method: "POST", data: {email: data.email, name: data.name, password: data.password}, publicRequest: true});
      form.reset();
      location.replace("/#platform");
    } catch (error) {
      errorBox.innerHTML = errorText(error);
      errorBox.hidden = false;
      if (error.code === "setup_complete") {
        form.reset();
        errorBox.insertAdjacentHTML("beforeend", '<p><a href="/login">Continue to sign in</a></p>');
        button.hidden = true;
      } else {
        button.disabled = false;
        button.innerHTML = `Create owner account ${icon("arrow")}`;
      }
    }
  };
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
