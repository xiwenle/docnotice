/**
 * Close the Pages a round explicitly marked as disposable, and nothing else.
 *
 * The TaskSpace allows only 8 managed Pages for the whole run, so a round that
 * opens throw-away Pages (a fresh session, a disposable probe) must release them
 * or every later round fails with PageBudgetError.
 *
 * Nothing is guessed here: a previous version closed every Page that was not on a
 * hard-coded keep-list, which silently closed Pages shared by later rounds
 * (rounds name the same persona differently — `user`, `user_a`, `user-a` — so a
 * name-based keep-list cannot be correct). A round now records the Page labels it
 * wants discarded in `<evidence>/ego/results/<round>/disposable-pages.json`, and
 * only those are closed.
 *
 * Usage: `ego-browser nodejs -e "await import('file:///<this file>')"`.
 * `__FULL_MATRIX_ROUND_ID__` is substituted by the runner before the script is
 * invoked: `ego-browser nodejs` does not forward environment variables, so
 * reading `process.env.FULL_MATRIX_ROUND_ID` silently yielded an empty round id
 * and no Page was ever recycled.
 */
const TASK_SPACE = "__FULL_MATRIX_TASK_SPACE__";
const EVIDENCE = "__FULL_MATRIX_EVIDENCE_ROOT__";
const ROUND_ID = "__FULL_MATRIX_ROUND_ID__";

const fs = await import("node:fs/promises");
const path = await import("node:path");

const task = await taskSpace(TASK_SPACE);
const report = { round: ROUND_ID, closed: [], skipped: [], failed: [] };

if (!ROUND_ID) {
  report.skipped.push({ reason: "no round id supplied" });
} else {
  const manifest = path.join(EVIDENCE, "ego", "results", ROUND_ID, "disposable-pages.json");
  let wanted = [];
  try {
    const parsed = JSON.parse(await fs.readFile(manifest, "utf8"));
    wanted = Array.isArray(parsed) ? parsed : (parsed.labels || []);
  } catch (_) {
    report.skipped.push({ reason: "round declared no disposable pages" });
  }
  // `disposePages` receives the logical labels rounds pass to makePage ("admin",
  // "user-a"), but the runtime names the actual Page p1..p8. `makePage` records
  // the mapping in page-usage.json; without translating through it every label
  // missed and recycling silently closed nothing.
  let usage = {};
  try {
    usage = JSON.parse(await fs.readFile(path.join(EVIDENCE, "ego", "results", "page-usage.json"), "utf8"));
  } catch (_) {
    report.skipped.push({ reason: "page-usage.json unavailable; cannot map logical labels" });
  }
  const realLabel = logical => usage[logical] || logical;
  if (wanted.length) {
    const pages = await task.pages();
    for (const slot of wanted) {
      const target = realLabel(slot);
      const page = pages.find(p => p.label === target);
      if (!page) { report.skipped.push(slot); continue; }
      try {
        await page.close();
        report.closed.push(slot);
        // Forget the mapping too.  page-usage.json is shared across the whole
        // run, so a stale entry would make the next round resolve "admin" to a
        // Page that no longer exists — makePage would then hand back a dead
        // handle and fail deep inside an event callback.
        delete usage[slot];
      } catch (_) {
        report.failed.push(slot);
      }
    }
    try {
      await fs.writeFile(
        path.join(EVIDENCE, "ego", "results", "page-usage.json"),
        JSON.stringify(usage, null, 2),
        "utf8",
      );
    } catch (_) {
      report.skipped.push({ reason: "could not rewrite page-usage.json" });
    }
  }
}

report.remaining = (await task.pages()).map(p => p.label);
console.log(JSON.stringify(report));
