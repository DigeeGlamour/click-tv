/**
 * Click TV scan trigger — a scheduler, and nothing else.
 *
 * WHY THIS EXISTS
 * GitHub's own `schedule` event is best effort, and on this repository it was
 * measured at 6.7% delivery over 294 hours: the crons asked for ~4,479 runs and
 * GitHub created 300, with an average gap of 59 minutes against a cron that
 * asks for one every five. In one three-hour window, 45 of 45 expected slots
 * produced no workflow run object at all. That is why an outside scheduler is
 * wanted — not because anything in the workflow is wrong.
 *
 * WHAT IT DOES
 * On its own cron it calls the repository's existing workflow through
 * `workflow_dispatch` with an explicit mode. That is the whole job.
 *
 * WHAT IT DELIBERATELY DOES NOT DO
 *   - no KV, no D1, no Durable Object, no storage of any kind
 *   - no control plane, no /api/events/*, no data path
 *   - no scanner, state, report or frontend logic
 *   - no `fetch` handler: with `workers_dev = false` and no route, nothing
 *     about this Worker is reachable from the internet
 * The scanner, its state and its reports are untouched. This Worker only
 * presses the button that GitHub's own scheduler presses unreliably.
 *
 * IT IS A SECOND PATH, NOT A REPLACEMENT
 * The repository's native crons stay declared. GitHub's 6.7% is a poor floor
 * but it is not zero, and two independent schedulers must both fail before the
 * data goes stale. An externally dispatched run lands in the SAME concurrency
 * group as its native cron — `live-signal-events-v4` for today — so when both
 * fire, GitHub runs one and drops the other. That is the duplicate guard, and
 * it already existed before this Worker.
 *
 * CADENCE
 * The cron in wrangler.toml is the repository's own declared Today cadence,
 * minute for minute. Firing on the same minutes means a duplicate collapses in
 * the concurrency group instead of doubling the scan rate; no new cadence is
 * invented here.
 */

const OWNER = "DigeeGlamour";
const REPO = "click-tv";
const WORKFLOW_FILE = "scan.yml";
const REF = "main";

/** The one mode this Worker is responsible for during the pilot. */
const MODE = "today";

/** The declared Today cadence, in minutes. Used only to size the watch below. */
const CADENCE_MINUTES = 20;

/**
 * How old the published Today summary may get before it is called silence.
 * Three cadences: one missed trigger is normal on any scheduler, three in a
 * row is not.
 */
const STALE_AFTER_MINUTES = CADENCE_MINUTES * 3;

const FRESHNESS_URL =
  `https://raw.githubusercontent.com/${OWNER}/${REPO}/${REF}` +
  `/reports/scan-summary-${MODE}.json`;

const USER_AGENT = "click-tv-scan-trigger";

export default {
  /**
   * Cloudflare gives a scheduled handler a short budget; the work is handed to
   * waitUntil so a slow GitHub response cannot truncate the alert path.
   */
  async scheduled(event, env, ctx) {
    ctx.waitUntil(tick(env, event.scheduledTime));
  },
};

async function tick(env, scheduledTime) {
  const firedAt = new Date(scheduledTime || Date.now()).toISOString();
  const notes = [];

  const dispatched = await dispatch(env);
  notes.push(
    dispatched.ok
      ? `dispatch accepted (HTTP ${dispatched.status})`
      : `DISPATCH FAILED (HTTP ${dispatched.status}) ${dispatched.detail}`
  );

  // A 204 says GitHub accepted the request. It does NOT say a run started, it
  // does not say the scan succeeded, and it carries no run id. Anything more
  // has to be looked up, and is reported as "not identified" when it cannot be.
  if (dispatched.ok) {
    const run = await findOurRun(env, firedAt);
    notes.push(
      run
        ? `run #${run.run_number} ${run.id} created ${run.created_at}`
        : "run not identified yet (this is normal within seconds of a dispatch)"
    );
  }

  // The check that catches the failure nobody else can see. A workflow that
  // never starts produces no failed run, so `if: failure()` inside the
  // workflow can never fire for it — the only symptom is a timestamp that
  // stops moving.
  const stale = await staleness();
  if (stale.error) {
    notes.push(`freshness unreadable: ${stale.error}`);
  } else {
    notes.push(`last ${MODE} scan ${stale.ageMinutes} min ago`);
  }

  const bad = !dispatched.ok || (stale.ageMinutes !== null &&
    stale.ageMinutes > STALE_AFTER_MINUTES);

  const summary = `click-tv scan-trigger ${firedAt}\nmode=${MODE}\n` +
    notes.map((line) => `- ${line}`).join("\n");

  if (bad) {
    console.error(summary);
    await alert(env, `⚠️ ${summary}`);
  } else {
    console.log(summary);
  }
}

/**
 * POST the dispatch. The token needs one repository permission and no more:
 * Actions read and write. It never touches contents — the workflow's own
 * GITHUB_TOKEN does the committing.
 */
async function dispatch(env) {
  const token = env.GITHUB_DISPATCH_TOKEN;
  if (!token) {
    return { ok: false, status: 0, detail: "GITHUB_DISPATCH_TOKEN is not set" };
  }
  const url =
    `https://api.github.com/repos/${OWNER}/${REPO}` +
    `/actions/workflows/${WORKFLOW_FILE}/dispatches`;
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: REF, inputs: { mode: MODE } }),
    });
    // 204 No Content is the documented success. Anything else is a failure,
    // including a 2xx that is not 204.
    if (response.status === 204) return { ok: true, status: 204, detail: "" };
    const detail = (await response.text().catch(() => "")).slice(0, 300);
    return { ok: false, status: response.status, detail };
  } catch (error) {
    return { ok: false, status: 0, detail: String(error).slice(0, 300) };
  }
}

/**
 * Best effort identification of the run this tick asked for.
 *
 * The rules are deliberately strict, because naming somebody else's run as
 * ours would make every later report untrustworthy:
 *   - it must be a workflow_dispatch, not a schedule
 *   - it must be on our ref
 *   - it must have been created at or after this tick fired
 * If nothing matches, this returns null and the caller says so.
 */
async function findOurRun(env, firedAt) {
  const token = env.GITHUB_DISPATCH_TOKEN;
  if (!token) return null;
  const url =
    `https://api.github.com/repos/${OWNER}/${REPO}` +
    `/actions/workflows/${WORKFLOW_FILE}/runs` +
    `?event=workflow_dispatch&branch=${REF}&per_page=5`;
  try {
    const response = await fetch(url, {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
      },
    });
    if (!response.ok) return null;
    const body = await response.json();
    const cutoff = Date.parse(firedAt) - 60_000; // one minute of clock slack
    for (const run of body.workflow_runs || []) {
      if (run.event !== "workflow_dispatch") continue;
      if (Date.parse(run.created_at) >= cutoff) return run;
    }
    return null;
  } catch (error) {
    return null;
  }
}

/** How long ago the published summary says the last scan of MODE finished. */
async function staleness() {
  try {
    const response = await fetch(`${FRESHNESS_URL}?t=${Date.now()}`, {
      headers: { "User-Agent": USER_AGENT, "Cache-Control": "no-cache" },
    });
    if (!response.ok) {
      return { ageMinutes: null, error: `HTTP ${response.status}` };
    }
    const body = await response.json();
    const stamp = Date.parse(body.last_scan);
    if (!stamp) return { ageMinutes: null, error: "no readable last_scan" };
    return {
      ageMinutes: Math.round((Date.now() - stamp) / 60000),
      error: "",
    };
  } catch (error) {
    return { ageMinutes: null, error: String(error).slice(0, 200) };
  }
}

/**
 * Same Telegram bot the workflow already uses, same secret names. Optional: if
 * the secrets are not set the Worker still runs and the message goes to the
 * Worker log instead, which is where it would have gone anyway.
 */
async function alert(env, text) {
  const token = env.TELEGRAM_BOT_TOKEN;
  const chat = env.TELEGRAM_CHAT_ID;
  if (!token || !chat) return;
  try {
    await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "User-Agent": USER_AGENT },
      body: JSON.stringify({
        chat_id: chat,
        text,
        disable_web_page_preview: true,
      }),
    });
  } catch (error) {
    console.error(`telegram alert failed: ${error}`);
  }
}
