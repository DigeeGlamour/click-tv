#!/usr/bin/env python3
"""Exhaustively click and verify every Click TV channel and movie in real Chrome.

The audit deliberately drives the deployed UI. It does not call stream URLs,
scanner internals, or test hooks. Results are checkpointed after every item.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, async_playwright


CHANNEL_CATEGORIES = [
    ("sports", "Sports", "sports", "sports-channel"),
    ("bangla", "Bangla", "live-tv", "bangla"),
    ("indian", "Indian", "live-tv", "indian"),
    ("cartoon", "Cartoon", "live-tv", "cartoon"),
    ("islamic", "Islamic", "live-tv", "islamic"),
    ("infotainments", "Infotainments", "live-tv", "infotainments"),
    ("foreign-news", "Foreign News", "live-tv", "foreign-news"),
    ("others", "Others", "live-tv", "others"),
]

MOVIE_CATEGORIES = [
    ("bangla", "Bangla", "movies", "movie:bangla"),
    ("hindi", "Hindi", "movies", "movie:hindi"),
    ("english", "English", "movies", "movie:english"),
    ("dubbed", "Hindi Dubbed", "movies", "movie:dubbed"),
    ("south-indian", "South Indian", "movies", "movie:south-indian"),
    ("premium", "Premium", "movies", "movie:premium"),
    ("mix", "Mix Movies", "movies", "movie:mix"),
]

EVENT_CATEGORIES = [
    ("today-match", "Today Match", "sports", "today-match"),
]

CARD_SELECTOR = {
    "channel": "#sidebarList .sidebar-item[data-uid]",
    "movie": "#sidebarList .movie-card[data-uid]",
    "event": "#sidebarList .event-ref-card[data-uid]",
}


@dataclass(frozen=True)
class Category:
    kind: str
    key: str
    label: str
    main_key: str
    sub_key: str

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.key}"


def categories(kind_filter: str = "") -> list[Category]:
    result = [Category("event", *values) for values in EVENT_CATEGORIES]
    result.extend(Category("channel", *values) for values in CHANNEL_CATEGORIES)
    result.extend(Category("movie", *values) for values in MOVIE_CATEGORIES)
    return [category for category in result if not kind_filter or category.kind == kind_filter]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_host(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def classify_problem(snapshot: dict[str, Any], timeout: bool) -> str:
    error = str(snapshot.get("media_error") or "").strip()
    message = str(snapshot.get("player_message") or "").strip()
    toast = str(snapshot.get("toast") or "").strip()
    network = " ".join(snapshot.get("network_failures") or [])
    combined = " ".join((error, message, toast, network)).lower()
    if snapshot.get("embed_visible"):
        return "Embedded player opened; decoded playback could not be proven"
    if "drm" in combined or "license" in combined or "widevine" in combined:
        return "DRM/license playback error"
    if "cors" in combined or "access-control" in combined or "cross-origin" in combined:
        return "CORS/cross-origin request blocked"
    if "403" in combined or "forbidden" in combined:
        return "Source/proxy returned HTTP 403"
    if "404" in combined or "not found" in combined:
        return "Source/proxy returned HTTP 404"
    if "429" in combined or "too many" in combined:
        return "Source/proxy rate limited the request"
    if "err_blocked_by_orb" in combined:
        return "Browser blocked the media response (ORB; invalid or cross-origin media response)"
    if message and ("চালানো যায়নি" in message or "চালানো যায়নি" in message):
        return f"Player exhausted all playback routes: {message}"
    if "timeout" in combined or timeout:
        if snapshot.get("playing_seen") and not snapshot.get("progressed"):
            return "Player entered playing state but video did not progress"
        if snapshot.get("ready_state", 0) >= 2 and not snapshot.get("video_width"):
            return "Media loaded without a decoded video frame"
        return "Playback startup timed out"
    if error:
        return f"Browser media error: {error}"
    if network:
        return "Playback network request failed"
    if message:
        return message
    return "No decoded playback progress"


class Checkpoint:
    def __init__(self, output: Path, base_url: str, mode: str):
        self.output = output
        self.csv_output = output.with_suffix(".csv")
        self.journal_output = output.with_suffix(".jsonl")
        self.lock = asyncio.Lock()
        self.payload: dict[str, Any] = {
            "generated_at": utc_now(),
            "updated_at": utc_now(),
            "base_url": base_url,
            "test_mode": mode,
            "inventory": [],
            "results": [],
            "notes": [
                "Every result was produced by clicking the deployed UI card in visible desktop Google Chrome.",
                "PASS requires a decoded video frame and measurable playback progress.",
                "Raw or tokenized playback URLs are intentionally excluded from the report.",
            ],
        }

    def load_existing(self) -> None:
        if self.output.exists():
            existing = json.loads(self.output.read_text(encoding="utf-8"))
            if existing.get("base_url") == self.payload["base_url"]:
                self.payload = existing
        if self.journal_output.exists():
            keyed = {
                (row["category_id"], row["uid"]): row
                for row in self.payload.get("results", [])
            }
            for line in self.journal_output.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                row = entry.get("result")
                if row:
                    keyed[(row["category_id"], row["uid"])] = row
            self.payload["results"] = list(keyed.values())

    async def set_inventory(self, inventory: list[dict[str, Any]]) -> None:
        async with self.lock:
            self.payload["inventory"] = inventory
            self.payload["inventory_count"] = len(inventory)
            await self._write()

    async def replace_results(self, results: list[dict[str, Any]]) -> None:
        async with self.lock:
            self.payload["results"] = results
            await self._write()

    async def add_result(self, result: dict[str, Any]) -> None:
        async with self.lock:
            keyed = {
                (row["category_id"], row["uid"]): row
                for row in self.payload.get("results", [])
            }
            key = (result["category_id"], result["uid"])
            previous = keyed.get(key)
            if previous:
                attempts = list(previous.get("attempt_history") or [])
                attempts.append({k: v for k, v in previous.items() if k != "attempt_history"})
                result["attempt_history"] = attempts
            keyed[key] = result
            ordered = []
            for item in self.payload.get("inventory", []):
                row = keyed.get((item["category_id"], item["uid"]))
                if row:
                    ordered.append(row)
            self.payload["results"] = ordered
            self.payload["tested_count"] = len(ordered)
            self.payload["status_counts"] = dict(Counter(row.get("status", "") for row in ordered))
            with self.journal_output.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"at": utc_now(), "result": result}, ensure_ascii=False) + "\n")
            if len(ordered) % 10 == 0:
                await self._write()

    async def flush(self) -> None:
        async with self.lock:
            await self._write()

    async def _write(self) -> None:
        results = self.payload.get("results", [])
        counts = Counter(row.get("status", "") for row in results)
        self.payload["updated_at"] = utc_now()
        self.payload["tested_count"] = len(results)
        self.payload["status_counts"] = dict(counts)
        temp = self.output.with_suffix(self.output.suffix + ".tmp")
        temp.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, self.output)
        self._write_csv(results)

    def _write_csv(self, results: list[dict[str, Any]]) -> None:
        columns = [
            "serial", "kind", "category", "name", "status", "load_time_seconds",
            "problem", "browser_evidence", "attempt", "tested_at", "uid",
        ]
        temp = self.csv_output.with_suffix(self.csv_output.suffix + ".tmp")
        with temp.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for serial, row in enumerate(results, 1):
                copy = dict(row)
                copy["serial"] = serial
                writer.writerow(copy)
        os.replace(temp, self.csv_output)


async def install_tracker(page: Page) -> None:
    await page.evaluate(
        """
        () => {
          const video = document.getElementById('videoPlayer');
          if (!video || window.__clickTvFullAuditInstalled) return;
          window.__clickTvFullAuditInstalled = true;
          window.__clickTvFullAudit = { token: 0, startedAt: 0, events: [] };
          const names = ['loadstart','loadedmetadata','loadeddata','canplay','play','playing','waiting','stalled','error','timeupdate'];
          for (const name of names) {
            video.addEventListener(name, () => {
              const audit = window.__clickTvFullAudit;
              if (!audit || !audit.startedAt) return;
              const elapsed = performance.now() - audit.startedAt;
              if (name !== 'timeupdate' || !audit.events.some((entry) => entry.name === 'timeupdate')) {
                audit.events.push({ name, elapsed, currentTime: Number(video.currentTime || 0), readyState: video.readyState });
              }
            });
          }
          window.__clickTvFullAuditReset = () => {
            window.__clickTvFullAudit = { token: Date.now(), startedAt: performance.now(), events: [] };
            return window.__clickTvFullAudit.token;
          };
        }
        """
    )


async def open_site(context: BrowserContext, base_url: str) -> Page:
    page = await context.new_page()
    await page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
    await page.locator("#desktopMainNav .final-main-button").first.wait_for(
        state="visible", timeout=60_000
    )
    await install_tracker(page)
    return page


async def select_category(page: Page, category: Category) -> None:
    main = page.locator(f'#desktopMainNav [data-final-key="{category.main_key}"]')
    await main.click(timeout=30_000)
    sub = page.locator(f'#desktopSubNav [data-final-key="{category.sub_key}"]')
    await sub.wait_for(state="visible", timeout=30_000)
    await sub.click(timeout=30_000)
    selector = CARD_SELECTOR[category.kind]
    try:
        await page.locator(selector).first.wait_for(state="attached", timeout=30_000)
    except Exception:
        count_text = await page.locator("#sidebarCountText").inner_text()
        if not re.search(r"\b0\b", count_text):
            raise


def parse_total(text: str, current: int) -> int:
    match = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if match:
        return int(match.group(2))
    match = re.search(r"(\d+)\s+(?:Channels|Movies|Items|Events|Matches)", text, flags=re.I)
    if match:
        return int(match.group(1))
    return current


async def load_all_cards(page: Page, category: Category) -> tuple[list[dict[str, Any]], str]:
    selector = CARD_SELECTOR[category.kind]
    stable = 0
    previous = -1
    target = 0
    for _ in range(260):
        count = await page.locator(selector).count()
        text = (await page.locator("#sidebarCountText").inner_text()).strip()
        target = max(target, parse_total(text, count))
        if count >= target and target > 0:
            stable += 1
            if stable >= 4:
                break
        elif count == previous:
            stable += 1
        else:
            stable = 0
        previous = count
        await page.evaluate(
            """
            () => {
              for (const id of ['sidebarScrollArea','sidebarList','sidebarSection']) {
                const node = document.getElementById(id);
                if (!node) continue;
                node.scrollTop = node.scrollHeight;
                node.dispatchEvent(new Event('scroll', { bubbles: true }));
              }
            }
            """
        )
        await page.wait_for_timeout(350 if category.kind == "movie" else 180)
    count = await page.locator(selector).count()
    text = (await page.locator("#sidebarCountText").inner_text()).strip()
    target = max(target, parse_total(text, count))
    if target and count < target:
        raise RuntimeError(f"{category.id}: rendered {count}/{target} cards ({text})")
    cards = await page.locator(selector).evaluate_all(
        """els => els.map((e, index) => ({
          uid: e.dataset.uid || '',
          name: e.getAttribute('aria-label') || e.querySelector('.sidebar-name,.movie-card-title')?.textContent?.trim() || '',
          visual_index: Number(e.dataset.itemIndex || index),
        }))"""
    )
    seen: set[str] = set()
    unique = []
    for card in cards:
        uid = str(card.get("uid") or "")
        if not uid or uid in seen:
            continue
        seen.add(uid)
        unique.append(card)
    if len(unique) != len(cards):
        raise RuntimeError(f"{category.id}: duplicate or missing DOM UID ({len(unique)}/{len(cards)})")
    return unique, text


async def inventory_site(browser: Browser, args: argparse.Namespace) -> list[dict[str, Any]]:
    context = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        locale="en-US",
        timezone_id="Asia/Dhaka",
    )
    page = await open_site(context, args.base_url)
    inventory: list[dict[str, Any]] = []
    try:
        for category in categories(args.only_kind):
            await select_category(page, category)
            cards, count_text = await load_all_cards(page, category)
            for order, card in enumerate(cards, 1):
                inventory.append({
                    "kind": category.kind,
                    "category": category.label,
                    "category_key": category.key,
                    "category_id": category.id,
                    "uid": card["uid"],
                    "name": card["name"],
                    "category_order": order,
                })
            print(f"INVENTORY {category.id} count={len(cards)} label={count_text}", flush=True)
    finally:
        await context.close()
    return inventory


async def snapshot(page: Page, failures: list[str]) -> dict[str, Any]:
    data = await page.evaluate(
        """
        () => {
          const v = document.getElementById('videoPlayer');
          const audit = window.__clickTvFullAudit || { events: [] };
          const visible = (node) => Boolean(node && getComputedStyle(node).display !== 'none' && node.getBoundingClientRect().width > 0);
          const event = (name) => audit.events.find((entry) => entry.name === name);
          const mediaError = v?.error;
          return {
            ready_state: Number(v?.readyState || 0),
            current_time: Number(v?.currentTime || 0),
            duration: Number.isFinite(v?.duration) ? Number(v.duration) : null,
            video_width: Number(v?.videoWidth || 0),
            video_height: Number(v?.videoHeight || 0),
            paused: Boolean(v?.paused),
            ended: Boolean(v?.ended),
            media_error: mediaError ? `${mediaError.code}: ${mediaError.message || ''}` : '',
            player_message: visible(document.getElementById('playerMsg')) ? (document.getElementById('playerMsgText')?.textContent || '').trim() : '',
            toast: visible(document.getElementById('osdToast')) ? (document.getElementById('osdToast')?.textContent || '').trim() : '',
            embed_visible: visible(document.querySelector('iframe')),
            playing_seen: Boolean(event('playing')),
            playing_ms: event('playing') ? Math.round(event('playing').elapsed) : null,
            loadeddata_ms: event('loadeddata') ? Math.round(event('loadeddata').elapsed) : null,
            canplay_ms: event('canplay') ? Math.round(event('canplay').elapsed) : null,
            events: audit.events.slice(-24),
          };
        }
        """
    )
    data["network_failures"] = failures[-8:]
    return data


async def test_one(
    page: Page,
    category: Category,
    item: dict[str, Any],
    timeout_seconds: float,
    attempt: int,
) -> dict[str, Any]:
    failures: list[str] = []

    def on_request_failed(request: Any) -> None:
        if request.resource_type not in {"media", "xhr", "fetch"}:
            return
        failure = request.failure or "request failed"
        failures.append(f"{safe_host(request.url)}: {failure}")

    page.on("requestfailed", on_request_failed)
    selector = f'{CARD_SELECTOR[category.kind]}[data-uid="{item["uid"]}"]'
    card = page.locator(selector)
    try:
        if await card.count() != 1:
            raise RuntimeError("Card missing from rendered catalogue")
        await card.scroll_into_view_if_needed(timeout=15_000)
        await page.evaluate("window.__clickTvFullAuditReset()")
        started = asyncio.get_running_loop().time()
        await card.click(timeout=15_000)
        success = False
        progressed = False
        terminal_failure = False
        last_time = 0.0
        while asyncio.get_running_loop().time() - started < timeout_seconds:
            await page.wait_for_timeout(250)
            snap = await snapshot(page, failures)
            current = float(snap.get("current_time") or 0)
            if current > last_time + 0.12:
                progressed = True
            last_time = max(last_time, current)
            active = await card.evaluate("e => e.classList.contains('active')")
            if (
                active
                and snap.get("playing_seen")
                and snap.get("ready_state", 0) >= 2
                and snap.get("video_width", 0) > 0
                and progressed
            ):
                success = True
                break
            message = str(snap.get("player_message") or "")
            if (
                asyncio.get_running_loop().time() - started >= 2.0
                and ("চালানো যায়নি" in message or "চালানো যায়নি" in message)
            ):
                terminal_failure = True
                break
        elapsed = asyncio.get_running_loop().time() - started
        snap = await snapshot(page, failures)
        snap["progressed"] = progressed
        evidence = (
            f"readyState={snap['ready_state']}; video={snap['video_width']}x{snap['video_height']}; "
            f"currentTime={snap['current_time']:.2f}s; playing={snap['playing_seen']}; progressed={progressed}"
        )
        problem = "" if success else classify_problem(snap, timeout=not terminal_failure)
        status = "PASS" if success else "FAIL"
        return {
            **item,
            "status": status,
            "load_time_seconds": round(elapsed, 3) if success else "",
            "problem": problem,
            "browser_evidence": evidence,
            "attempt": attempt,
            "tested_at": utc_now(),
            "diagnostic": snap,
        }
    except Exception as exc:
        snap = await snapshot(page, failures)
        snap["progressed"] = False
        return {
            **item,
            "status": "ERROR",
            "load_time_seconds": "",
            "problem": f"Audit interaction error: {type(exc).__name__}: {exc}",
            "browser_evidence": (
                f"readyState={snap.get('ready_state', 0)}; video={snap.get('video_width', 0)}x"
                f"{snap.get('video_height', 0)}; currentTime={snap.get('current_time', 0):.2f}s"
            ),
            "attempt": attempt,
            "tested_at": utc_now(),
            "diagnostic": snap,
        }
    finally:
        page.remove_listener("requestfailed", on_request_failed)


def category_map() -> dict[str, Category]:
    return {category.id: category for category in categories()}


def make_jobs(items: list[dict[str, Any]], target_size: int) -> list[tuple[Category, list[dict[str, Any]]]]:
    mapping = category_map()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(item["category_id"], []).append(item)
    jobs = []
    for category_id, records in grouped.items():
        chunk_count = max(1, math.ceil(len(records) / target_size))
        for shard in range(chunk_count):
            subset = records[shard::chunk_count]
            if subset:
                jobs.append((mapping[category_id], subset))
    jobs.sort(key=lambda entry: len(entry[1]), reverse=True)
    return jobs


async def run_job(
    number: int,
    browser: Browser,
    args: argparse.Namespace,
    checkpoint: Checkpoint,
    category: Category,
    items: list[dict[str, Any]],
    attempt: int,
) -> None:
    context = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        locale="en-US",
        timezone_id="Asia/Dhaka",
    )
    page = await open_site(context, args.base_url)
    try:
        await select_category(page, category)
        cards, _ = await load_all_cards(page, category)
        loaded_uids = {card["uid"] for card in cards}
        missing = [item for item in items if item["uid"] not in loaded_uids]
        if missing:
            # A live list is allowed to change between the inventory pass and
            # this one. Today Match showed 25 cards when the inventory ran and
            # 21 by the time the job started, because matches had ended - and
            # raising here failed the whole job of 25, so twenty-one testable
            # cards were reported as ERROR alongside the four that had simply
            # gone. Measured 2026-08-30: 25 event rows, every one an ERROR
            # reading "3 assigned cards missing after full render".
            #
            # A card that has left a live category is recorded as GONE and the
            # rest of the job proceeds. For a fixed catalogue - channels and
            # movies - a missing card is still a real problem, so it stays an
            # error there.
            if category.kind == "event":
                for item in missing:
                    await checkpoint.add_result({
                        **{key: item[key] for key in item if key != "diagnostic"},
                        "status": "GONE",
                        "load_time_seconds": "",
                        "problem": "No longer listed in this live category when the "
                                   "job ran; the fixture had ended or moved tab",
                        "browser_evidence": "",
                        "attempt": attempt,
                        "tested_at": utc_now(),
                        "diagnostic": {},
                    })
                gone = {item["uid"] for item in missing}
                items = [item for item in items if item["uid"] not in gone]
                print(
                    f"GONE category={category.id} count={len(gone)} "
                    f"remaining={len(items)}",
                    flush=True,
                )
                if not items:
                    return
            else:
                raise RuntimeError(
                    f"{category.id}: {len(missing)} assigned cards missing after full render"
                )
        timeout = args.movie_timeout if category.kind == "movie" else args.channel_timeout
        if attempt > 1:
            timeout = args.retry_timeout
        for index, item in enumerate(items, 1):
            result = await test_one(page, category, item, timeout, attempt)
            await checkpoint.add_result(result)
            counts = checkpoint.payload.get("status_counts", {})
            print(
                f"RESULT job={number} item={index}/{len(items)} total={checkpoint.payload.get('tested_count', 0)}/"
                f"{checkpoint.payload.get('inventory_count', 0)} status={result['status']} "
                f"pass={counts.get('PASS', 0)} fail={counts.get('FAIL', 0)} error={counts.get('ERROR', 0)} "
                f"category={category.id} name={item['name']}",
                flush=True,
            )
    finally:
        await context.close()


async def run_jobs(
    browser: Browser,
    args: argparse.Namespace,
    checkpoint: Checkpoint,
    items: list[dict[str, Any]],
    workers: int,
    attempt: int,
) -> None:
    queue: asyncio.Queue[tuple[int, Category, list[dict[str, Any]]]] = asyncio.Queue()
    jobs = make_jobs(items, args.job_size if attempt == 1 else max(1, args.retry_job_size))
    for number, (category, subset) in enumerate(jobs, 1):
        queue.put_nowait((number, category, subset))

    async def worker(worker_id: int) -> None:
        while True:
            try:
                number, category, subset = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            print(
                f"JOB_START worker={worker_id} job={number}/{len(jobs)} category={category.id} items={len(subset)} attempt={attempt}",
                flush=True,
            )
            try:
                await run_job(number, browser, args, checkpoint, category, subset, attempt)
            except Exception as exc:
                print(f"JOB_ERROR job={number} category={category.id} error={type(exc).__name__}: {exc}", flush=True)
                for item in subset:
                    await checkpoint.add_result({
                        **item,
                        "status": "ERROR",
                        "load_time_seconds": "",
                        "problem": f"Category audit job failed: {type(exc).__name__}: {exc}",
                        "browser_evidence": "",
                        "attempt": attempt,
                        "tested_at": utc_now(),
                        "diagnostic": {},
                    })
            finally:
                queue.task_done()

    await asyncio.gather(*(worker(index + 1) for index in range(min(workers, len(jobs)))))


async def main_async(args: argparse.Namespace) -> int:
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = Checkpoint(output, args.base_url, "visible Chrome UI clicks")
    if args.resume:
        checkpoint.load_existing()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=args.headless,
            executable_path=args.chrome,
            args=[
                "--autoplay-policy=no-user-gesture-required",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
            ],
        )
        try:
            inventory = checkpoint.payload.get("inventory") or await inventory_site(browser, args)
            await checkpoint.set_inventory(inventory)
            if args.inventory_only:
                print(f"INVENTORY_COMPLETE total={len(inventory)} report={output}", flush=True)
                return 0
            inventory_keys = {(row["category_id"], row["uid"]) for row in inventory}
            existing = {
                (row["category_id"], row["uid"]): row
                for row in checkpoint.payload.get("results", [])
            }
            first_pass = [row for row in inventory if (row["category_id"], row["uid"]) not in existing]
            if args.force_uid:
                first_pass = [row for row in inventory if row["uid"] == args.force_uid]
                if not first_pass:
                    raise RuntimeError(f"Requested --force-uid was not found: {args.force_uid}")
            if args.max_items > 0:
                first_pass = first_pass[:args.max_items]
            print(
                f"AUDIT_START inventory={len(inventory)} already_tested={len(existing)} "
                f"already_passed={sum(1 for row in existing.values() if row.get('status') == 'PASS')} "
                f"first_pass={len(first_pass)} workers={args.workers}",
                flush=True,
            )
            if first_pass:
                await run_jobs(browser, args, checkpoint, first_pass, args.workers, 1)
                await checkpoint.flush()

            if not args.no_retry:
                latest = {
                    (row["category_id"], row["uid"]): row
                    for row in checkpoint.payload.get("results", [])
                }
                retry_items = [
                    row for row in inventory
                    if latest.get((row["category_id"], row["uid"]), {}).get("status") != "PASS"
                ]
                if retry_items:
                    print(f"RETRY_START items={len(retry_items)} workers={args.retry_workers}", flush=True)
                    await run_jobs(browser, args, checkpoint, retry_items, args.retry_workers, 2)
                    await checkpoint.flush()
        finally:
            await browser.close()

    final = {
        (row["category_id"], row["uid"]): row
        for row in checkpoint.payload.get("results", [])
    }
    missing = [key for key in inventory_keys if key not in final]
    counts = Counter(row.get("status") for row in final.values())
    print(
        f"AUDIT_COMPLETE inventory={len(inventory_keys)} tested={len(final)} missing={len(missing)} "
        f"pass={counts.get('PASS', 0)} fail={counts.get('FAIL', 0)} error={counts.get('ERROR', 0)} "
        f"report={output}",
        flush=True,
    )
    return 0 if not missing else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://clicktv.pages.dev/")
    parser.add_argument("--output", default="reports/clicktv-full-live-playback-audit.json")
    parser.add_argument("--chrome", default=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--retry-workers", type=int, default=1)
    parser.add_argument("--job-size", type=int, default=55)
    parser.add_argument("--retry-job-size", type=int, default=20)
    parser.add_argument("--channel-timeout", type=float, default=22.0)
    parser.add_argument("--movie-timeout", type=float, default=32.0)
    parser.add_argument("--retry-timeout", type=float, default=48.0)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--no-retry", action="store_true")
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--max-items", type=int, default=0)
    parser.add_argument("--force-uid", default="")
    parser.add_argument("--only-kind", choices=("event", "channel", "movie"), default="")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main_async(parse_args())))
    except KeyboardInterrupt:
        print("AUDIT_INTERRUPTED checkpoint preserved", file=sys.stderr)
        raise SystemExit(130)
