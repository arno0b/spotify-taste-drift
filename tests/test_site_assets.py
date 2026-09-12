"""Guards against the dependency bug that silently blanked the dashboard.

Observable Plot's UMD build does NOT bundle d3 — it expects a global `d3`.
A page that loads Plot alone gets a Plot object whose every call throws, and
because rendering is sequential, the first chart that throws kills every figure
after it. The page looked half-built with no error visible to the reader.

These are cheap file assertions, not a substitute for rendering the page. The
real lesson was that the original smoke test stubbed Plot with a permissive
proxy: a stub that accepts any call proves the code path runs, never that the
library accepts what it was given.
"""

import re
from pathlib import Path

import pytest

# Both pages are named index.html, so the script cannot be derived from the
# page name — it has to be stated.
PAGES = [Path("site/index.html"), Path("site/try/index.html")]
SCRIPTS = {
    Path("site/index.html"): Path("site/app.js"),
    Path("site/try/index.html"): Path("site/try/try.js"),
}


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p))
def test_d3_is_loaded_before_plot(page):
    html = page.read_text(encoding="utf-8")
    d3_at = html.find("d3")
    plot_at = html.find("@observablehq/plot")

    assert d3_at != -1, f"{page} loads Plot without d3; every Plot call will throw"
    assert plot_at != -1, f"{page} does not load Plot at all"
    assert d3_at < plot_at, f"{page} loads d3 after Plot; Plot reads the global at load time"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p))
def test_cdn_versions_are_pinned_exactly(page):
    html = page.read_text(encoding="utf-8")
    for src in re.findall(r'<script src="(https://[^"]+)"', html):
        assert "@latest" not in src, f"{page} uses a floating version: {src}"
        assert re.search(r"@\d+\.\d+\.\d+", src), f"{page} version is not fully pinned: {src}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p))
def test_every_element_the_script_looks_up_exists_in_the_markup(page):
    """A missing id makes $(...) return null and the next property access throw."""
    script = SCRIPTS[page].read_text(encoding="utf-8")
    html = page.read_text(encoding="utf-8")

    referenced = set(re.findall(r'\$\("([a-z-]+)"\)', script))
    present = set(re.findall(r'id="([a-z-]+)"', html))

    missing = sorted(referenced - present)
    assert not missing, f"{page} is missing ids used by its script: {missing}"
