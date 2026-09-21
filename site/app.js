const $ = (id) => document.getElementById(id);
const TOP_N = 10; // lines that get weight and a name label in Figure 1

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
  $("byline").textContent = dates.length
    ? `${plural(dates.length, "daily snapshot")} · ${dates[0]} to ${dates[dates.length - 1]}`
    : "No snapshots yet.";

  const skipped = data.data_quality.skipped_files;
  if (skipped.length) {
    const quality = $("quality");
    quality.hidden = false;
    quality.textContent =
      `${plural(skipped.length, "raw file")} could not be read and ${skipped.length === 1 ? "was" : "were"} excluded: ` +
      skipped.map((entry) => entry.path).join(", ");
  }

  const head = data.headline;
  $("headline").innerHTML = [
    tile("Divergence", head.divergence_artists === null ? "—" : head.divergence_artists.toFixed(2)),
    tile("Entries, 7d", head.entries_7d),
    tile("Exits, 7d", head.exits_7d),
  ].join("");

  drawMovers(data);
  $("kind").onchange = () => drawMovers(data);
  $("range").onchange = () => drawMovers(data);

  drawHorizon(data.horizon);
  drawGenreShift(data.genre_shift, data.genre_coverage);
  drawDivergence(data.divergence.filter((d) => d.kind === "artist"));
  drawReach(data.reach);
  drawSurvival(data.survival);
  drawHalfLife(data.half_life);
  drawEvents(data.events);
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

  const header = hasBaseline
    ? `<th class="num">vs ${baseline.slice(5)}</th><th>Trend</th>`
    : `<th>Trend</th>`;

  node.innerHTML =
    `<table class="movers"><thead><tr><th class="num">#</th><th>Name</th>${header}</tr></thead><tbody>` +
    entries
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

  if (!hasBaseline) {
    caption.textContent =
      `Ranking as of ${latest}. Movement appears once there are snapshots a week apart.`;
  } else {
    caption.textContent =
      `Rank on ${latest}, and the change since ${baseline}. ` +
      `The trend column is each entry's rank across all ${dates.length} snapshots; ` +
      `a break in the line means it dropped out of the top fifty. ` +
      (biggest
        ? `Biggest mover: ${biggest.name}, ${biggest.delta > 0 ? "up" : "down"} ${Math.abs(biggest.delta)}. ` +
          `${moved.length} of ${entries.length} moved at all.`
        : `Nothing moved.`);
  }
}

function drawHorizon(horizon) {
  const node = $("horizon");
  const caption = $("horizon-cap");
  const artist = (horizon || []).filter((h) => h.kind === "artist").pop();
  if (!artist) {
    caption.textContent = "";
    return awaiting(node, "Needs a snapshot with both the four-week and yearly lists.");
  }

  const rise = artist.ascending.slice(0, 8);
  const fall = artist.fading.slice(0, 8);

  const riseRows = rise
    .map(
      (e) =>
        `<tr><td class="num pos">${e.short}</td><td class="name">${e.name}</td>` +
        `<td class="num">${e.long === null ? `<span class="new">new</span>` : `<span class="faint">#${e.long}</span>`}</td></tr>`
    )
    .join("");
  const fallRows = fall
    .map(
      (e) =>
        `<tr><td class="num pos">#${e.long}</td><td class="name">${e.name}</td>` +
        `<td class="num"><span class="down">gone</span></td></tr>`
    )
    .join("");

  node.innerHTML =
    `<div class="two-up">` +
    `<div><p class="colhead up">Ascending</p><table class="movers"><thead><tr>` +
    `<th class="num">Now</th><th>Artist</th><th class="num">Year</th></tr></thead>` +
    `<tbody>${riseRows || `<tr><td colspan="3" class="faint">Nothing climbing.</td></tr>`}</tbody></table></div>` +
    `<div><p class="colhead down">Fading</p><table class="movers"><thead><tr>` +
    `<th class="num">Year</th><th>Artist</th><th class="num">Now</th></tr></thead>` +
    `<tbody>${fallRows || `<tr><td colspan="3" class="faint">Nothing fading.</td></tr>`}</tbody></table></div>` +
    `</div>`;

  const c = artist.counts;
  caption.textContent =
    `Your last four weeks against your last year, from the ${artist.date} snapshot. ` +
    `"Ascending" ranks high now but low or absent over the year; "fading" was a yearly ` +
    `regular that has dropped out of the last four weeks entirely. ` +
    `${c.short_only} artists are new to rotation, ${c.long_only} have dropped away, ` +
    `and ${c.both} are steady.`;
}

const GENRE_SHIFT_ROWS = 10;

function drawGenreShift(shift, coverage) {
  const node = $("genres");
  const caption = $("genres-cap");
  const rows = shift || [];
  if (!rows.length) {
    caption.textContent = "";
    return awaiting(node, "No genres resolved yet — run tools/enrich_artists.py.");
  }

  // Already sorted by |delta| server-side; take the strongest movers either way.
  const shown = rows.slice(0, GENRE_SHIFT_ROWS);
  const share = (v) => (v === null || v === undefined ? `<span class="faint">&mdash;</span>` : pct(v));
  const delta = (d) => {
    const points = d * 100;
    if (Math.abs(points) < 0.05) return `<span class="flat">&mdash;</span>`;
    const up = points > 0;
    return `<span class="${up ? "up" : "down"}">${up ? "&uarr;" : "&darr;"}${Math.abs(points).toFixed(1)}</span>`;
  };

  node.innerHTML =
    `<table class="movers"><thead><tr><th>Genre</th><th class="num">Last 4 weeks</th>` +
    `<th class="num">Over the year</th><th class="num">Shift</th></tr></thead><tbody>` +
    shown
      .map(
        (r) =>
          `<tr><td class="name">${r.genre}</td><td class="num">${share(r.short_share)}</td>` +
          `<td class="num">${share(r.long_share)}</td><td class="num">${delta(r.delta)}</td></tr>`
      )
      .join("") +
    `</tbody></table>`;

  const cov = (coverage || []).filter((c) => c.time_range === "short_term").pop();
  caption.textContent =
    `Share of listening by genre in the last four weeks against the last year, weighted so ` +
    `a rank-1 artist counts for more than a rank-50 one. A dash means the genre is absent ` +
    `from that window entirely, which is not the same as holding a zero share.` +
    (cov ? ` Based on the ${cov.resolved} of ${cov.total} artists whose genres could be identified.` : "");
}

function drawReach(rows) {
  const node = $("reach");
  const caption = $("reach-cap");
  const shortTerm = (rows || []).filter((r) => r.time_range === "short_term");
  if (!shortTerm.length) {
    caption.textContent = "";
    return awaiting(node, "No reach data yet — run tools/enrich_artists.py.");
  }

  const latest = shortTerm[shortTerm.length - 1];
  caption.textContent =
    `Median Deezer fan count of the artists in the last four weeks — a stand-in ` +
    `for Spotify's withdrawn popularity score. Median, not mean, because fan ` +
    `counts span five orders of magnitude. Based on ${latest.artists_measured} artists.`;

  node.replaceChildren(
    Plot.plot({
      height: 200,
      style: { background: "transparent" },
      // Log scale: fan counts run from hundreds to tens of millions.
      y: { type: "log", label: "median fans", grid: true },
      x: { label: null, type: "utc" },
      marks: [
        Plot.line(shortTerm, {
          x: (d) => new Date(d.date), y: "median_fans",
          stroke: "var(--accent)", strokeWidth: 1.8,
        }),
        Plot.dot(shortTerm, {
          x: (d) => new Date(d.date), y: "median_fans", r: 2.5, fill: "var(--accent)",
        }),
      ],
    })
  );
}

function drawDivergence(rows) {
  const node = $("divergence");
  if (!rows.length) return awaiting(node, "Needs at least one snapshot.");

  node.replaceChildren(
    Plot.plot({
      height: 200,
      style: { background: "transparent" },
      y: { domain: [0, 1], label: "overlap" },
      x: { label: null, type: "utc" },
      marks: [
        Plot.ruleY([0], { stroke: "var(--line)" }),
        // The dot matters: with a single snapshot a line mark renders nothing.
        Plot.line(rows, { x: (d) => new Date(d.date), y: "overlap", stroke: "var(--accent)", strokeWidth: 1.8 }),
        Plot.dot(rows, { x: (d) => new Date(d.date), y: "overlap", r: 2.5, fill: "var(--accent)" }),
      ],
    })
  );
}

function drawSurvival(survival) {
  const node = $("survival");
  if (!survival.available) {
    const weeks = survival.weeks_needed - survival.weeks_have;
    return awaiting(
      node,
      `Needs ${plural(weeks, "more week")} of history. First appears ${arrivalDate(weeks, 7)}.`
    );
  }
  node.replaceChildren(
    Plot.plot({
      height: 220,
      style: { background: "transparent" },
      y: { domain: [0, 1], label: "still in top 50", percent: true },
      x: { label: "weeks since first appearance" },
      marks: [Plot.line(survival.curve, { x: "week", y: "fraction", stroke: "var(--accent)", strokeWidth: 1.8 })],
    })
  );
}

function drawHalfLife(halfLife) {
  const node = $("halflife");
  if (!halfLife.available) {
    const spells = halfLife.spells_needed - halfLife.spells_have;
    return awaiting(
      node,
      `Needs ${plural(spells, "more completed spell")} before this can be measured. ` +
      `A spell is an artist entering the top fifty and later leaving it.`
    );
  }
  node.innerHTML = `<p class="lede" style="font-size:1.3rem">${plural(halfLife.median_days, "day")}</p>`;
  $("halflife-cap").textContent =
    `How long a typical artist survives in the top fifty, across ` +
    `${plural(halfLife.spells_have, "completed spell")}.`;
}

function drawEvents(events) {
  const recent = events
    .filter((e) => e.kind === "artist" && e.time_range === "short_term")
    .reverse()
    .slice(0, 50);
  if (!recent.length) {
    return awaiting($("events"), "No changes recorded yet — that needs a second snapshot.");
  }
  $("events").innerHTML = recent
    .map((event) => {
      const cls = event.event === "exited" ? "exited" : "entered";
      const verb = { entered: "entered", re_entered: "returned to", exited: "left" }[event.event];
      return `<li><time>${event.snapshot_date}</time><span class="${cls}">${verb}</span><span>${event.name}</span></li>`;
    })
    .join("");
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { render, drawMovers, writeLede, sparkline }; // for the node smoke test
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
