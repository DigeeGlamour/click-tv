/**
 * Runtime test for workers/scan-trigger — the scheduler Worker.
 *
 * Run: node tests/scan-trigger-worker-runtime.mjs
 *
 * The Worker's whole job is to press the right button and then be honest about
 * what happened. The things worth proving are exactly the ones that are easy to
 * get wrong:
 *
 *   1. each cron presses ITS OWN button - a today cron must never dispatch
 *      upcoming-targeted, and a cron the Worker does not recognise must press
 *      nothing at all rather than guess;
 *   2. only HTTP 204 counts as accepted - GitHub documents 204 for this
 *      endpoint, and treating any 2xx as success would hide a redirect or a
 *      202 that means something else;
 *   3. a dispatch that was accepted is still not a run - the endpoint returns
 *      no run id, so the Worker must never report one it did not verify;
 *   4. a run belonging to somebody else is never claimed as ours;
 *   5. each mode watches its own report, at its own cadence.
 *
 * Nothing here talks to GitHub, Cloudflare or Telegram: `fetch` is replaced for
 * the duration of each case.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..");
const WORKER = join(ROOT, "workers", "scan-trigger", "src", "index.js");
const TOML = join(ROOT, "workers", "scan-trigger", "wrangler.toml");
const WORKFLOW = join(ROOT, ".github", "workflows", "scan.yml");

const TODAY_CRON = "3,23,43 * * * *";
const TARGETED_CRON = "1-59/5 * * * *";

let failures = 0;
function check(name, condition, detail = "") {
  if (condition) {
    console.log(`  ok   ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL ${name}${detail ? ` - ${detail}` : ""}`);
  }
}

const source = readFileSync(WORKER, "utf8");
const toml = readFileSync(TOML, "utf8");
const workflow = readFileSync(WORKFLOW, "utf8");

console.log("scan-trigger worker");

// ---------------------------------------------------------------- shape ----
check("it is a scheduler: a scheduled handler exists",
  /async\s+scheduled\s*\(/.test(source));
check("it is ONLY a scheduler: no fetch handler is exported",
  !/^\s*async\s+fetch\s*\(/m.test(source));
check("no storage binding is used anywhere",
  !/\benv\.(KV|DB|D1|BUCKET|QUEUE)\b/.test(source));
// Read the configuration, not the prose that says the configuration is empty:
// the toml's own comments name the bindings it deliberately does not use.
const tomlSettings = toml
  .split("\n")
  .filter((line) => !line.trim().startsWith("#"))
  .join("\n");
check("no KV, D1, durable object or queue is configured",
  !/\b(kv_namespaces|d1_databases|durable_objects|queues)\b/.test(tomlSettings));
check("workers_dev is off, so nothing is publicly reachable",
  /workers_dev\s*=\s*false/.test(toml));
check("no route or custom domain is configured either",
  !/^\s*(routes?|route)\s*=/m.test(tomlSettings)
  && !/^\s*\[\[routes\]\]/m.test(tomlSettings));
check("no [vars] block: every input is a secret",
  !/^\s*\[vars\]/m.test(toml));

// -------------------------------------------------------------- cadence ----
const workerCrons = [...tomlSettings.matchAll(/"([^"]+)"/g)]
  .map((m) => m[1])
  .filter((value) => /^[\d,*/\- ]+$/.test(value) && value.split(" ").length === 5);
check("the worker schedules exactly two crons", workerCrons.length === 2,
  JSON.stringify(workerCrons));
check("both are the repository's own declared cadences",
  workerCrons.includes(TODAY_CRON) && workerCrons.includes(TARGETED_CRON)
  && workflow.includes(`"${TODAY_CRON}"`) && workflow.includes(`"${TARGETED_CRON}"`),
  JSON.stringify(workerCrons));
check("every scheduled cron has a mode in SCHEDULES, and vice versa",
  workerCrons.every((cron) => source.includes(`"${cron}":`))
  && [...source.matchAll(/^\s{2}"([^"]+)":\s*\{/gm)].map((m) => m[1]).sort()
     .join("|") === [...workerCrons].sort().join("|"),
  JSON.stringify([...source.matchAll(/^\s{2}"([^"]+)":\s*\{/gm)].map((m) => m[1])));

// The rule findOurRun leans on: the two dispatch minute sets never come within
// the one minute of backward clock slack the run lookup allows. If a cron is
// ever retimed so that they do, a today tick could name a targeted run as its
// own, and every later report would be untrustworthy. Expand both crons and
// measure the real gap rather than trusting the comment that claims it.
function minutesOf(cron) {
  const field = cron.split(" ")[0];
  const out = new Set();
  for (const part of field.split(",")) {
    const step = part.includes("/") ? Number(part.split("/")[1]) : 1;
    const range = part.split("/")[0];
    if (range === "*") {
      for (let m = 0; m < 60; m += step) out.add(m);
    } else if (range.includes("-")) {
      const [lo, hi] = range.split("-").map(Number);
      for (let m = lo; m <= hi; m += step) out.add(m);
    } else {
      out.add(Number(range));
    }
  }
  return [...out];
}
{
  const today = minutesOf(TODAY_CRON);
  const targeted = minutesOf(TARGETED_CRON);
  let closest = 60;
  for (const a of today) {
    for (const b of targeted) {
      const gap = Math.min(Math.abs(a - b), 60 - Math.abs(a - b));
      if (gap < closest) closest = gap;
    }
  }
  check("the two crons never fire within the run lookup's 1 minute of slack",
    closest >= 2, `closest gap is ${closest} min`);
}

// -------------------------------------------------------------- secrets ----
check("no literal token of any shape is in the worker source",
  !/(github_pat_|ghp_|gho_)[A-Za-z0-9_]{10,}/.test(source + toml));
check("the github token is read from env, not embedded",
  /env\.GITHUB_DISPATCH_TOKEN/.test(source));
check("it reuses the workflow's own Telegram secret names",
  /env\.TELEGRAM_BOT_TOKEN/.test(source) && /env\.TELEGRAM_CHAT_ID/.test(source)
  && workflow.includes("TELEGRAM_BOT_TOKEN") && workflow.includes("TELEGRAM_CHAT_ID"));

// ---------------------------------------------------- workflow agreement ----
// The Worker dispatches a mode by name. If scan.yml ever stops offering one of
// those names, the dispatch is rejected at the API and the mode silently stops
// running, so the agreement is asserted here rather than discovered live.
for (const mode of ["today", "upcoming-targeted"]) {
  check(`scan.yml still offers the "${mode}" dispatch input`,
    new RegExp(`^\\s+- ${mode}\\s*$`, "m").test(workflow), mode);
}
check("scan.yml puts a dispatched upcoming-targeted in the targeted group",
  /inputs\.mode == 'upcoming-targeted'\)\s*\n\s*&& 'targeted-v1'/.test(workflow));
check("scan.yml cancels in progress for a dispatched upcoming-targeted",
  /cancel-in-progress:[\s\S]{0,200}inputs\.mode == 'upcoming-targeted'/.test(workflow));
check("scan.yml runs the superseded guard for both dispatched modes",
  /inputs\.mode == 'today'\s*\n\s*\|\| inputs\.mode == 'upcoming-targeted'/.test(workflow));
check("scan.yml skips the full suite for upcoming-targeted, however triggered",
  workflow.includes("if: steps.scan_mode.outputs.mode != 'upcoming-targeted'"));

// ------------------------------------------------------------- behaviour ---
const worker = (await import("file://" + WORKER.replace(/\\/g, "/"))).default;

/**
 * Run one scheduled tick with `fetch` replaced; return every call it made.
 * `cron` is what Cloudflare hands the handler as `controller.cron`.
 */
async function tick(env, handler, ...rest) {
  // Rest arguments rather than default parameters, because one of the cases
  // below is "Cloudflare handed us a controller with no cron at all" and a
  // default parameter fires on an explicit `undefined` - which would quietly
  // turn that case into a today tick and pass for the wrong reason.
  const cron = rest.length > 0 ? rest[0] : TODAY_CRON;
  const scheduledTime = rest.length > 1 && rest[1] !== undefined
    ? rest[1]
    : Date.now();
  const calls = [];
  const real = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    return handler(String(url), options, calls.length);
  };
  const waits = [];
  try {
    await worker.scheduled(
      { cron, scheduledTime },
      env,
      { waitUntil: (promise) => waits.push(promise) },
    );
    await Promise.all(waits);
  } finally {
    globalThis.fetch = real;
  }
  return calls;
}

/** Same, but capture what the tick said on the console. */
async function say(env, handler, ...rest) {
  const cron = rest.length > 0 ? rest[0] : TODAY_CRON;
  const scheduledTime = rest.length > 1 && rest[1] !== undefined
    ? rest[1]
    : Date.now();
  const said = [];
  const realLog = console.log;
  const realError = console.error;
  console.log = (...args) => said.push(args.join(" "));
  console.error = (...args) => said.push(args.join(" "));
  try {
    const calls = await tick(env, handler, cron, scheduledTime);
    return { calls, spoken: said.join("\n") };
  } finally {
    console.log = realLog;
    console.error = realError;
  }
}

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status });
const freshFor = (mode) =>
  json({ last_scan: new Date().toISOString(), mode });

/** A tick where everything works; returns what was dispatched and asked for. */
async function happyTick(cron, scheduledTime = Date.now()) {
  let dispatchBody = null;
  let freshnessUrl = "";
  let runsUrl = "";
  const calls = await tick({ GITHUB_DISPATCH_TOKEN: "t" }, (url, options) => {
    if (url.includes("/dispatches")) {
      dispatchBody = JSON.parse(options.body);
      return new Response(null, { status: 204 });
    }
    if (url.includes("/runs")) { runsUrl = url; return json({ workflow_runs: [] }); }
    freshnessUrl = url;
    return json({ last_scan: new Date().toISOString() });
  }, cron, scheduledTime);
  return { calls, dispatchBody, freshnessUrl, runsUrl };
}

// --- each cron presses its own button -------------------------------------
{
  const { calls, dispatchBody, freshnessUrl } = await happyTick(TODAY_CRON);
  check("the today cron POSTs to the workflow's dispatches endpoint",
    calls.some((c) => c.url.endsWith("/actions/workflows/scan.yml/dispatches")
      && c.options.method === "POST"));
  check("the today cron sends ref=main and mode=today",
    dispatchBody && dispatchBody.ref === "main"
    && dispatchBody.inputs.mode === "today",
    JSON.stringify(dispatchBody));
  check("the today cron watches the today report",
    freshnessUrl.includes("/reports/scan-summary-today.json"), freshnessUrl);
  check("it sends the token as a bearer header",
    calls[0].options.headers.Authorization === "Bearer t");
}
{
  const { dispatchBody, freshnessUrl } = await happyTick(TARGETED_CRON);
  check("the targeted cron sends ref=main and mode=upcoming-targeted",
    dispatchBody && dispatchBody.ref === "main"
    && dispatchBody.inputs.mode === "upcoming-targeted",
    JSON.stringify(dispatchBody));
  check("the targeted cron watches the targeted report, not today's",
    freshnessUrl.includes("/reports/scan-summary-upcoming-targeted.json"),
    freshnessUrl);
}

// --- an unrecognised cron presses nothing ---------------------------------
for (const unknown of ["*/1 * * * *", "0 0 * * *", "", undefined]) {
  const { calls, spoken } = await say(
    { GITHUB_DISPATCH_TOKEN: "t", TELEGRAM_BOT_TOKEN: "b", TELEGRAM_CHAT_ID: "c" },
    () => json({}),
    unknown,
  );
  const dispatched = calls.filter((c) => c.url.includes("/dispatches"));
  const alerted = calls.filter((c) => c.url.includes("api.telegram.org"));
  check(`an unknown cron ${JSON.stringify(unknown)} dispatches nothing`,
    dispatched.length === 0, JSON.stringify(dispatched.map((c) => c.url)));
  check(`an unknown cron ${JSON.stringify(unknown)} says so and alerts`,
    spoken.includes("UNKNOWN CRON") && alerted.length === 1, spoken.slice(0, 200));
}

// --- only 204 is accepted, for BOTH modes ---------------------------------
for (const cron of [TODAY_CRON, TARGETED_CRON]) {
  for (const status of [200, 202, 301, 401, 403, 404, 422, 500]) {
    let alerted = false;
    await tick(
      { GITHUB_DISPATCH_TOKEN: "t", TELEGRAM_BOT_TOKEN: "b", TELEGRAM_CHAT_ID: "c" },
      (url) => {
        if (url.includes("/dispatches")) return new Response("no", { status });
        if (url.includes("api.telegram.org")) { alerted = true; return json({}); }
        return json({ last_scan: new Date().toISOString() });
      },
      cron,
      // A minute the throttle always allows, so this case measures the status
      // rule and not the throttle.
      Date.UTC(2026, 8, 6, 10, cron === TODAY_CRON ? 3 : 1),
    );
    check(`${cron === TODAY_CRON ? "today" : "targeted"}: HTTP ${status} is a `
      + "failure and alerts", alerted);
  }
}

// --- silence: the summary stops moving ------------------------------------
{
  // 20 minutes: fresh for today (60 min limit), stale for targeted (15 min).
  const stamp = new Date(Date.now() - 20 * 60 * 1000).toISOString();
  const run = async (cron, minute) => {
    let alertText = null;
    await tick(
      { GITHUB_DISPATCH_TOKEN: "t", TELEGRAM_BOT_TOKEN: "b", TELEGRAM_CHAT_ID: "c" },
      (url, options) => {
        if (url.includes("/dispatches")) return new Response(null, { status: 204 });
        if (url.includes("/runs")) return json({ workflow_runs: [] });
        if (url.includes("api.telegram.org")) {
          alertText = JSON.parse(options.body).text;
          return json({});
        }
        return json({ last_scan: stamp });
      },
      cron,
      Date.UTC(2026, 8, 6, 10, minute),
    );
    return alertText;
  };
  check("a 20 minute old targeted report is silence and alerts",
    (await run(TARGETED_CRON, 1) || "").includes("last upcoming-targeted scan"));
  check("a 20 minute old today report is still within cadence and does not",
    (await run(TODAY_CRON, 3)) === null);
}
{
  const old = new Date(Date.now() - 5 * 3600 * 1000).toISOString();
  let alertText = "";
  await tick(
    { GITHUB_DISPATCH_TOKEN: "t", TELEGRAM_BOT_TOKEN: "b", TELEGRAM_CHAT_ID: "c" },
    (url, options) => {
      if (url.includes("/dispatches")) return new Response(null, { status: 204 });
      if (url.includes("/runs")) return json({ workflow_runs: [] });
      if (url.includes("api.telegram.org")) {
        alertText = JSON.parse(options.body).text;
        return json({});
      }
      return json({ last_scan: old, mode: "today" });
    },
  );
  check("a stale published summary raises an alert even though the dispatch "
    + "was accepted", alertText.includes("last today scan"), alertText);
}
for (const cron of [TODAY_CRON, TARGETED_CRON]) {
  let alerted = false;
  await tick(
    { GITHUB_DISPATCH_TOKEN: "t", TELEGRAM_BOT_TOKEN: "b", TELEGRAM_CHAT_ID: "c" },
    (url) => {
      if (url.includes("/dispatches")) return new Response(null, { status: 204 });
      if (url.includes("/runs")) return json({ workflow_runs: [] });
      if (url.includes("api.telegram.org")) { alerted = true; return json({}); }
      return freshFor(cron === TODAY_CRON ? "today" : "upcoming-targeted");
    },
    cron,
  );
  check(`${cron === TODAY_CRON ? "today" : "targeted"}: a fresh summary and an `
    + "accepted dispatch raise nothing", !alerted);
}

// --- the throttle: quieter phone, same log --------------------------------
{
  const count = async (cron, minute) => {
    let sent = 0;
    const { spoken } = await say(
      { GITHUB_DISPATCH_TOKEN: "t", TELEGRAM_BOT_TOKEN: "b", TELEGRAM_CHAT_ID: "c" },
      (url) => {
        if (url.includes("/dispatches")) return new Response("no", { status: 500 });
        if (url.includes("api.telegram.org")) { sent += 1; return json({}); }
        return json({ last_scan: new Date().toISOString() });
      },
      cron,
      Date.UTC(2026, 8, 6, 10, minute),
    );
    return { sent, spoken };
  };
  const targetedMinutes = minutesOf(TARGETED_CRON);
  let sentInAnHour = 0;
  for (const minute of targetedMinutes) sentInAnHour += (await count(TARGETED_CRON, minute)).sent;
  check("a permanent targeted fault sends 2 messages an hour, not 12",
    sentInAnHour === 2, String(sentInAnHour));
  const throttled = await count(TARGETED_CRON, 11);
  check("a throttled tick still writes the whole report to the worker log",
    throttled.sent === 0 && throttled.spoken.includes("DISPATCH FAILED")
    && throttled.spoken.includes("telegram throttled"), throttled.spoken.slice(0, 200));
  let todaySent = 0;
  for (const minute of minutesOf(TODAY_CRON)) todaySent += (await count(TODAY_CRON, minute)).sent;
  check("today's every-tick alerting is preserved exactly", todaySent === 3,
    String(todaySent));
}

// --- the rule that keeps every later report trustworthy -------------------
{
  const older = new Date(Date.now() - 20 * 60 * 1000).toISOString();
  const { spoken } = await say({ GITHUB_DISPATCH_TOKEN: "t" }, (url) => {
    if (url.includes("/dispatches")) return new Response(null, { status: 204 });
    if (url.includes("/runs")) {
      return json({
        workflow_runs: [
          { id: 1, run_number: 1, event: "schedule", created_at: new Date().toISOString() },
          { id: 2, run_number: 2, event: "workflow_dispatch", created_at: older },
        ],
      });
    }
    return freshFor("today");
  });
  // Neither candidate qualifies: one is a schedule, the other predates the
  // tick. The Worker must SAY it identified nothing rather than claim either.
  check("a scheduled run and an older dispatch are both refused as 'ours'",
    spoken.includes("run not identified")
    && !spoken.includes("run #1") && !spoken.includes("run #2"),
    spoken);
}
{
  let asked = "";
  await tick({ GITHUB_DISPATCH_TOKEN: "t" }, (url) => {
    if (url.includes("/dispatches")) return new Response(null, { status: 204 });
    if (url.includes("/runs")) { asked = url; return json({ workflow_runs: [] }); }
    return freshFor("today");
  });
  check("the run lookup filters by event=workflow_dispatch and branch",
    asked.includes("event=workflow_dispatch") && asked.includes("branch=main"),
    asked);
}

// --- a missing token must not throw, and must not look like success -------
for (const cron of [TODAY_CRON, TARGETED_CRON]) {
  const { calls, spoken } = await say(
    { TELEGRAM_BOT_TOKEN: "b", TELEGRAM_CHAT_ID: "c" },
    (url) => url.includes("api.telegram.org") ? json({}) : freshFor("today"),
    cron,
    Date.UTC(2026, 8, 6, 10, cron === TODAY_CRON ? 3 : 1),
  );
  check(`${cron === TODAY_CRON ? "today" : "targeted"}: a missing `
    + "GITHUB_DISPATCH_TOKEN alerts instead of failing silently",
    calls.some((c) => c.url.includes("api.telegram.org"))
    && spoken.includes("GITHUB_DISPATCH_TOKEN is not set"), spoken.slice(0, 160));
  check(`${cron === TODAY_CRON ? "today" : "targeted"}: no dispatch is `
    + "attempted without a token",
    !calls.some((c) => c.url.includes("/dispatches")));
}

// --- an unreachable GitHub must not take the Worker down ------------------
for (const cron of [TODAY_CRON, TARGETED_CRON]) {
  let alerted = false;
  await tick(
    { GITHUB_DISPATCH_TOKEN: "t", TELEGRAM_BOT_TOKEN: "b", TELEGRAM_CHAT_ID: "c" },
    (url) => {
      if (url.includes("/dispatches")) throw new Error("network down");
      if (url.includes("api.telegram.org")) { alerted = true; return json({}); }
      return freshFor("today");
    },
    cron,
    Date.UTC(2026, 8, 6, 10, cron === TODAY_CRON ? 3 : 1),
  );
  check(`${cron === TODAY_CRON ? "today" : "targeted"}: a thrown fetch is `
    + "caught and alerted, not propagated", alerted);
}
{
  const { spoken } = await say({ GITHUB_DISPATCH_TOKEN: "t" }, (url) => {
    if (url.includes("/dispatches")) return new Response(null, { status: 204 });
    if (url.includes("/runs")) return json({ workflow_runs: [] });
    throw new Error("raw.githubusercontent unreachable");
  }, TARGETED_CRON);
  check("an unreadable freshness report is reported, not guessed at",
    spoken.includes("freshness unreadable"), spoken.slice(0, 200));
}

// --- the alert channel itself failing must not take the tick down ---------
for (const outcome of ["throw", "500", "200-not-ok"]) {
  let survived = false;
  const { spoken } = await say(
    { GITHUB_DISPATCH_TOKEN: "t", TELEGRAM_BOT_TOKEN: "b", TELEGRAM_CHAT_ID: "c" },
    (url) => {
      if (url.includes("/dispatches")) return new Response("no", { status: 500 });
      if (url.includes("api.telegram.org")) {
        if (outcome === "throw") throw new Error("telegram unreachable");
        if (outcome === "500") return new Response("nope", { status: 500 });
        return json({ ok: false, description: "chat not found" });
      }
      survived = true;
      return freshFor("today");
    },
  );
  check(`a failing telegram (${outcome}) is logged and the tick completes`,
    survived && spoken.includes("DISPATCH FAILED"), spoken.slice(0, 160));
}
{
  // No Telegram secrets at all: the Worker must still run to the end.
  const { spoken, calls } = await say({ GITHUB_DISPATCH_TOKEN: "t" }, (url) => {
    if (url.includes("/dispatches")) return new Response("no", { status: 500 });
    return freshFor("upcoming-targeted");
  }, TARGETED_CRON, Date.UTC(2026, 8, 6, 10, 1));
  check("with no telegram secrets the report still reaches the worker log",
    spoken.includes("DISPATCH FAILED")
    && !calls.some((c) => c.url.includes("api.telegram.org")), spoken.slice(0, 160));
}

console.log(failures === 0
  ? "\nscan-trigger worker: all checks passed"
  : `\nscan-trigger worker: ${failures} check(s) FAILED`);
process.exit(failures === 0 ? 0 : 1);
