const mode = document.body.dataset.experience;
document.querySelector(".skip-link")?.addEventListener("click", (event) => {
  event.preventDefault();
  const main = document.querySelector("#main");
  if (main) {
    main.setAttribute("tabindex", "-1");
    main.focus();
    main.scrollIntoView({ block: "start" });
  }
});
const module =
  mode === "organizer"
    ? "./app.js"
    : mode === "referee"
      ? "./referee.js"
      : mode === "identity"
        ? "./identity.js"
        : "./public.js";
import(module)
  .then((m) => m.start())
  .catch((err) => {
    document.querySelector("#app").innerHTML =
      '<main id="main" class="initial-load"><h1>Unable to open Courtline</h1><p>Refresh this page to try again.</p><button id="retry-boot">Refresh</button><p class="credit">Designed and developed by Vamsi Pinnaka</p></main>';
    document.querySelector("#retry-boot").onclick = () => location.reload();
    console.error("Application initialization failed", err?.message);
  });
