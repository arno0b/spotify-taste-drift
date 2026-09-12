// Public by design: a PKCE client has no secret, and the client ID appears in
// every authorize URL regardless. Nothing here is confidential.
const CLIENT_ID = "90842cf751aa429398dd1f4c5c03369b";
const REDIRECT_URI = `${location.origin}${location.pathname}`;
const SCOPE = "user-top-read";
const STORE_KEY = "taste-drift-snapshots-v1";

const $ = (id) => document.getElementById(id);

// --- PKCE ------------------------------------------------------------------

function base64url(bytes) {
  return btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function randomVerifier() {
  return base64url(crypto.getRandomValues(new Uint8Array(64)));
}

async function challengeFor(verifier) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url(digest);
}

async function beginLogin() {
  const verifier = randomVerifier();
  sessionStorage.setItem("pkce_verifier", verifier);
  const params = new URLSearchParams({
    client_id: CLIENT_ID,
    response_type: "code",
    redirect_uri: REDIRECT_URI,
    scope: SCOPE,
    code_challenge_method: "S256",
    code_challenge: await challengeFor(verifier),
  });
  location.href = `https://accounts.spotify.com/authorize?${params}`;
}

async function exchangeCode(code) {
  const verifier = sessionStorage.getItem("pkce_verifier");
  if (!verifier) throw new Error("Login session expired. Try signing in again.");

  const response = await fetch("https://accounts.spotify.com/api/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: REDIRECT_URI,
      client_id: CLIENT_ID,
      code_verifier: verifier,
    }),
  });
  if (!response.ok) throw new Error(`Token exchange failed (${response.status}).`);
  sessionStorage.removeItem("pkce_verifier");
  // The access token is used for this visit and discarded. No refresh token is
  // requested or stored, which is why PKCE is fine here but not for the cron.
  return (await response.json()).access_token;
}

// --- Spotify ---------------------------------------------------------------

async function fetchTop(token, kind, timeRange) {
  const response = await fetch(
    `https://api.spotify.com/v1/me/top/${kind}?time_range=${timeRange}&limit=50`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
  if (!response.ok) throw new Error(`Spotify returned ${response.status} for ${kind}/${timeRange}.`);
  return (await response.json()).items;
}

// --- Analysis --------------------------------------------------------------

// Same definition as the cron's build_metrics.divergence: overlap over the
// smaller set, because Spotify often returns fewer than 50 for short_term.
function overlapOf(shortItems, longItems) {
  const shortIds = new Set(shortItems.map((item) => item.id));
  const longIds = new Set(longItems.map((item) => item.id));
  const shared = [...shortIds].filter((id) => longIds.has(id)).length;
  const smaller = Math.min(shortIds.size, longIds.size);
  return smaller ? shared / smaller : null;
}

// --- Local store -----------------------------------------------------------

function loadSnapshots() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE_KEY) || "[]");
    return Array.isArray(raw) ? raw : [];
  } catch {
    return []; // corrupt or blocked storage is not worth failing over
  }
}

function saveSnapshot(entry) {
  const kept = loadSnapshots().filter((snapshot) => snapshot.date !== entry.date);
  kept.push(entry);
  kept.sort((a, b) => a.date.localeCompare(b.date));
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(kept));
  } catch {
    // Private browsing or blocked storage. The visit still works; only the
    // accumulating history is lost, and the page already warns about that.
  }
  return kept;
}

// --- Render ----------------------------------------------------------------

function tile(label, value) {
  return `<div class="tile"><div class="value">${value}</div><div class="label">${label}</div></div>`;
}

function showError(message) {
  $("error").hidden = false;
  $("error").textContent = message;
}

function renderResult(artistOverlap, trackOverlap, snapshots) {
  $("gate").hidden = true;
  $("result").hidden = false;

  $("lede").textContent =
    artistOverlap === null
      ? "Spotify has not computed enough listening for your account yet."
      : `${Math.round(artistOverlap * 100)} percent of what you're playing right now ` +
        `was already in your long-term rotation.`;

  $("tiles").innerHTML = [
    tile("Artists", artistOverlap === null ? "—" : artistOverlap.toFixed(2)),
    tile("Tracks", trackOverlap === null ? "—" : trackOverlap.toFixed(2)),
    tile("Visits recorded", snapshots.length),
  ].join("");

  const caption = $("history-cap");
  if (snapshots.length < 2) {
    // One point is not a trend. Draw nothing rather than imply a line.
    $("history").innerHTML = `<div class="awaiting"></div>`;
    caption.textContent =
      "Come back another day and this fills in. One visit is not a trend, so nothing is drawn yet.";
    return;
  }

  caption.textContent = `${snapshots.length} visits recorded in this browser.`;
  $("history").replaceChildren(
    Plot.plot({
      height: 200,
      style: { background: "transparent" },
      y: { domain: [0, 1], label: "overlap" },
      x: { label: null, type: "utc" },
      marks: [
        Plot.ruleY([0], { stroke: "var(--line)" }),
        Plot.line(snapshots, {
          x: (d) => new Date(d.date), y: "artistOverlap",
          stroke: "var(--accent)", strokeWidth: 1.8,
        }),
        Plot.dot(snapshots, {
          x: (d) => new Date(d.date), y: "artistOverlap", r: 2.5, fill: "var(--accent)",
        }),
      ],
    })
  );
}

// --- Entry point -----------------------------------------------------------

async function main() {
  $("login").onclick = () => beginLogin().catch((error) => showError(error.message));
  $("forget").onclick = (event) => {
    event.preventDefault();
    localStorage.removeItem(STORE_KEY);
    location.href = REDIRECT_URI;
  };

  const params = new URLSearchParams(location.search);

  // Spotify redirects back with ?error= when the account is not on the app's
  // allowlist. That is the expected path for most visitors, so it gets a real
  // explanation rather than an error dump.
  if (params.get("error")) {
    showError(
      "Spotify would not authorise this account. While this app is in development " +
      "mode Spotify allows at most 25 people, each added by hand. Ask to have your " +
      "Spotify account email added, then try again."
    );
    history.replaceState({}, "", REDIRECT_URI);
    return;
  }

  const code = params.get("code");
  if (!code) return; // first visit — show the sign-in gate

  history.replaceState({}, "", REDIRECT_URI);
  try {
    const token = await exchangeCode(code);
    const [shortArtists, longArtists, shortTracks, longTracks] = await Promise.all([
      fetchTop(token, "artists", "short_term"),
      fetchTop(token, "artists", "long_term"),
      fetchTop(token, "tracks", "short_term"),
      fetchTop(token, "tracks", "long_term"),
    ]);

    const artistOverlap = overlapOf(shortArtists, longArtists);
    const trackOverlap = overlapOf(shortTracks, longTracks);
    const snapshots = saveSnapshot({
      date: new Date().toISOString().slice(0, 10),
      artistOverlap,
      trackOverlap,
    });

    renderResult(artistOverlap, trackOverlap, snapshots);
  } catch (error) {
    showError(error.message);
  }
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { overlapOf, base64url, saveSnapshot, loadSnapshots, renderResult };
} else {
  main();
}
