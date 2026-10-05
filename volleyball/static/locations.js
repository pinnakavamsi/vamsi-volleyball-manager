import { api, esc, items } from "./common.js";

export function addressFields(address = "") {
  return `<div class="address-picker" data-address-picker><label class="field"><span>Street address</span><input name="address" value="${esc(address)}" placeholder="Start typing a venue or street address…" maxlength="1000" autocomplete="off" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="address-suggestions" aria-describedby="address-search-status"></label><input type="hidden" name="address_selection_token" disabled><input type="hidden" name="address_id" disabled><div id="address-suggestions" class="address-suggestions" role="listbox" aria-label="Address suggestions" hidden></div><p id="address-search-status" class="form-help" role="status" aria-live="polite">Search your address book and street addresses. Choose a suggestion to enable weather, or enter the address manually.</p><p class="address-attribution small muted" hidden></p></div>`;
}

export function bindAddressSearch(form, { tournamentId } = {}) {
  const root = form?.querySelector("[data-address-picker]");
  if (!root) return;
  const input = root.querySelector("[name=address]"),
    token = root.querySelector("[name=address_selection_token]"),
    addressId = root.querySelector("[name=address_id]"),
    list = root.querySelector("[role=listbox]"),
    status = root.querySelector("[role=status]"),
    attribution = root.querySelector(".address-attribution"),
    timezone = form.querySelector("[name=timezone]"),
    dialog = form.closest("dialog");
  let suggestions = [], selected = -1, timer, request, sequence = 0, disposed = false, automaticTimezone, previousTimezone;
  const current = () => !disposed && form.isConnected && (!dialog || dialog.open);
  const close = () => {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    selected = -1;
  };
  const cancel = () => {
    clearTimeout(timer);
    request?.abort();
    request = null;
    sequence++;
  };
  const clearSelection = () => {
    if (automaticTimezone && timezone?.value === automaticTimezone) timezone.value = previousTimezone;
    automaticTimezone = previousTimezone = null;
    token.value = "";
    token.disabled = true;
    addressId.value = "";
    addressId.disabled = true;
  };
  const highlight = (index) => {
    if (!suggestions.length || list.hidden) return;
    selected = (index + suggestions.length) % suggestions.length;
    list.querySelectorAll("[role=option]").forEach((option, i) => {
      option.setAttribute("aria-selected", String(i === selected));
      if (i === selected) {
        input.setAttribute("aria-activedescendant", option.id);
        option.scrollIntoView({ block: "nearest" });
      }
    });
  };
  const choose = (index) => {
    const choice = suggestions[index];
    if (!choice || !current()) return;
    cancel();
    clearSelection();
    input.value = choice.address ?? choice.label ?? "";
    if (choice.selection_token) {
      token.value = choice.selection_token;
      token.disabled = false;
    } else if (choice.source === "saved" && choice.id) {
      addressId.value = choice.id;
      addressId.disabled = false;
    }
    if (choice.timezone && timezone) {
      previousTimezone = timezone.value;
      automaticTimezone = choice.timezone;
      timezone.value = choice.timezone;
    }
    close();
    status.textContent = choice.resolved === false
      ? "Saved address selected. Choose a matching street address suggestion to enable weather."
      : "Address selected. Available event weather will use this location after the venue is saved.";
    input.focus();
  };
  const search = async (retryAttempt = 0) => {
    const query = input.value.trim();
    if (!current() || query.length < 3 || document.activeElement !== input) return;
    cancel();
    const version = sequence;
    request = new AbortController();
    status.textContent = "Searching your address book and street addresses…";
    try {
      const scope = tournamentId ? `&tournament_id=${encodeURIComponent(tournamentId)}` : "";
      const response = await api(`/addresses/search?q=${encodeURIComponent(query.slice(0, 200))}${scope}`, { signal: request.signal });
      if (!current() || version !== sequence || input.value.trim() !== query) return;
      suggestions = items(response.items);
      list.innerHTML = suggestions.map((choice, index) => `<button type="button" class="address-option" id="address-option-${index}" role="option" aria-selected="false" tabindex="-1" data-address-option="${index}"><span>${esc(choice.label || choice.address)}</span><small>${choice.source === "saved" ? "Address book" : "Street address"}${choice.address && choice.address !== choice.label ? ` · ${esc(choice.address)}` : ""}</small></button>`).join("");
      list.hidden = !suggestions.length;
      input.setAttribute("aria-expanded", String(suggestions.length > 0));
      status.textContent = response.message || (suggestions.length ? `${suggestions.length} suggestions. Use the arrow keys and Enter to choose an address.` : "No matching address found. You can enter it manually; weather needs a resolved address.");
      attribution.textContent = response.attribution || "";
      attribution.hidden = !response.attribution;
      selected = -1;
      if (response.status === "busy" && retryAttempt < 1) {
        const delay = Math.min(2500, Math.max(1100, Number(response.retry_after || 1) * 1000 + 100));
        timer = setTimeout(() => {
          if (current() && version === sequence && input.value.trim() === query) search(retryAttempt + 1);
        }, delay);
      }
    } catch (error) {
      if (error.name === "AbortError" || !current() || version !== sequence) return;
      close();
      status.textContent = "Address suggestions are unavailable. Enter the address manually and try the search again later.";
    }
  };
  input.addEventListener("input", () => {
    cancel();
    clearSelection();
    suggestions = [];
    close();
    attribution.hidden = true;
    if (input.value.trim().length < 3) {
      status.textContent = "Type at least 3 characters to search your address book and street addresses.";
      return;
    }
    status.textContent = "Choose an address suggestion to enable weather, or finish entering the address manually.";
    timer = setTimeout(search, 350);
  });
  input.addEventListener("focus", () => {
    if (!token.value && !addressId.value && input.value.trim().length >= 3) {
      clearTimeout(timer);
      timer = setTimeout(search, 350);
    }
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (!list.hidden) event.preventDefault();
      cancel();
      close();
      return;
    }
    if (list.hidden || !suggestions.length) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      highlight(selected < 0 ? (event.key === "ArrowDown" ? 0 : suggestions.length - 1) : selected + (event.key === "ArrowDown" ? 1 : -1));
    } else if (event.key === "Enter" && selected >= 0) {
      event.preventDefault();
      choose(selected);
    } else if (event.key === "Tab") close();
  });
  input.addEventListener("blur", () => { cancel(); close(); });
  list.addEventListener("pointerdown", (event) => {
    if (event.target.closest("[data-address-option]")) event.preventDefault();
  });
  list.addEventListener("click", (event) => {
    const option = event.target.closest("[data-address-option]");
    if (option) choose(Number(option.dataset.addressOption));
  });
  const dispose = () => {
    disposed = true;
    cancel();
    observer.disconnect();
    dialog?.removeEventListener("close", dispose);
    window.removeEventListener("hashchange", dispose);
    window.removeEventListener("pagehide", dispose);
  };
  const observer = new MutationObserver(() => { if (!form.isConnected) dispose(); });
  observer.observe(dialog || document.body, { childList: true, subtree: true });
  dialog?.addEventListener("close", dispose);
  window.addEventListener("hashchange", dispose);
  window.addEventListener("pagehide", dispose);
}
