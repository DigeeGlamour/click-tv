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
 * On each of its crons it calls the repository's existing workflow through
 * `workflow_dispatch` with the mode that cron is responsible for. That is the
 * whole job.
 *
 * WHAT IT DELIBERATELY DOES NOT DO
 *   - no KV, no D1, no Durable Object, no storage of any kind
 *   - no control plane, no /api/events/*, no data path
 *   - no scanner, state, report or frontend logic
 *   - no `fetch` handler: with `workers_dev = false` and no route, nothing
 *     about this Worker is reachable from the internet
 * The scanner, its state and its reports are untouched. This Worker only
 * presses the buttons that GitHub's own scheduler presses unreliably.
 *
 * IT IS A SECOND PATH, NOT A REPLACEMENT
 * The repository's native crons stay declared. GitHub's 6.7% is a poor floor
 * but it is not zero, and two independent schedulers must both fail before the
 * data goes stale. An externally dispatched run lands in the SAME concurrency
 * group as its native cron, because scan.yml computes the group from
 * `inputs.mode` as well as `github.event.schedule`:
 *
 *   today             -> live-signal-events-v4    cancel-in-progress false
 *   upcoming-targeted -> live-signal-targeted-v1  cancel-in-progress true
 *
 * So when both schedulers fire, GitHub holds one behind the other (today) or
 * cancels the older one (targeted). That is the duplicate guard, and it
 * already existed before this Worker. No new lock is invented here.
 *
 * CADENCE
 * Every cron below is the repository's own declared cadence for that mode,
 * minute for minute. Firing on the same minutes means a duplicate collapses in
 * the concurrency group instead of doubling the scan rate; no new cadence is
 * invented here either.
 */

const OWNER = "DigeeGlamour";
const REPO = "click-tv";
const WORKFLOW_FILE = "scan.yml";
const REF = "main";

/**
 * Which cron means which mode.
 *
 * The keys are the exact cron strings from wrangler.toml, which are in turn the
 * exact strings from `.github/workflows/scan.yml`. `controller.cron` hands back
 * the string Cloudflare matched, so this is a lookup and never a guess: a cron
 * that is not a key here dispatches NOTHING, because a scheduler that guesses a
 * mode can write the wrong data under it.
 *
 *   cadenceMinutes    the declared cadence, used only to size the silence watch
 *   reports           the published summaries that prove this mode ran; the
 *                     NEWEST of them is the freshness figure
 *   telegramEveryTick whether every bad tick may send a Telegram message
 *
 * `upcoming-targeted` needs two reports, and finding that out cost a wrong
 * alert on the first live tick. scanner/output.py aliases `upcoming-targeted`
 * to `upcoming` before naming its report, so a targeted run that actually
 * chases a link writes `scan-summary-upcoming.json`. The literal
 * `scan-summary-upcoming-targeted.json` is written only by scan.py's two early
 * exits - "nothing is inside the window" and "output preserved". Watching just
 * the second one means reporting silence exactly when targeted is working,
 * which is worse than not watching at all. So both are read and the newer
 * wins: every targeted outcome moves exactly one of them.
 *
 * The twice-daily `upcoming` full refresh also writes the second file, so it
 * can mask a targeted outage - for fifteen minutes, twice a day. That is the
 * honest cost of the only signal the scanner publishes, and it is a far
 * smaller hole than the one it closes.
 *
 * `telegramEveryTick` is false for the five-minute mode and true for the
 * twenty-minute one. That is not a preference: a permanent fault on a
 * five-minute cron is twelve identical messages an hour, which is how a bot
 * gets muted, and a muted bot is the same silence this Worker exists to break.
 * Throttled ticks still write the full report to the Cloudflare log — nothing
 * is hidden, only the phone is spared. Today keeps its original every-tick
 * behaviour untouched.
 */
const SCHEDULES = {
  "3,23,43 * * * *": {
    mode: "today",
    cadenceMinutes: 20,
    reports: ["scan-summary-today.json"],
    telegramEveryTick: true,
  },
  "1-59/5 * * * *": {
    mode: "upcoming-targeted",
    cadenceMinutes: 5,
    reports: [
      "scan-summary-upcoming.json",
      "scan-summary-upcoming-targeted.json",
    ],
    telegramEveryTick: false,
  },
};

/**
 * How old a published summary may get before it is called silence, as a
 * multiple of that mode's own cadence. Three cadences: one missed trigger is
 * normal on any scheduler, three in a row is not. today -> 60 min,
 * upcoming-targeted -> 15 min.
 */
const STALE_AFTER_CADENCES = 3;

/** At most one throttled Telegram message per this many minutes. */
const THROTTLE_WINDOW_MINUTES = 30;

const USER_AGENT = "click-tv-scan-trigger";

export default {
  /**
   * Cloudflare gives a scheduled handler a short budget; the work is handed to
   * waitUntil so a slow GitHub response cannot truncate the alert path.
   */
  async scheduled(event, env, ctx) {
    ctx.waitUntil(tick(env, event.cron, event.scheduledTime));
  },
};

async function tick(env, cron, scheduledTime) {
  const firedAtMs = scheduledTime || Date.now();
  const firedAt = new Date(firedAtMs).toISOString();

  const job = SCHEDULES[cron];
  if (!job) {
    // A trigger this Worker has no mode for. Dispatch nothing: the workflow's
    // own selector fails loudly on an unrecognised schedule for exactly this
    // reason, and a dispatch with a guessed mode would defeat it by arriving
    // as a perfectly well-formed request for the wrong scan.
    const known = Object.keys(SCHEDULES)
      .map((value) => JSON.stringify(value))
      .join(", ");
    const message =
      `click-tv scan-trigger ${firedAt}\n` +
      `UNKNOWN CRON ${JSON.stringify(cron)} - no dispatch was sent.\n` +
      `This Worker only knows: ${known}.\n` +
      `A trigger was added to wrangler.toml without adding it to SCHEDULES in ` +
      `src/index.js, or removed from SCHEDULES without removing the trigger.`;
    console.error(message);
    await alert(env, `⚠️ ${message}`);
    return;
  }

  const notes = [];

  const dispatched = await dispatch(env, job.mode);
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
  // stops moving. Each mode watches its OWN published summary, because the
  // targeted report going stale while today kept writing is precisely the
  // fault that went unnoticed for 115 hours.
  const staleAfter = job.cadenceMinutes * STALE_AFTER_CADENCES;
  const stale = await staleness(job.reports);
  if (stale.ageMinutes === null) {
    notes.push(`freshness unreadable: ${stale.error}`);
  } else {
    notes.push(
      `last ${job.mode} scan ${stale.ageMinutes} min ago ` +
      `(stale after ${staleAfter} min, from ${stale.report})`
    );
  }

  const bad = !dispatched.ok ||
    (stale.ageMinutes !== null && stale.ageMinutes > staleAfter);

  const summary = `click-tv scan-trigger ${firedAt}\nmode=${job.mode}\n` +
    notes.map((line) => `- ${line}`).join("\n");

  if (!bad) {
    console.log(summary);
    return;
  }

  // Always log; message the phone only when this tick is allowed to.
  console.error(summary);
  if (mayMessage(job, firedAtMs)) {
    await alert(env, `⚠️ ${summary}`);
  } else {
    console.error(
      `telegram throttled: ${job.mode} sends at most one message per ` +
      `${THROTTLE_WINDOW_MINUTES} min; the report above stands.`
    );
  }
}

/**
 * Stateless throttle. This Worker stores nothing, so "have I already sent one?"
 * cannot be remembered — it is derived from the clock instead: only the first
 * tick of each throttle window may message. With a five-minute cadence that is
 * the tick at :01 and the one at :31, so at most two messages an hour, and a
 * fault that persists is still reported twice an hour forever.
 */
function mayMessage(job, firedAtMs) {
  if (job.telegramEveryTick) return true;
  const minute = new Date(firedAtMs).getUTCMinutes();
  return (minute % THROTTLE_WINDOW_MINUTES) < job.cadenceMinutes;
}

/**
 * POST the dispatch. The token needs one repository permission and no more:
 * Actions read and write. It never touches contents — the workflow's own
 * GITHUB_TOKEN does the committing.
 */
async function dispatch(env, mode) {
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
      body: JSON.stringify({ ref: REF, inputs: { mode } }),
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
 *
 * The dispatches endpoint returns no run id, and the runs list does not carry
 * `inputs`, so the mode cannot be confirmed from the API either. What keeps
 * the two modes from being mistaken for each other is the clock: the today
 * minutes (3, 23, 43) and the targeted minutes (1, 6, 11 ... 56) are never
 * closer than two minutes apart, and the backward slack below is one. The test
 * suite asserts that separation, so if a cron is ever changed in a way that
 * breaks it, the tests fail before the reports start lying.
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

/**
 * How long ago this mode last published, taken from the NEWEST of its reports.
 *
 * A mode whose outcomes are written to different files is fresh if any of them
 * is fresh, so one unreadable report never manufactures silence on its own -
 * it is only reported when no report could be read at all.
 */
async function staleness(reports) {
  const errors = [];
  let newest = null;
  let from = "";
  for (const report of reports) {
    const one = await readReport(report);
    if (one.stamp === null) {
      errors.push(`${report}: ${one.error}`);
      continue;
    }
    if (newest === null || one.stamp > newest) {
      newest = one.stamp;
      from = report;
    }
  }
  if (newest === null) {
    return { ageMinutes: null, report: "", error: errors.join("; ") };
  }
  return {
    ageMinutes: Math.round((Date.now() - newest) / 60000),
    report: from,
    error: errors.join("; "),
  };
}

/** One published summary's `last_scan`, as epoch milliseconds. */
async function readReport(report) {
  const url =
    `https://raw.githubusercontent.com/${OWNER}/${REPO}/${REF}` +
    `/reports/${report}`;
  try {
    const response = await fetch(`${url}?t=${Date.now()}`, {
      headers: { "User-Agent": USER_AGENT, "Cache-Control": "no-cache" },
    });
    if (!response.ok) {
      return { stamp: null, error: `HTTP ${response.status}` };
    }
    const body = await response.json();
    const stamp = Date.parse(body.last_scan);
    if (!stamp) return { stamp: null, error: "no readable last_scan" };
    return { stamp, error: "" };
  } catch (error) {
    return { stamp: null, error: String(error).slice(0, 200) };
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
    const response = await fetch(
      `https://api.telegram.org/bot${token}/sendMessage`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "User-Agent": USER_AGENT },
        body: JSON.stringify({
          chat_id: chat,
          text,
          disable_web_page_preview: true,
        }),
      },
    );
    // A failure here cannot itself be alerted about — the alert channel is
    // what failed — so it is recorded in the Worker log, and it must never
    // take the tick down.
    if (!response.ok) {
      console.error(`telegram alert rejected: HTTP ${response.status}`);
    }
  } catch (error) {
    console.error(`telegram alert failed: ${error}`);
  }
}
