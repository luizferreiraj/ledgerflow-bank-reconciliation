/* Reconciliation workbench — front-end, no framework. EXCERPT of 2,693 lines.
 *
 * Three screens over the same API. No business rule lives here: an amount comes from
 * the server already as a decimal string and is only formatted, never recomputed in
 * JavaScript — `0.1 + 0.2` in float is exactly the error the statement parser exists to
 * prevent, and there is no sense in reintroducing it at display time.
 *
 * What is kept below: the portal token handoff and its renewal (the client side of
 * ADR 7), the outdated-server detection, and `cents()`. `// …` marks what was left out.
 *
 * Identifiers and comments are in English; the text the operator reads on screen stays
 * in Portuguese, as it is on the screenshots. Strings that name something outside this
 * file stay as they are too: element ids and CSS class names (the published
 * `styles.css` and `index.html` use them), and the portal handshake — its page, its
 * window name, the `tipo` field of its postMessage envelope with the three type names it
 * carries, and the sessionStorage key. API paths follow the routes published in
 * `workbench/app.py`.
 */

const $ = (s) => document.querySelector(s);

// …  the `state` object, `money`/`dateBR` formatting and `$$`

function notify(string) {
  const box = $("#aviso");
  box.textContent = string;
  box.classList.remove("oculto");
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => box.classList.add("oculto"), 2600);
}

// ── The portal login ─────────────────────────────────────────────────────────
// Opened by the portal, the workbench gets the token in the URL ANCHOR (#at=…), not in
// the query. The anchor is not sent to the server, does not land in an access log and
// does not leak through the Referer header — and the token is the live credential of
// whoever opened it.
//
// Kept in sessionStorage: it lasts while the tab is open, and goes away when it closes.
// Running on your own machine, with no login gate, there is no token and none of this
// changes the behaviour.
const TOKEN_KEY = "bancada_token";
let tokenInMemory = null;

function takeTokenFromUrl() {
  const anchor = new URLSearchParams(location.hash.slice(1));
  const token = anchor.get("at");
  if (!token) return false;
  storeToken(token);
  // Clears the URL so the token is not visible in the address bar or in the history.
  history.replaceState(null, "", location.pathname + location.search);
  return true;
}

takeTokenFromUrl();

// Pasting `#at=…` into a tab that is ALREADY on the workbench does not reload the page:
// changing only the anchor is navigation within the same document, and no script runs
// again. Without this, the person pastes the token, nothing happens, and the screen goes
// on saying "não autenticado" — with the token right there in the address bar.
//
// It happened on the first real access, and the operator only got out with Ctrl+Shift+R.
window.addEventListener("hashchange", () => {
  if (takeTokenFromUrl()) {
    // Reloads so the whole screen is reborn authenticated. Just reacting to the event is
    // not enough: whatever already failed for want of a token would stay empty.
    location.reload();
  }
});

// The key that came in the anchor and the ones renewal brings later: one single place.
function storeToken(token) {
  try {
    sessionStorage.setItem(TOKEN_KEY, token);
  } catch (_) {
    // Private tab with storage blocked: carry on in memory anyway.
  }
  tokenInMemory = token;
}

function portalToken() {
  if (tokenInMemory) return tokenInMemory;
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch (_) {
    return null;
  }
}

// …  `runImport()`: POSTs to /api/companies/{id}/statements, gets a ticket back and polls
//    /api/imports/{ticket} every 500 ms, showing the queue position while it waits

// ── Renewing the key without leaving the workbench ──────────────────────────
// The portal's key is valid for ONE HOUR — that is the Supabase default. The portal
// renews its own; the workbench only ever received the key, with no way to renew it, and
// after an hour of work the screen said "não autorizado" and the person had to close the
// tab and open it again from the portal.
//
// The portal opens the workbench with `window.open`, so the workbench tab knows who
// opened it (`window.opener`). When the key is about to expire, it asks that tab for a
// new one, and that tab answers only to the workbench's exact address. The login model
// does not change: the key is still the Supabase one, checked by the server on every
// request.
//
// No timer: the expiry is written inside the key itself, and the workbench checks it
// before every request. Sitting idle on screen, it costs nothing.
//
// If the portal tab was closed, or navigated away from the workbench page, nobody
// answers: a strip shows up with "Renovar acesso", which opens the portal in a small
// window — one click, no reload and nothing lost on screen.

// Renews when less than 5 minutes are left: a request must not go out with the key
// expiring on the way.
const RENEW_BEFORE = 300;
// How long to wait for the portal tab to answer.
let PORTAL_WAIT = 4000;

function secondsUntilExpiry(token) {
  try {
    const part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const body = JSON.parse(atob(part));
    return typeof body.exp === "number" ? body.exp - Date.now() / 1000 : null;
  } catch (_) {
    return null;
  }
}

let knownOrigins = null;
async function portalOrigins() {
  if (knownOrigins) return knownOrigins;
  try {
    const version = await fetch("/api/version").then((r) => (r.ok ? r.json() : {}));
    knownOrigins = Array.isArray(version.portal) ? version.portal : [];
  } catch (_) {
    knownOrigins = [];
  }
  return knownOrigins;
}

/* The key only counts if it comes FROM the window that was asked AND from a portal
   origin. Accepting it from any window would let any page that opened the workbench walk
   into it with somebody else's login. */
function waitForToken(portalWindow, origins, wait = PORTAL_WAIT) {
  return new Promise((resolve) => {
    let deadline = null;
    function listen(event) {
      if (event.source !== portalWindow || !origins.includes(event.origin)) return;
      const data = event.data || {};
      if (data.tipo === "bancada:token" && typeof data.token === "string" && data.token) {
        storeToken(data.token);
        finish(true);
      } else if (data.tipo === "bancada:sem-sessao") {
        finish(false);
      }
    }
    function finish(ok) {
      clearTimeout(deadline);
      window.removeEventListener("message", listen);
      resolve(ok);
    }
    window.addEventListener("message", listen);
    deadline = setTimeout(() => finish(false), wait);
  });
}

async function askThePortal() {
  const portalWindow = typeof window !== "undefined" ? window.opener : null;
  if (!portalWindow || portalWindow.closed) return false;
  const origins = await portalOrigins();
  if (!origins.length) return false;
  const response = waitForToken(portalWindow, origins);
  for (const origin of origins) {
    try {
      // Addressed to each portal origin: if the tab that opened the workbench is from
      // another origin, the browser simply does not deliver it.
      portalWindow.postMessage({ tipo: "bancada:pede-token" }, origin);
    } catch (_) {
      /* origin misspelled in the configuration: move on to the next */
    }
  }
  return response;
}

// One renewal at a time: requests that expire together wait for the same answer.
let renewing = null;
// And, with no answer, a minute of pause before asking again. With the portal tab open on
// ANOTHER page, nobody answers — and without the pause every request the screen makes, in
// the last five minutes of the key, would wait four seconds.
const PAUSE_AFTER_FAILURE = 60000;
let failedAt = 0;
function renewToken() {
  if (renewing) return renewing;
  if (Date.now() - failedAt < PAUSE_AFTER_FAILURE) return Promise.resolve(false);
  renewing = askThePortal()
    .then((ok) => {
      if (!ok) failedAt = Date.now();
      return ok;
    })
    .finally(() => (renewing = null));
  return renewing;
}

async function validToken() {
  const token = portalToken();
  if (!token) return null;
  const remaining = secondsUntilExpiry(token);
  if (remaining !== null && remaining < RENEW_BEFORE && (await renewToken())) {
    return portalToken();
  }
  return token;
}

/* When the portal tab does not answer. Opening a window needs a click — the browser
   blocks a window opened on its own —, and that is why this is a button. */
function showRenewStrip() {
  if (typeof document === "undefined" || document.getElementById("faixa-renovar")) return;
  portalOrigins(); // fetch already, so the click opens the window without waiting
  const strip = document.createElement("div");
  strip.id = "faixa-renovar";
  strip.className = "faixa-renovar";
  strip.innerHTML = `Seu acesso do portal venceu.
    <button class="botao" type="button">Renovar acesso</button>
    <span class="muted pequeno">abre o portal numa janelinha, sem sair desta tela</span>`;
  const note = strip.querySelector(".pequeno");
  strip.querySelector("button").addEventListener("click", async () => {
    const origins = knownOrigins || [];
    if (!origins.length) {
      note.textContent = "Não sei o endereço do portal — abra a bancada por ele de novo.";
      return;
    }
    const portalWindow = window.open(
      `${origins[0]}/bancada.html#renovar`, "renovar-bancada", "width=460,height=360"
    );
    if (!portalWindow) {
      note.textContent = "O navegador bloqueou a janelinha — libere pop-ups para a bancada.";
      return;
    }
    // Waits longer: if the portal login expired too, the person logs in again over there.
    if (await waitForToken(portalWindow, origins, 120000)) {
      failedAt = 0;
      strip.remove();
      notify("Acesso renovado. Repita o que estava fazendo.");
    } else {
      note.textContent = "Não consegui renovar. Entre no portal e tente de novo.";
    }
  });
  document.body.prepend(strip);
}

async function api(route, options, afterRenewing) {
  const original = options;
  const token = await validToken();
  if (token) {
    options = { ...(options || {}) };
    options.headers = { ...(options.headers || {}), Authorization: `Bearer ${token}` };
  }
  const response = await fetch(route, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    // The 401 comes BEFORE `body.error`. The login gate answers `{"error": "not
    // authenticated"}`, and with the order reversed that raw text was what the person
    // saw — the sentence explaining what to do never appeared, and the key renewal, which
    // lives here, would never get its chance to run.
    if (response.status === 401) {
      // The key expired on the way, or the hour went by with the screen idle. Ask the
      // portal for a new one and repeat ONCE: the 401 leaves the gate before the request
      // does anything, so repeating is safe — in a POST too.
      if (!afterRenewing && token && (await renewToken())) {
        return api(route, original, true);
      }
      showRenewStrip();
      throw new Error(
        'Sua sessão expirou. Use "Renovar acesso", no alto da tela, ou abra a bancada pelo portal de novo.'
      );
    }
    if (body.error) throw new Error(body.error);
    // A 404 on an API route almost always means a server frozen in time: the screens come
    // from disk, the routes come from the memory of whoever started the process.
    if (response.status === 404 && route.startsWith("/api/")) {
      warnOutdated();
      throw new Error(
        `O servidor não conhece "${route}". Ele provavelmente está rodando uma versão ` +
          `anterior — reinicie a bancada (Ctrl+C no terminal e ./.venv/bin/workbench).`
      );
    }
    if (response.status === 403) {
      throw new Error(
        "Este login não é da equipe do escritório — fale com quem administra o portal."
      );
    }
    throw new Error(body.detail || `erro ${response.status}`);
  }
  return body;
}

/* A strip pinned to the top. Shows up when the code on disk changed after the server
 * started — the cause of a 404 on a new route, and of behaviour that "did not change"
 * after an update. */
function warnOutdated() {
  if (document.getElementById("desatualizado")) return;
  const strip = document.createElement("div");
  strip.id = "desatualizado";
  strip.className = "faixa-versao";
  strip.innerHTML = `
    <strong>A bancada foi atualizada em disco, mas o servidor continua rodando a versão anterior.</strong>
    Feche-o com <code>Ctrl+C</code> no terminal e rode <code>./.venv/bin/workbench</code> de novo.
    Seus dados não são afetados.`;
  document.body.prepend(strip);
}

async function checkVersion() {
  try {
    const v = await fetch("/api/version").then((r) => (r.ok ? r.json() : null));
    if (v && v.outdated) warnOutdated();
  } catch {
    /* an old server has no /api/version — the 404 in api() already covers it */
  }
}

// …  the `form()` helper, the "is the server up?" probe, the `file:` guard, screen
//    navigation, companies, periods, the balance proof and its chart

/* The panel that ASKS.

   On a scanned statement the reader gets it wrong in two ways, and they call for
   different questions:

   - the number did not come out: the file stops, and the list says where;
   - the number came out well-formed and wrong: nothing stops, and what finds it is the
     daily balance check — which points at the date, the pages and the similar entries.

   What the person types does NOT skip the check. That is the difference between asking
   and deducing: a deduced amount makes the file close by construction, and the 0.00 would
   stop being proof. An amount typed wrong does not close, and the statement stays
   refused. */
/* Cents of an amount written any which way — `1.462,35`, `R$ 528,34C`, `-745,00`. The
   number only: the sign does not come in here, because this serves to tell whether the
   person TYPED THE SAME NUMBER the system read, not to do accounting. */
function cents(string) {
  const number = String(string == null ? "" : string).replace(/[^\d,]/g, "");
  const parts = number.match(/^(\d+),(\d{2})$/);
  return parts ? Number(parts[1]) * 100 + Number(parts[2]) : null;
}

// …  the correction panel, the suspect list, the reconciliation queue, the category
//    combo box, transfer pairing, the batch bar and the settings screen

/* ═══════════════════════════════ start ═════════════════════════════════ */

checkVersion();
// …  `loadCompanies().catch((err) => alert(err.message));`
