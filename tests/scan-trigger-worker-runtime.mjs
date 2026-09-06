/**
 * Runtime test for workers/scan-trigger — the scheduler Worker.
 *
 * Run: node tests/scan-trigger-worker-runtime.mjs
 *
 * The Worker's whole job is to press a button and then be honest about what
 * happened. The three things worth proving are exactly the three that are easy
 * to get wrong:
 *
 *   1. only HTTP 204 counts as accepted - GitHub documents 204 for this
 *      endpoint, and treating any 2xx as success would hide a redirect or a
 *      202 that means something else;
 *   2. a dispatch that was accepted is still not a run - the endpoint returns
 *      no run id, so the Worker must never report one it did not verify;
 *   3. a run belonging to somebody else is never claimed as ours.
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
check("no [vars] block: every input is a secret",
  !/^\s*\[vars\]/m.test(toml));

// -------------------------------------------------------------- cadence ----
const workerCrons = [...toml.matchAll(/"([^"]+)"/g)]
  .map((m) => m[1])
  .filter((value) => /^[\d,*/\- ]+$/.test(value) && value.split(" ").length === 5);
check("the worker schedules exactly one cron", workerCrons.length === 1,
  JSON.stringify(workerCrons));
check("it is the repository's own declared Today cadence",
  workerCrons[0] === "3,23,43 * * * *" && workflow.includes('"3,23,43 * * * *"'),
  workerCrons[0]);
check("the targeted cron is NOT scheduled by the worker (pilot is today only)",
  !toml.includes("1-59/5"));
check("the dispatched mode is today",
  /const\s+MODE\s*=\s*"today"/.test(source));

// -------------------------------------------------------------- secrets ----
check("no literal token of any shape is in the worker source",
  !/(github_pat_|ghp_|gho_)[A-Za-z0-9_]{10,}/.test(source + toml));
check("the github token is read from env, not embedded",
  /env\.GITHUB_DISPATCH_TOKEN/.test(source));
check("it reuses the workflow's own Telegram secret names",
  /env\.TELEGRAM_BOT_TOKEN/.test(source) && /env\.TELEGRAM_CHAT_ID/.test(source)
  && workflow.includes("TELEGRAM_BOT_TOKEN") && workflow.includes("TELEGRAM_CHAT_ID"));

// ------------------------------------------------------------- behaviour ---
const worker = (await import("file://" + WORKER.replace(/\\/g, "/"))).default;

/** Run one scheduled tick with `fetch` replaced; return every call it made. */
async function tick(env, handler) {
  const calls = [];
  const real = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    return handler(String(url), options, calls.length);
  };
  const waits = [];
  try {
    await worker.scheduled(
      { scheduledTime: Date.now() },
      env,
      { waitUntil: (promise) => waits.push(promise) },
    );
    await Promise.all(waits);
  } finally {
    globalThis.fetch = real;
  }
  return calls;
}

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status });
const fresh = () =>
  json({ last_scan: new Date().toISOString(), mode: "today" });

// 204 is the accepted answer, and the mode travels with it.
{
  let dispatchBody = null;
  const calls = await tick({ GITHUB_DISPATCH_TOKEN: "t" }, (url, options) => {
    if (url.includes("/dispatches")) {
      dispatchBody = JSON.parse(options.body);
      return new Response(null, { status: 204 });
    }
    if (url.includes("/runs")) return json({ workflow_runs: [] });
    return fresh();
  });
  check("it POSTs to the workflow's dispatches endpoint",
    calls.some((c) => c.url.endsWith("/actions/workflows/scan.yml/dispatches")
      && c.options.method === "POST"));
  check("it sends ref=main and the explicit mode",
    dispatchBody && dispatchBody.ref === "main"
    && dispatchBody.inputs.mode === "today",
    JSON.stringify(dispatchBody));
  check("it sends the token as a bearer header",
    calls[0].options.headers.Authorization === "Bearer t");
}

// A non-204 answer is a failure, and it must alert rather than pass quietly.
for (const status of [200, 202, 401, 403, 404, 422, 500]) {
  let alerted = false;
  await tick(
    { GITHUB_DISPATCH_TOKEN: "t", TELEGRAM_BOT_TOKEN: "b", TELEGRAM_CHAT_ID: "c" },
    (url) => {
      if (url.includes("/dispatches")) return new Response("no", { status });
      if (url.includes("api.telegram.org")) { alerted = true; return json({}); }
      return fresh();
    },
  );
  check(`HTTP ${status} from the dispatch is treated as a failure and alerts`,
    alerted);
}

// Silence: the summary stops moving, and nothing else can notice.
{
  let alertText = "";
  const old = new Date(Date.now() - 5 * 3600 * 1000).toISOString();
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
{
  let alerted = false;
  await tick(
    { GITHUB_DISPATCH_TOKEN: "t", TELEGRAM_BOT_TOKEN: "b", TELEGRAM_CHAT_ID: "c" },
    (url) => {
      if (url.includes("/dispatches")) return new Response(null, { status: 204 });
      if (url.includes("/runs")) return json({ workflow_runs: [] });
      if (url.includes("api.telegram.org")) { alerted = true; return json({}); }
      return fresh();
    },
  );
  check("a fresh summary and an accepted dispatch raise nothing", !alerted);
}

// The rule that keeps every later report trustworthy.
{
  const older = new Date(Date.now() - 20 * 60 * 1000).toISOString();
  const said = [];
  const realLog = console.log;
  console.log = (...args) => said.push(args.join(" "));
  await tick({ GITHUB_DISPATCH_TOKEN: "t" }, (url) => {
    if (url.includes("/dispatches")) return new Response(null, { status: 204 });
    if (url.includes("/runs")) {
      return json({
        workflow_runs: [
          { id: 1, run_number: 1, event: "schedule", created_at: new Date().toISOString() },
          { id: 2, run_number: 2, event: "workflow_dispatch", created_at: older },
        ],
      });
    }
    return fresh();
  });
  console.log = realLog;
  // Neither candidate qualifies: one is a schedule, the other predates the
  // tick. The Worker must SAY it identified nothing rather than claim either.
  const spoken = said.join("\n");
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
    return fresh();
  });
  check("the run lookup filters by event=workflow_dispatch and branch",
    asked.includes("event=workflow_dispatch") && asked.includes("branch=main"),
    asked);
}

// A missing token must not throw, and must not look like success.
{
  let alerted = false;
  await tick({ TELEGRAM_BOT_TOKEN: "b", TELEGRAM_CHAT_ID: "c" }, (url) => {
    if (url.includes("api.telegram.org")) { alerted = true; return json({}); }
    return fresh();
  });
  check("a missing GITHUB_DISPATCH_TOKEN alerts instead of failing silently",
    alerted);
}

// An unreachable GitHub must not take the Worker down.
{
  let alerted = false;
  await tick(
    { GITHUB_DISPATCH_TOKEN: "t", TELEGRAM_BOT_TOKEN: "b", TELEGRAM_CHAT_ID: "c" },
    (url) => {
      if (url.includes("/dispatches")) throw new Error("network down");
      if (url.includes("api.telegram.org")) { alerted = true; return json({}); }
      return fresh();
    },
  );
  check("a thrown fetch is caught and alerted, not propagated", alerted);
}

console.log(failures === 0
  ? "\nscan-trigger worker: all checks passed"
  : `\nscan-trigger worker: ${failures} check(s) FAILED`);
process.exit(failures === 0 ? 0 : 1);
