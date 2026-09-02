"""Read the data-age label out of a real browser, at phone width.

Report items [07] and [09] added a label the page had never carried, and a
label is only fixed when a browser actually shows it - the mobile header it
sits in is hidden by reference-design.css and brought back by smart-filter.css
only while an event list is on screen, which is exactly the kind of thing that
looks right in a stylesheet and is invisible on the page.

Usage: python scripts/browser-data-freshness-check.py [base-url]
"""
import json
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:4173"

READ = """() => {
  const node = document.getElementById('dataFreshness');
  const style = node ? getComputedStyle(node) : null;
  const list = document.getElementById('sidebarList');
  const streams = document.querySelector('#sidebarList .event-card-streams');
  return {
    view: (window.state && window.state.view) || '(unknown)',
    exists: Boolean(node),
    hidden: node ? node.hidden : null,
    text: node ? node.textContent : '',
    font_size: style ? style.fontSize : '',
    displayed: style ? (style.display !== 'none' && style.visibility !== 'hidden') : null,
    list_columns: list ? getComputedStyle(list).gridTemplateColumns : '',
    cards: document.querySelectorAll('#sidebarList > *').length,
    streams_display: streams ? getComputedStyle(streams).display : '(none present)',
    readiness: (() => {
      // Upcoming states readiness through its own status pill, not through the
      // stream-count chip the Today-side card carries.
      const pill = document.querySelector('#sidebarList .event-status-pill');
      if (!pill) return '(no status pill)';
      const style = getComputedStyle(pill);
      return {
        text: pill.textContent.trim(),
        font_size: style.fontSize,
        displayed: style.display !== 'none' && style.visibility !== 'hidden',
        min_height: style.minHeight,
      };
    })(),
    smallest_text: (() => {
      // Report item [11]: league at 8px and state badges down to 7.5px.
      let smallest = null;
      for (const node of document.querySelectorAll('#sidebarList *')) {
        if (!node.textContent.trim() || node.children.length) continue;
        const style = getComputedStyle(node);
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        const size = parseFloat(style.fontSize);
        if (!Number.isFinite(size)) continue;
        if (!smallest || size < smallest.size) {
          smallest = { size, text: node.textContent.trim().slice(0, 24),
                       cls: node.className || node.tagName };
        }
      }
      return smallest || '(no text)';
    })(),
    touch_targets: (() => {
      // Report item [12]: channel buttons at 27-42px.
      const rows = [];
      for (const node of document.querySelectorAll(
          '#sidebarList .event-card-action, #sidebarList .event-channel-chip,'
          + ' #sidebarList .tm-channel-solo, #sidebarList .card-fav-btn,'
          + ' #sidebarList .card-remind-btn')) {
        const box = node.getBoundingClientRect();
        if (box.width && box.height) {
          rows.push({ cls: String(node.className).split(' ')[0],
                      w: Math.round(box.width), h: Math.round(box.height) });
        }
      }
      return rows.slice(0, 6);
    })(),
    play_cue: (() => {
      // Report item [13]. The Today card's press affordance is the channel
      // button itself - the owner's reference design is a poster with "channel
      // buttons and nothing else" - so that is what has to be legible.
      const node = document.querySelector(
        '#sidebarList .tm-channel-solo, #sidebarList .event-channel-chip,'
        + ' #sidebarList .event-card-action');
      if (!node) return '(no action)';
      const box = node.getBoundingClientRect();
      return {
        text: node.textContent.trim().slice(0, 40),
        cls: String(node.className).split(' ').slice(0, 2).join(' '),
        w: Math.round(box.width), h: Math.round(box.height),
        has_play_glyph: Boolean(node.querySelector('.fa-play, .fa-circle-play')),
      };
    })(),
    remind_button: (() => {
      const btn = document.querySelector('#sidebarList .card-remind-btn');
      if (!btn) return '(no reminder button)';
      const box = btn.getBoundingClientRect();
      return { width: Math.round(box.width), height: Math.round(box.height) };
    })(),
    manifest_updated_at: (window.state && window.state.manifest
                          && window.state.manifest.updated_at) || '',
  };
}"""


def main() -> int:
    errors = []
    with sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
        page.on("console", lambda message: message.type == "error"
                and errors.append(f"console: {message.text}"))
        page.goto(f"{BASE}/index.html", wait_until="domcontentloaded")
        page.wait_for_timeout(9000)
        info = {"today": page.evaluate(READ)}

        # Report item [20]: the Upcoming card's stream readiness was hidden
        # below 1000px, so the phone never saw whether a link had arrived.
        # The nav button lives in a drawer at this width, so the view is
        # switched the way the page switches it.
        try:
            page.evaluate("() => selectMainView('upcoming')")
            page.wait_for_timeout(5000)
            info["upcoming"] = page.evaluate(READ)
        except Exception as error:  # noqa: BLE001 - reported, not raised
            info["upcoming"] = {"error": str(error)[:200]}
        browser.close()

    print(json.dumps(info, indent=2, ensure_ascii=False))
    print(f"runtime errors: {len(errors)}")
    for line in errors[:8]:
        print(f"  {line}")
    return 1 if errors or not info["today"].get("exists") else 0


if __name__ == "__main__":
    raise SystemExit(main())
