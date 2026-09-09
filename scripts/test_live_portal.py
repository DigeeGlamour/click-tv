import json
import sys
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

def run_test():
    artifact_dir = Path(r"C:\Users\RUMAN\.gemini\antigravity-ide\brain\3675eba0-f6d3-4594-b72c-6738af37ff73")
    results = {
        "url": "https://clicktv.pages.dev/?v=20260909_browser_verify",
        "console_errors": [],
        "checks": {}
    }
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        page.on("console", lambda msg: results["console_errors"].append(msg.text) if msg.type == "error" else None)
        
        print("Navigating to live URL...", flush=True)
        page.goto("https://clicktv.pages.dev/?v=20260909-movie-portal-final-v3", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4000)
        
        # Check 1: Header and Navigation structure
        header_capsule_count = page.locator(".header-nav-capsule").count()
        legacy_nav_count = page.locator(".desktop-main-navigation:visible, .channel-tabs:visible").count()
        brand_box = page.locator(".brand-wrap").bounding_box() or {}
        sidebar_box = page.locator(".sidebar-rail").bounding_box() or {}
        
        results["checks"]["header_capsule_count"] = header_capsule_count
        results["checks"]["legacy_nav_visible_count"] = legacy_nav_count
        results["checks"]["brand_x"] = brand_box.get("x")
        results["checks"]["sidebar_x"] = sidebar_box.get("x")
        
        # Check 2: Top View Screenshot
        ss1_path = artifact_dir / "live_screenshot_1_top_view.png"
        page.screenshot(path=str(ss1_path))
        results["checks"]["screenshot_1"] = str(ss1_path)
        print("Captured Screenshot 1 (Top View)", flush=True)
        
        # Check 3: Desktop Scrolling Capability
        scroll_info_before = page.evaluate("""() => {
            return {
                bodyOverflow: window.getComputedStyle(document.body).overflowY,
                htmlOverflow: window.getComputedStyle(document.documentElement).overflowY,
                bodyHeight: window.getComputedStyle(document.body).height,
                scrollHeight: document.documentElement.scrollHeight,
                windowHeight: window.innerHeight,
                scrollY: window.scrollY
            };
        }""")
        results["checks"]["scroll_info_before"] = scroll_info_before
        
        # Scroll down
        page.evaluate("window.scrollTo(0, 600)")
        page.wait_for_timeout(1000)
        scroll_y_after = page.evaluate("window.scrollY")
        results["checks"]["scroll_y_after"] = scroll_y_after
        
        # Screenshot 2: Scrolled Grid
        ss2_path = artifact_dir / "live_screenshot_2_scrolled.png"
        page.screenshot(path=str(ss2_path))
        results["checks"]["screenshot_2"] = str(ss2_path)
        print(f"Captured Screenshot 2 (Scrolled View, scrollY={scroll_y_after})", flush=True)
        
        # Scroll back up to test categories
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(500)
        
        # Check 4: Categories in Sidebar
        categories = page.evaluate("""() => {
            const items = Array.from(document.querySelectorAll('.sidebar-rail .cat-item'));
            return items.map(el => el.innerText.trim());
        }""")
        results["checks"]["sidebar_categories"] = categories
        
        # Click on 'Bangla' or first available category
        bangla_btn = page.locator(".sidebar-rail .cat-item:has-text('Bangla')").first
        if bangla_btn.count() > 0:
            bangla_btn.click()
            page.wait_for_timeout(2000)
            
        bangla_cards_count = page.locator(".movie-card").count()
        results["checks"]["cards_count_after_category_click"] = bangla_cards_count
        
        ss3_path = artifact_dir / "live_screenshot_3_category_bangla.png"
        page.screenshot(path=str(ss3_path))
        results["checks"]["screenshot_3"] = str(ss3_path)
        print(f"Captured Screenshot 3 (Category Click, cards={bangla_cards_count})", flush=True)
        
        # Check 5: Click a Movie Card to open Detail View
        first_card = page.locator(".movie-card").first
        if first_card.count() > 0:
            first_card.click()
            page.wait_for_timeout(1500)
            
            detail_visible = page.evaluate("""() => {
                const detail = document.querySelector('#detailSection');
                return detail && window.getComputedStyle(detail).display !== 'none' && detail.innerHTML.trim().length > 0;
            }""")
            results["checks"]["detail_view_opened"] = detail_visible
            
            ss4_path = artifact_dir / "live_screenshot_4_movie_detail.png"
            page.screenshot(path=str(ss4_path))
            results["checks"]["screenshot_4"] = str(ss4_path)
            print(f"Captured Screenshot 4 (Movie Detail View, opened={detail_visible})", flush=True)
        else:
            results["checks"]["detail_view_opened"] = False
            
        browser.close()
        
    print("TEST_RESULTS_JSON:" + json.dumps(results, indent=2))

if __name__ == "__main__":
    run_test()
