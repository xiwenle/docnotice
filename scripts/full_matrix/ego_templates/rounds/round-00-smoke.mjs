// round-00-smoke: DNS, health, page reachability, and raw CDP event smoke.
// This round intentionally never calls task.finish().

import {
  API, ORIGINS, OUT, PORT, RUN_ID, apiJson, caseRow, disposePages, drainEvents, ensureDir, makePage, now, pageState, recordCase, screenshot, summarize, writeJson, writeResult,
} from "../lib/core.mjs";

const cases = [];
const pages = [];
const started = Date.now();

async function main() {
  const smoke = await makePage("smoke");
  pages.push({ label: "smoke", target: "anon" });
  const results = {};
  for (const [name, origin] of Object.entries(ORIGINS)) {
    const pageResult = { origin, health: null, state: null, error: null };
    try {
      // about:blank has no same-origin context for window.fetch; navigate first.
      // The resulting document state and URL are the ground truth for host reachability.
      await smoke.page.goto(`${origin}/login`);
      await smoke.page.waitForLoadState();
      pageResult.state = await pageState(smoke.page);
      const response = await smoke.page.fetch("/health");
      pageResult.health = { status: response.status };
    } catch (error) {
      pageResult.error = String(error);
    }
    results[name] = pageResult;
    const ok = pageResult.health?.status === 200 && pageResult.state?.host === new URL(origin).host;
    recordCase(cases, caseRow({
      id: `EGO-SMOKE-${name.toUpperCase()}`,
      phase: "round-00-smoke",
      owner: "F",
      role: name === "anon" ? "anon" : "system",
      mutability: "READ_SHARED",
      precondition: "sandbox uvicorn is running on 127.0.0.1 and hosts resolve to loopback",
      expected: ["/health returns 200", "login page loads on the requested host origin", "no unexpected CDP error"],
      actual: pageResult,
      status: ok ? "PASS" : "FAIL",
      severity: ok ? "NONE" : "P0",
      evidence: [{ kind: "json", path: `${OUT}/ego/results/round-00-smoke/smoke.json` }],
      repro: { command: "python -m scripts.full_matrix.run ego round-00-smoke" },
      duration_ms: 0,
    }));
  }

  await smoke.page.goto(`${ORIGINS.anon}/login`);
  await smoke.page.waitForLoadState();
  const dom = await smoke.page.evaluate(() => ({
    loginForm: !!document.querySelector("#login-form"),
    username: !!document.querySelector("#username"),
    password: !!document.querySelector("#password"),
    title: document.title,
    host: location.host,
  }));
  const events = await drainEvents(smoke.page);
  recordCase(cases, caseRow({
    id: "EGO-SMOKE-DOM",
    phase: "round-00-smoke",
    owner: "F",
    role: "anon",
    mutability: "READ_SHARED",
    precondition: "anonymous host is reachable",
    expected: ["login form and credentials inputs exist", "page uses anon.localhost"],
    actual: dom,
    status: dom.loginForm && dom.username && dom.password && dom.host.startsWith("anon.localhost") ? "PASS" : "FAIL",
    severity: "P1",
    evidence: [{ kind: "screenshot", path: await screenshot(smoke.page, "smoke-login-390.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-00-smoke" },
    duration_ms: 0,
  }));
  const eventOk = events.consoleErrors.length === 0 && events.pageErrors.length === 0;
  recordCase(cases, caseRow({
    id: "EGO-SMOKE-EVENTS",
    phase: "round-00-smoke",
    owner: "F",
    role: "anon",
    mutability: "READ_SHARED",
    precondition: "Runtime/Log/Network CDP domains enabled",
    expected: ["no console error", "no pageerror", "no unexpected 5xx"],
    actual: events,
    status: eventOk ? "PASS" : "FAIL",
    severity: "P1",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/round-00-smoke/events.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-00-smoke" },
    duration_ms: 0,
  }));
  await ensureDir(`${OUT}/ego/results/round-00-smoke`);
  await writeJson(`${OUT}/ego/results/round-00-smoke/smoke.json`, { runId: RUN_ID, results, events, dom, generatedAt: now() });
  await writeJson(`${OUT}/ego/results/round-00-smoke/pages.json`, pages);
}

try {
  await main();
  await disposePages("round-00-smoke", ["smoke"]);
  await writeResult("round-00-smoke", cases, pages, { durationMs: Date.now() - started });
} catch (error) {
  recordCase(cases, caseRow({
    id: "EGO-SMOKE-RUNNER",
    phase: "round-00-smoke",
    owner: "F",
    role: "system",
    mutability: "READ_SHARED",
    precondition: "ego runtime",
    expected: ["smoke round completes"],
    actual: { error: String(error), stack: error?.stack || null },
    status: "ERROR",
    severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-00-smoke" },
  }));
  await disposePages("round-00-smoke", ["smoke"]);
  await writeResult("round-00-smoke", cases, pages, { durationMs: Date.now() - started });
  process.exitCode = 1;
}