const $ = (id) => document.getElementById(id);
const TOP_N = 10; // rows shown by default in the rank table

function tile(label, value) {
  return `<div class="tile"><div class="value">${value}</div><div class="label">${label}</div></div>`;
}

// An unready figure keeps its slot as an empty frame. We never draw a shape
// that is not real data, so there is deliberately nothing inside it.
function awaiting(node, note) {
  node.innerHTML = `<div class="awaiting"></div><p class="awaiting-note">${note}</p>`;
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function arrivalDate(units, unitDays) {
  const when = new Date(Date.now() + units * unitDays * 86400000);
  return when.toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" });
}

function longDate(iso) {
  return new Date(iso + "T00:00:00Z").toLocaleDateString(undefined, {
    day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
  });
}

function pct(x) {
  return `${Math.round(x * 100)}%`;
}

function writeLede(data) {
  const artists = data.headline.divergence_artists;
  const tracks = data.headline.divergence_tracks;

  if (artists === null || artists === undefined) {
    $("lede").textContent = "Not enough listening recorded yet to say anything true.";
    return;
  }

  // The interesting claim is the gap between the two, not either number. A
  // large gap means the artists persist while the songs turn over — the
  // listener is working through catalogues rather than finding new acts.
  if (tracks !== null && tracks !== undefined && artists - tracks >= 0.15) {
    $("lede").textContent =
      `I keep my artists and change the songs: ${pct(artists)} of the artists ` +
      `I'm playing now are long-term regulars, but only ${pct(tracks)} of the tracks are.`;
    return;
  }
  if (tracks !== null && tracks !== undefined && tracks - artists >= 0.15) {
    $("lede").textContent =
      `I keep the songs and change the artists: ${pct(tracks)} of the tracks ` +
      `I'm playing now are long-term regulars, but only ${pct(artists)} of the artists are.`;
    return;
  }
  $("lede").textContent =
    `${pct(artists)} of what I'm playing right now was already in my long-term rotation.`;
}

function render(data) {
  writeLede(data);

  const dates = data.snapshot_dates;
  // "16 daily snapshots" is a fact about the pipeline, not about the reader.
  $("byline").textContent = dates.length
    ? `Tracking daily since ${longDate(dates[0])}.`
    : "No snapshots yet.";

  const skipped = data.data_quality.skipped_files;
  if (skipped.length) {
    const quality = $("quality");
    quality.hidden = false;
    quality.textContent =
      `${plural(skipped.length, "raw file")} could not be read and ${skipped.length === 1 ? "was" : "were"} excluded: ` +
      skipped.map((entry) => entry.path).join(", ");
  }

  drawVerdict(data.headline);

  drawHorizon("track", "tracks", data.horizon, data.images);
  drawHorizon("artist", "horizon", data.horizon, data.images);
  drawGenreShift(data.genre_shift, data.genre_coverage);

  drawRuns(data.album_runs);
  drawEra(data.release_profile);

  drawMovers(data);
  for (const control of ["kind", "range", "count"]) {
    $(control).onchange = () => drawMovers(data);
  }

  drawUnlocks(data.survival, data.half_life);
}

// Hand-rolled rather than 50 Plot instances: a sparkline is a polyline, and
// Plot's per-chart overhead would dominate. Gaps break the line deliberately —
// an absent day means "outside the top 50", not "interpolate through it".
function sparkline(byDate, dates, { width = 76, height = 18, max = 50 } = {}) {
  const x = (i) => (dates.length < 2 ? width / 2 : 1 + (i / (dates.length - 1)) * (width - 2));
  const y = (rank) => 2 + ((rank - 1) / (max - 1)) * (height - 4);

  const segments = [];
  let run = [];
  dates.forEach((date, i) => {
    const rank = byDate[date];
    if (rank === undefined) {
      if (run.length) segments.push(run);
      run = [];
      return;
    }
    run.push([x(i), y(rank)]);
  });
  if (run.length) segments.push(run);

  const shapes = segments
    .map((points) =>
      points.length === 1
        ? `<circle cx="${points[0][0].toFixed(1)}" cy="${points[0][1].toFixed(1)}" r="1.5" fill="currentColor"/>`
        : `<polyline points="${points.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ")}"` +
          ` fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>`
    )
    .join("");

  return `<svg class="spark" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" aria-hidden="true">${shapes}</svg>`;
}

function deltaCell(delta) {
  if (delta === null) return `<span class="new">new</span>`;
  if (delta === 0) return `<span class="flat">&mdash;</span>`;
  const up = delta > 0;
  // delta is (older rank - current rank), so positive means climbed.
  return `<span class="${up ? "up" : "down"}">${up ? "&uarr;" : "&darr;"}${Math.abs(delta)}</span>`;
}

// Replaces the "Divergence 0.54" tile. A coefficient is not something anyone
// opens Spotify wanting to read; a position on a named spectrum is.
function drawVerdict(head) {
  const node = $("headline");
  const phase = head.phase;
  if (!phase) {
    node.innerHTML = `<p class="pending">Not enough listening recorded yet.</p>`;
    return;
  }
  node.innerHTML =
    `<p class="verdict">${phase.sentence}</p>` +
    `<div class="gauge" role="img" aria-label="${phase.sentence}">` +
    `<i style="left:${phase.position}%"></i></div>` +
    `<div class="gauge-ends"><span>Comfort zone</span><span>Exploring</span></div>` +
    `<p class="gauge-sub">` +
    `${head.entries_7d} arrived and ${head.exits_7d} left in the last week.</p>`;
}

// A listener working through a record looks nothing like one picking singles,
// and it is the mechanism behind the lede: the artists persist because the
// albums do.
function drawRuns(runs) {
  const node = $("runs");
  const caption = $("runs-cap");
  const list = (runs || []).filter((r) => r.name);
  if (!list.length) {
    caption.textContent = "";
    return awaiting(node, "No album is contributing more than one of your recent songs.");
  }

  const top = list[0];
  const art = top.image
    ? `<img src="${top.image}" alt="" loading="lazy" width="96" height="96">`
    : `<span class="no-art big" aria-hidden="true"></span>`;
  const titles = top.tracks.map((t) => t.name).join(" · ");
  const others = list.slice(1, 4);

  node.innerHTML =
    `<div class="run">` +
    `<a class="run-art" href="https://open.spotify.com/album/${top.album_id}" target="_blank" rel="noopener">${art}</a>` +
    `<div class="run-body">` +
    `<p class="run-lede">${plural(top.count, "song")} in your last four weeks ` +
    `come from one record &mdash; <strong>${top.name}</strong>${top.artist ? ` by ${top.artist}` : ""}.</p>` +
    `<p class="run-tracks">${titles}</p>` +
    `</div></div>` +
    (others.length
      ? `<p class="run-others">Also more than one song deep: ` +
        others.map((r) => `${r.name} (${r.count})`).join(", ") + `.</p>`
      : "");

  caption.textContent =
    `Albums giving you more than one of your fifty most-played songs right now. ` +
    `Working through a record rather than picking singles is why your artists ` +
    `stay put while the songs turn over.`;
}

// Drift is usually assumed to run older. Worth measuring rather than assuming.
function drawEra(profile) {
  const node = $("era");
  const caption = $("era-cap");
  const rows = profile || [];
  const now = rows.find((r) => r.time_range === "short_term");
  const year = rows.find((r) => r.time_range === "long_term");
  if (!now) {
    caption.textContent = "";
    return awaiting(node, "No release dates captured yet.");
  }

  const max = Math.max(...now.decades.map((d) => d.count), 1);
  const bars = now.decades
    .map(
      (d) =>
        `<div class="era-bar" title="${plural(d.count, "song")} from the ${d.decade}s">` +
        `<span style="height:${Math.round((d.count / max) * 100)}%"></span>` +
        `<em>${String(d.decade).slice(2)}s</em></div>`
    )
    .join("");

  const direction =
    year && now.recent_share - year.recent_share > 0.05
      ? `That is up from ${pct(year.recent_share)} across your whole year, so you are ` +
        `getting more current, not less.`
      : year && year.recent_share - now.recent_share > 0.05
      ? `Across your whole year it is ${pct(year.recent_share)}, so you have been ` +
        `reaching further back lately.`
      : "";

  node.innerHTML =
    `<p class="era-lede">${pct(now.recent_share)} of what you are playing came out ` +
    `in ${now.recent_since} or later. ${direction}</p>` +
    `<div class="era-chart">${bars}</div>`;

  caption.textContent =
    `Release decade of your fifty most-played songs right now, oldest to newest — ` +
    `${now.oldest} to ${now.newest}. Median year ${now.median_year}. ` +
    `Based on the ${now.measured} with a release date.`;
}

function drawMovers(data) {
  const node = $("timeline");
  const caption = $("timeline-cap");
  const rows = (data.rank_timeline[$("kind").value] || {})[$("range").value] || [];
  if (!rows.length) {
    caption.textContent = "";
    return awaiting(node, "No data for this selection yet.");
  }

  const dates = [...new Set(rows.map((r) => r.date))].sort();
  const latest = dates[dates.length - 1];

  const names = data.names || {};
  const series = new Map();
  for (const r of rows) {
    if (!series.has(r.id)) series.set(r.id, { name: names[r.id] || r.id, byDate: {} });
    series.get(r.id).byDate[r.date] = r.rank;
  }

  // Compare against the snapshot nearest a week before the latest, so the
  // column means the same thing whether or not every day was captured.
  const target = new Date(new Date(latest).getTime() - 7 * 86400000).toISOString().slice(0, 10);
  const baseline = dates.filter((d) => d <= target).pop() || dates[0];
  const hasBaseline = baseline !== latest;

  const entries = [...series.entries()]
    .filter(([, s]) => s.byDate[latest] !== undefined)
    .map(([id, s]) => {
      const now = s.byDate[latest];
      const then = s.byDate[baseline];
      return {
        id, name: s.name, now, byDate: s.byDate,
        delta: !hasBaseline || then === undefined ? null : then - now,
      };
    })
    .sort((a, b) => a.now - b.now);

  // Fifty rows is a spreadsheet, not a first impression. Ten is enough to see
  // who is on top; the control lets you open it up to twenty.
  const limit = Number($("count").value) || 10;
  const shown = entries.slice(0, limit);

  const header = hasBaseline
    ? `<th class="num">vs ${baseline.slice(5)}</th><th>Trend</th>`
    : `<th>Trend</th>`;

  node.innerHTML =
    `<table class="movers"><thead><tr><th class="num">#</th><th>Name</th>${header}</tr></thead><tbody>` +
    shown
      .map(
        (e) =>
          `<tr><td class="num pos">${e.now}</td><td class="name">${e.name}</td>` +
          (hasBaseline ? `<td class="num">${deltaCell(e.delta)}</td>` : "") +
          `<td class="trend">${sparkline(e.byDate, dates)}</td></tr>`
      )
      .join("") +
    `</tbody></table>`;

  const moved = entries.filter((e) => e.delta !== null && e.delta !== 0);
  const biggest = moved.slice().sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))[0];

  // The counts and the biggest mover describe the whole ranking, not just the
  // rows on screen, and the wording says so — otherwise a mover count sitting
  // under ten rows reads as a contradiction. It deliberately avoids phrasing
  // like "top 10 of 50", which implied the Show control offered 50.
  if (!hasBaseline) {
    caption.textContent =
      `Your top ${shown.length} as of ${latest}. Movement appears once there are ` +
      `snapshots a week apart.`;
  } else {
    caption.textContent =
      `Your top ${shown.length} as of ${latest}, with the change since ${baseline}. ` +
      `The trend column is each entry's rank across all ${dates.length} snapshots; ` +
      `a break in the line means it dropped out of Spotify's ranking entirely. ` +
      (biggest
        ? `The biggest mover anywhere in your ranking is ${biggest.name}, ` +
          `${biggest.delta > 0 ? "up" : "down"} ${Math.abs(biggest.delta)}; ` +
          `${moved.length} entries moved at all.`
        : `Nothing moved.`);
  }
}

const FACES = 6;

function face(entry, images, faded, kind) {
  const src = images[entry.spotify_id];
  // Album covers are square; artist portraits read better as circles.
  const shape = kind === "track" ? " square" : "";
  const art = src
    ? `<img src="${src}" alt="" loading="lazy" width="58" height="58">`
    : `<span class="no-art" aria-hidden="true"></span>`;
  const link = `https://open.spotify.com/${kind}/${entry.spotify_id}`;
  const sub = entry.subtitle ? `<span class="sub">${entry.subtitle}</span>` : "";
  return (
    `<figure class="face${shape}${faded ? " faded" : ""}">` +
    `<a href="${link}" target="_blank" rel="noopener">${art}</a>` +
    `<figcaption>${entry.name}${sub}</figcaption></figure>`
  );
}

const HORIZON_COPY = {
  artist: {
    inHead: "Taking over",
    outHead: "Slipping away",
    caption:
      "Artists you are playing heavily in the last four weeks that were nowhere " +
      "near your top fifty over the year. The faded ones are the reverse — " +
      "regulars from the year that you have stopped playing.",
  },
  track: {
    inHead: "On heavy rotation",
    outHead: "Worn out",
    caption:
      "Songs big in your last four weeks that were not among your most played " +
      "over the year. The faded ones you played all year and have now put down.",
  },
};

function drawHorizon(kind, nodeId, horizon, images) {
  const node = $(nodeId);
  const caption = $(nodeId + "-cap");
  const block = (horizon || []).filter((h) => h.kind === kind).pop();
  const copy = HORIZON_COPY[kind];
  if (!block) {
    caption.textContent = "";
    return awaiting(node, "Needs a snapshot with both the four-week and yearly lists.");
  }

  const pics = images || {};
  const rise = block.ascending.slice(0, FACES);
  const fall = block.fading.slice(0, FACES);

  // Artwork rather than ranks: Spotify's own visual language. A listener
  // recognises a cover or a face far faster than a rank against an absence.
  node.innerHTML =
    `<p class="rowhead up">${copy.inHead}</p>` +
    `<div class="faces">${rise.map((e) => face(e, pics, false, kind)).join("") ||
      `<p class="pending">Nothing climbing.</p>`}</div>` +
    `<p class="rowhead down">${copy.outHead}</p>` +
    `<div class="faces">${fall.map((e) => face(e, pics, true, kind)).join("") ||
      `<p class="pending">Nothing fading.</p>`}</div>`;

  const c = block.counts;
  caption.textContent =
    `${copy.caption} ${c.short_only} have arrived, ${c.long_only} have gone, ` +
    `and ${c.both} have stayed throughout.`;
}

const GENRE_TAGS = 6;

function drawGenreShift(shift, coverage) {
  const node = $("genres");
  const caption = $("genres-cap");
  const rows = shift || [];
  if (!rows.length) {
    caption.textContent = "";
    return awaiting(node, "No genres resolved yet — run tools/enrich_artists.py.");
  }

  // Words, not percentages. This is how people describe their own taste:
  // "I've been on an r&b thing lately, I used to be all ambient."
  const gained = rows.filter((r) => r.delta > 0).slice(0, GENRE_TAGS);
  const lost = rows.filter((r) => r.delta < 0).slice(0, GENRE_TAGS);
  const pill = (r, cls) =>
    `<span class="pill ${cls}" title="${pct(r.short_share || 0)} now, ${pct(r.long_share || 0)} over the year">${r.genre}</span>`;

  node.innerHTML =
    `<p class="rowhead up">Your sound lately</p>` +
    `<div class="pills">${gained.map((r) => pill(r, "up")).join("") ||
      `<p class="pending">Nothing gaining.</p>`}</div>` +
    `<p class="rowhead down">What you have moved on from</p>` +
    `<div class="pills">${lost.map((r) => pill(r, "down")).join("") ||
      `<p class="pending">Nothing fading.</p>`}</div>`;

  const cov = (coverage || []).filter((c) => c.time_range === "short_term").pop();
  // The gap is not random: the unidentified artists are overwhelmingly regional
  // ones, so this figure skews toward Western music. Naming a few of them is the
  // only honest way to present it.
  let gap = "";
  if (cov) {
    gap = ` Genres could be identified for ${cov.resolved} of your ${cov.total} artists.`;
    const missing = cov.unidentified || [];
    if (missing.length) {
      gap +=
        ` The rest — including ${missing.slice(0, 3).join(", ")} — are not catalogued ` +
        `in MusicBrainz, which under-represents regional artists, so this figure ` +
        `leans toward the Western half of your listening.`;
    }
  }
  caption.textContent =
    `Genres that have grown in your last four weeks compared with your last year, and ` +
    `the ones that have receded. Hover a tag for the actual shares.` + gap;
}

// Two empty framed figures promising November was a lot of IOU for a page with
// four working ones. One sentence carries the same promise.
function drawUnlocks(survival, halfLife) {
  const waiting = [];
  if (survival && !survival.available) {
    const weeks = survival.weeks_needed - survival.weeks_have;
    waiting.push(`how long new artists last in your rotation (${plural(weeks, "week")} away)`);
  }
  if (halfLife && !halfLife.available) {
    const spells = halfLife.spells_needed - halfLife.spells_have;
    waiting.push(`how quickly your rotation turns over (needs ${spells} more artists to come and go)`);
  }
  $("unlocks").textContent = waiting.length
    ? `Still gathering history for two longer-term measures: ${waiting.join(", and ")}.`
    : "";
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { render, drawMovers, writeLede, sparkline, longDate }; // for the node smoke test
} else {
  fetch("data.json")
    .then((response) => {
      if (!response.ok) throw new Error(`data.json returned ${response.status}`);
      return response.json();
    })
    .then(render)
    .catch((error) => {
      $("lede").textContent = "Could not load the data.";
      $("byline").textContent = error.message;
    });
}
