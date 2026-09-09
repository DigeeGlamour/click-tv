import http.server
import socketserver
import threading
from pathlib import Path
from playwright.sync_api import sync_playwright

PORT = 8765
DIST_DIR = Path(__file__).resolve().parent.parent / "dist"

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST_DIR), **kwargs)
    def log_message(self, format, *args):
        pass # suppress console noise

def run_tests():
    artifact_dir = Path(r"C:\Users\RUMAN\.gemini\antigravity-ide\brain\3675eba0-f6d3-4594-b72c-6738af37ff73")
    httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    print(f"Local test server running on http://127.0.0.1:{port}", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Mobile viewport: 390 x 844 (iPhone 14 / modern Android standard)
        context = browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
        )
        page = context.new_page()
        page.goto(f"http://127.0.0.1:{port}/index.html", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        # 1. Mobile Movies top view
        ss1 = artifact_dir / "mobile_v3_movies_top.png"
        page.screenshot(path=str(ss1))
        print("Captured 1: mobile_v3_movies_top.png", flush=True)

        # 2. Scroll down 400px on mobile
        page.evaluate("window.scrollTo(0, 400)")
        page.wait_for_timeout(600)
        ss2 = artifact_dir / "mobile_v3_movies_scrolled.png"
        page.screenshot(path=str(ss2))
        print("Captured 2: mobile_v3_movies_scrolled.png", flush=True)

        # 3. Category click on 'Bangla'
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(400)
        bangla_btn = page.locator(".sidebar-rail .cat-item:has-text('Bangla')").first
        if bangla_btn.count() > 0:
            bangla_btn.click()
            page.wait_for_timeout(1000)
        ss3 = artifact_dir / "mobile_v3_category_bangla.png"
        page.screenshot(path=str(ss3))
        print("Captured 3: mobile_v3_category_bangla.png", flush=True)

        # 4. Detail View on mobile
        first_card = page.locator(".movie-card").first
        if first_card.count() > 0:
            first_card.click()
            page.wait_for_timeout(1000)
            ss4 = artifact_dir / "mobile_v3_movie_detail.png"
            page.screenshot(path=str(ss4))
            print("Captured 4: mobile_v3_movie_detail.png", flush=True)

            back_btn = page.locator("#detailBackBtn")
            if back_btn.count() > 0:
                back_btn.click()
                page.wait_for_timeout(500)

        # 5. Mobile Search Open
        search_btn = page.locator("#mobileBottomSearchBtn")
        if search_btn.count() > 0:
            search_btn.click()
            page.wait_for_timeout(500)
            ss_search = artifact_dir / "mobile_v3_search_open.png"
            page.screenshot(path=str(ss_search))
            print("Captured 5: mobile_v3_search_open.png", flush=True)

        # 6. Live TV view on mobile
        live_tv_btn = page.locator("#navLiveTvBtn")
        if live_tv_btn.count() > 0:
            live_tv_btn.click()
            page.wait_for_timeout(1500)
            ss5 = artifact_dir / "mobile_v3_live_tv.png"
            page.screenshot(path=str(ss5))
            print("Captured 6: mobile_v3_live_tv.png", flush=True)

        # 7. Live Sports view on mobile
        sports_btn = page.locator("#navLiveSportsBtn")
        if sports_btn.count() > 0:
            sports_btn.click()
            page.wait_for_timeout(1500)
            ss6 = artifact_dir / "mobile_v3_live_sports.png"
            page.screenshot(path=str(ss6))
            print("Captured 7: mobile_v3_live_sports.png", flush=True)

        browser.close()
    httpd.shutdown()
    print("Local verification complete.", flush=True)

if __name__ == "__main__":
    run_tests()
