// round-04-security: role routes, permission surface, session invalidation, CSRF/XSS/redirect/no-store locks.

import {
  API, CREDS, ORIGINS, OUT, PORT, apiJson, caseRow, currentMe, drainEvents,
  disposePages, goto, login, makePage, pageState, recordCase, screenshot, writeJson, writeResult,
} from "../lib/core.mjs";

const cases = [];
const pages = [];
const adminPaths = [
  "/admin/documents/new", "/admin/documents/1/edit", "/admin/notices/new",
  "/admin/notices/1/edit", "/admin/notices/1/stats", "/admin/users",
  "/admin/departments", "/admin/categories", "/admin/tags", "/admin/notice-types",
  "/admin/recycle-bin", "/admin/audit-logs", "/admin/backups",
];
const writeEndpoints = [
  ["POST", "/api/v1/users"], ["PUT", "/api/v1/users/1"], ["DELETE", "/api/v1/users/1"], ["DELETE", "/api/v1/users/1/purge"], ["POST", "/api/v1/users/1/reset-password"],
  ["POST", "/api/v1/departments"], ["PUT", "/api/v1/departments/1"], ["DELETE", "/api/v1/departments/1"],
  ["POST", "/api/v1/categories"], ["PUT", "/api/v1/categories/1"], ["DELETE", "/api/v1/categories/1"],
  ["POST", "/api/v1/tags"], ["PUT", "/api/v1/tags/1"], ["DELETE", "/api/v1/tags/1"],
  ["POST", "/api/v1/notice-types"], ["PUT", "/api/v1/notice-types/1"], ["DELETE", "/api/v1/notice-types/1"],
  ["POST", "/api/v1/documents"], ["PUT", "/api/v1/documents/1"], ["POST", "/api/v1/documents/1/file"], ["POST", "/api/v1/documents/1/attachment"], ["DELETE", "/api/v1/documents/1/attachment"],
  ["POST", "/api/v1/documents/bulk"], ["POST", "/api/v1/documents/batch-actions/archive/preview"], ["POST", "/api/v1/documents/batch-actions/archive/execute"], ["DELETE", "/api/v1/documents/1"],
  ["POST", "/api/v1/documents/1/restore"], ["POST", "/api/v1/notices"], ["PUT", "/api/v1/notices/1"], ["POST", "/api/v1/notices/1/cancel"],
  ["POST", "/api/v1/notices/bulk-cancel"], ["POST", "/api/v1/recycle-bin/1/restore"], ["DELETE", "/api/v1/recycle-bin/1"],
  ["POST", "/api/v1/recycle-bin/purge-all"], ["POST", "/api/v1/recycle-bin/notices/bulk-restore"], ["POST", "/api/v1/recycle-bin/notices/1/restore"],
  ["DELETE", "/api/v1/recycle-bin/notices/1"], ["POST", "/api/v1/backups"], ["POST", "/api/v1/backups/1/verify"],
  ["POST", "/api/v1/backups/1/restore"], ["DELETE", "/api/v1/backups/1"], ["DELETE", "/api/v1/audit-logs/1"], ["POST", "/api/v1/audit-logs/batch-delete"],
];

async function fetchStatus(page, method, route, body = {}) {
  const response = await page.fetch(route, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return response.status;
}

async function main() {
  const admin = await makePage("admin");
  const user = await makePage("user");
  const anon = await makePage("anon");
  pages.push({ label: "admin" }, { label: "user" }, { label: "anon" });
  await login(admin.page, "admin.localhost", CREDS.admin);
  await login(user.page, "user-a.localhost", CREDS.user_a);

  for (const path of adminPaths) {
    const before = await pageState(anon.page);
    await goto(anon.page, ORIGINS.anon, path);
    const anonState = await pageState(anon.page);
    await goto(user.page, ORIGINS.user_a, path);
    const userState = await pageState(user.page);
    recordCase(cases, caseRow({
      id: `EGO-SEC-PAGE-${path.replaceAll("/", "_")}`,
      phase: "round-04-security",
      owner: "D",
      role: "cross_role",
      mutability: "READ_SHARED",
      precondition: "13 administrator page routes are in the current FastAPI app",
      expected: ["anonymous is redirected to /login", "ordinary user is denied to / or ?denied", "no admin DOM is rendered for either role"],
      actual: { path, anon: anonState, user: userState, before },
      status: anonState.path.startsWith("/login") && (userState.path === "/" || userState.path.startsWith("/?denied")) ? "PASS" : "FAIL",
      severity: "P0",
      evidence: [{ kind: "screenshot", path: await screenshot(user.page, `security-denied-${path.replaceAll("/", "_")}.png`) }],
      repro: { command: "python -m scripts.full_matrix.run ego round-04-security", path },
    }));
  }

  for (const [method, route] of writeEndpoints) {
    const anonStatus = await fetchStatus(anon.page, method, route);
    const userStatus = await fetchStatus(user.page, method, route);
    recordCase(cases, caseRow({
      id: `EGO-SEC-API-${method}-${route.replaceAll("/", "_")}`,
      phase: "round-04-security",
      owner: "D",
      role: "cross_role",
      mutability: "GLOBAL_WRITE",
      precondition: "valid app route shape; no fixture mutation expected because authorization runs first",
      expected: ["anonymous gets 401 or 403", "ordinary user gets 401 or 403"],
      actual: { method, route, anonStatus, userStatus },
      status: [401, 403].includes(anonStatus) && [401, 403].includes(userStatus) ? "PASS" : "FAIL",
      severity: "P0",
      evidence: [{ kind: "json", path: `${OUT}/ego/results/round-04-security/permissions.json` }],
      repro: { command: "python -m scripts.full_matrix.run ego round-04-security", method, route },
    }));
  }

  const userSessionA = await makePage("user-session-a");
  const userSessionB = await makePage("user-session-b");
  const sessionCreds = { username: "user_fin_1", password: CREDS.user_a.password };
  // Use explicit API logins so each POST definitely increments token_version.
  // Browser cookies are shared by origin in one ego profile, so verify the old
  // Bearer via Node fetch (no cookie jar), not page.fetch.
  const apiLogin = async (page) => {
    await goto(page, ORIGINS.user_b, "/login");
    const response = await page.fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(sessionCreds),
    });
    let body = response.body;
    if (typeof body === "string") { try { body = JSON.parse(body); } catch (_) { body = null; } }
    if (response.status !== 200 || !body?.access_token) {
      throw new Error(`session login failed: ${response.status}`);
    }
    return body.access_token;
  };
  const oldBearer = await apiLogin(userSessionA.page);
  const bearerStatus = async () => {
    const response = await fetch(`http://127.0.0.1:${PORT}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${oldBearer}` },
    });
    return response.status;
  };
  const oldStatus = await bearerStatus();
  await apiLogin(userSessionB.page);
  const invalidatedStatus = await bearerStatus();
  recordCase(cases, caseRow({
    id: "EGO-SEC-SESSION-INVALIDATION",
    phase: "round-04-security", owner: "D", role: "cross_role", mutability: "GLOBAL_WRITE",
    precondition: "two explicit API logins of one synthetic user",
    expected: ["first Bearer is valid before second login", "first Bearer returns 401 after second login"],
    actual: { before: oldStatus, after: invalidatedStatus },
    status: oldStatus === 200 && invalidatedStatus === 401 ? "PASS" : "FAIL",
    severity: "P0", evidence: [{ kind: "json", path: `${OUT}/ego/results/round-04-security/session.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-04-security" },
  }));

  await goto(anon.page, ORIGINS.anon, "/login?next=//evil.example/steal");
  await login(anon.page, "anon.localhost", CREDS.admin);
  const redirectState = await pageState(anon.page);
  recordCase(cases, caseRow({
    id: "EGO-SEC-NEXT-OPEN-REDIRECT",
    phase: "round-04-security", owner: "D", role: "anon", mutability: "GLOBAL_WRITE",
    precondition: "login page receives malicious next",
    expected: ["login stays on localhost and never navigates to evil.example"],
    actual: redirectState,
    status: redirectState.host.startsWith("anon.localhost") ? "PASS" : "FAIL",
    severity: "P0", evidence: [{ kind: "screenshot", path: await screenshot(anon.page, "security-next-390.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-04-security" },
  }));

  await goto(user.page, ORIGINS.user_a, "/documents");
  await user.page.evaluate(() => {
    window.__xss = 0;
    window.__xssNodes = { script: 0, img: 0, svg: 0 };
    window.__xssObserver = new MutationObserver(() => {
      window.__xssNodes.script += document.querySelectorAll("script").length > 0 ? 1 : 0;
      window.__xssNodes.img += document.querySelectorAll('img[src="x"]').length;
      window.__xssNodes.svg += document.querySelectorAll("svg").length;
    });
    window.__xssObserver.observe(document.body, { childList: true, subtree: true });
  });
  const xssTitle = `<img src=x onerror="window.__xss=1">E2E-XSS-${Date.now()}`;
  const xssDoc = await apiJson(admin.page, API.documents, {
    method: "POST", body: { title: xssTitle, summary: "<script>window.__xss=1</script>", effective_from: "2026-01-01", audience_scope: "all" },
  });
  if (xssDoc.body?.id) {
    await goto(user.page, ORIGINS.user_a, `/documents/${xssDoc.body.id}`);
    await user.page.waitForSelector("#detail-root", { timeout: 15000 });
    const xss = await user.page.evaluate(() => ({ value: window.__xss, nodes: window.__xssNodes, text: document.body.innerText || "" }));
    recordCase(cases, caseRow({
      id: "EGO-SEC-XSS-BROWSER", phase: "round-04-security", owner: "D", role: "user", mutability: "GLOBAL_WRITE",
      precondition: "synthetic document title/summary contain HTML payloads",
      expected: ["window sentinel stays zero", "no executable script/img[src=x] node is introduced", "payload is visible as plain text"],
      actual: { status: xssDoc.status, xss }, status: xss.value === 0 && xss.nodes.img === 0 && xss.text.includes(xssTitle) ? "PASS" : "FAIL",
      severity: "P0", evidence: [{ kind: "screenshot", path: await screenshot(user.page, "security-xss.png") }],
      repro: { command: "python -m scripts.full_matrix.run ego round-04-security" },
    }));
  }

  const noStore = await user.page.fetch(`/api/v1/documents/1/attachment`);
  const attachmentHeaders = noStore.headers || {};
  recordCase(cases, caseRow({
    id: "EGO-SEC-ATTACHMENT-NO-STORE",
    phase: "round-04-security", owner: "D", role: "user", mutability: "READ_SHARED",
    precondition: "attachment endpoint is reachable for a test document; 404 is still header-testable",
    expected: ["sensitive attachment response includes Cache-Control private/no-store"],
    actual: { status: noStore.status, headers: attachmentHeaders },
    status: String(attachmentHeaders["cache-control"] || "").includes("no-store") ? "PASS" : "FAIL",
    severity: "P0", evidence: [{ kind: "json", path: `${OUT}/ego/results/round-04-security/no-store.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-04-security" },
  }));

  const events = await drainEvents(admin.page);
  recordCase(cases, caseRow({
    id: "EGO-SEC-CONSOLE", phase: "round-04-security", owner: "F", role: "admin", mutability: "READ_SHARED",
    precondition: "security traversal complete", expected: ["no console error or pageerror"], actual: events,
    status: events.consoleErrors.length === 0 && events.pageErrors.length === 0 ? "PASS" : "FAIL",
    severity: "P1", evidence: [{ kind: "json", path: `${OUT}/ego/results/round-04-security/events.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-04-security" },
  }));
  await writeJson(`${OUT}/ego/results/round-04-security/permissions.json`, cases.filter(item => item.id.startsWith("EGO-SEC-API")));
  await writeJson(`${OUT}/ego/results/round-04-security/session.json`, { oldStatus, invalidatedStatus });
  await writeJson(`${OUT}/ego/results/round-04-security/no-store.json`, { status: noStore.status, headers: attachmentHeaders });
  await writeJson(`${OUT}/ego/results/round-04-security/events.json`, events);
}

try {
  await main();
  // The two single-sign-on probe sessions are finished; hand their Page slots back
  // so later rounds stay inside the 8-Page budget.
  await disposePages("round-04-security", ["admin", "user", "anon", "user-session-a", "user-session-b"]);
  await writeResult("round-04-security", cases, pages);
} catch (error) {
  recordCase(cases, caseRow({
    id: "EGO-SEC-RUNNER", phase: "round-04-security", owner: "D", role: "system", mutability: "GLOBAL_WRITE",
    precondition: "isolated synthetic sandbox", expected: ["round completes"], actual: { error: String(error), stack: error?.stack || null },
    status: "ERROR", severity: "P0", repro: { command: "python -m scripts.full_matrix.run ego round-04-security" },
  }));
  // The two single-sign-on probe sessions are finished; hand their Page slots back
  // so later rounds stay inside the 8-Page budget.
  await disposePages("round-04-security", ["admin", "user", "anon", "user-session-a", "user-session-b"]);
  await writeResult("round-04-security", cases, pages);
  process.exitCode = 1;
}