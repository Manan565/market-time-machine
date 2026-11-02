"""Drive the built app in Chromium: load, play, scrub, race. Reports problems, saves screenshots.

Usage: python scripts/e2e_check.py OUT_DIR [BASE_URL]
Needs the server running (`make serve`) and Playwright with Chromium installed.
"""

import sys

from playwright.sync_api import sync_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/shots"
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8000/"
problems = []

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.on("console", lambda m: m.type in ("error", "warning") and problems.append(f"console {m.type}: {m.text}"))
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("requestfailed", lambda r: problems.append(f"request failed: {r.url}"))
    page.on("response", lambda r: r.status >= 400 and problems.append(f"HTTP {r.status}: {r.url}"))

    page.goto(BASE)
    page.wait_for_selector("canvas", timeout=15000)
    page.wait_for_timeout(800)
    page.screenshot(path=f"{OUT}/1_loaded.png", full_page=True)

    clock0 = page.inner_text(".clock")
    page.select_option(".speed select", "3000")
    page.click(".play")
    page.wait_for_timeout(3500)
    clock1 = page.inner_text(".clock")
    price1 = page.inner_text(".quote-price")
    page.screenshot(path=f"{OUT}/2_playing.png")
    page.click(".play")  # pause
    page.wait_for_timeout(400)
    paused_at = page.inner_text(".clock")
    page.wait_for_timeout(600)
    still = page.inner_text(".clock")

    # scrub with the keyboard on the range input
    page.focus("input[type=range]")
    page.evaluate("""() => {
      const el = document.querySelector('input[type=range]');
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      set.call(el, 52200); el.dispatchEvent(new Event('input', {bubbles: true}));
    }""")
    page.wait_for_timeout(600)
    scrubbed = page.inner_text(".clock")
    scrub_price = page.inner_text(".quote-price")
    page.screenshot(path=f"{OUT}/3_scrubbed.png")

    # race
    page.click("text=Run the race")
    page.wait_for_selector(".verdict", timeout=30000)
    page.wait_for_timeout(300)
    page.screenshot(path=f"{OUT}/4_race.png", full_page=True)
    verdict = page.inner_text(".verdict p")
    lane_times = page.locator(".lane-time").all_inner_texts()

    # switch day and symbol
    page.click(".day >> nth=1")
    page.wait_for_timeout(700)
    day_label = page.inner_text(".date")
    page.click("text=ETHUSDT")
    page.wait_for_timeout(900)
    eth_label = page.inner_text(".quote-symbol")

    # phone layout
    phone = browser.new_page(viewport={"width": 390, "height": 844})
    phone.on("pageerror", lambda e: problems.append(f"phone pageerror: {e}"))
    phone.goto(BASE)
    phone.wait_for_selector("canvas", timeout=15000)
    phone.wait_for_timeout(800)
    overflow = phone.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    phone.screenshot(path=f"{OUT}/5_phone.png", full_page=True)
    browser.close()

print("clock before/after play:", clock0, "->", clock1, "| price", price1)
print("paused:", paused_at, "then", still, "(should match)")
print("scrubbed to:", scrubbed, scrub_price)
print("race lane times:", lane_times)
print("verdict:", verdict)
print("after day click:", day_label, "| symbol:", eth_label)
print("phone horizontal overflow px:", overflow)
print("PROBLEMS:", problems or "none")

sys.exit(1 if problems else 0)
