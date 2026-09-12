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

function writeLede(data) {
  const overlap = data.headline.divergence_artists;
  if (overlap === null || overlap === undefined) {
    $("lede").textContent = "Not enough listening recorded yet to say anything true.";
    return;
  }
  $("lede").textContent =
    `${Math.round(overlap * 100)} percent of what I'm playing right now ` +
    `was already in my long-term rotation.`;
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

  drawTimeline(data);
  $("kind").onchange = () => drawTimeline(data);
  $("range").onchange = () => drawTimeline(data);

  drawDivergence(data.divergence.filter((d) => d.kind === "artist"));
  drawSurvival(data.survival);
  drawHalfLife(data.half_life);
  drawEvents(data.events);
}

function drawTimeline(data) {
  const node = $("timeline");
  const caption = $("timeline-cap");
  const rows = (data.rank_timeline[$("kind").value] || {})[$("range").value] || [];
  if (!rows.length) {
    caption.textContent = "";
    return awaiting(node, "No data for this selection yet.");
  }

  const dates = [...new Set(rows.map((d) => d.date))].sort();
  const lastDate = dates[dates.length - 1];
  const current = rows.filter((d) => d.date === lastDate).sort((a, b) => a.rank - b.rank);

  // One snapshot means no movement to draw. Rather than an empty frame, show
  // the ranking itself — that is real data, and it is all today can honestly say.
  if (dates.length < 2) {
    node.innerHTML =
      `<ol class="ranking">` +
      current
        .slice(0, TOP_N)
        .map((d) => `<li><span class="pos">${d.rank}</span><span>${d.name}</span></li>`)
        .join("") +
      `</ol>`;
    caption.textContent =
      `Only one snapshot so far, so there is no movement to plot — this is today's top ` +
      `${Math.min(TOP_N, current.length)}. Lines appear once a second day is captured.`;
    return;
  }

  const featured = new Set(current.filter((d) => d.rank <= TOP_N).map((d) => d.id));
  const isFeatured = (d) => featured.has(d.id);

  caption.textContent =
    "Every line is one entry. The current top ten are drawn in full and named; the rest stay faint.";

  node.replaceChildren(
    Plot.plot({
      height: 480,
      marginLeft: 34,
      marginRight: 132,
      style: { background: "transparent" },
      y: { reverse: true, label: "rank", domain: [1, 50], ticks: [1, 10, 25, 50] },
      x: { label: null, type: "utc" },
      marks: [
        Plot.line(rows.filter((d) => !isFeatured(d)), {
          x: (d) => new Date(d.date), y: "rank", z: "id",
          stroke: "var(--faint)", strokeWidth: 1, strokeOpacity: 0.5,
        }),
        Plot.line(rows.filter(isFeatured), {
          x: (d) => new Date(d.date), y: "rank", z: "id",
          stroke: "var(--fg)", strokeWidth: 1.8,
        }),
        Plot.text(current.filter((d) => d.rank <= TOP_N), {
          x: (d) => new Date(d.date), y: "rank", text: "name",
          dx: 7, textAnchor: "start", fontSize: 11, fill: "var(--fg)",
        }),
        Plot.tip(rows, Plot.pointer({
          x: (d) => new Date(d.date), y: "rank",
          title: (d) => `${d.name} · #${d.rank}`,
        })),
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
  module.exports = { render, drawTimeline, writeLede }; // for the node smoke test
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
