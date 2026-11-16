// Shared ego-browser helpers for the isolated full-matrix run.
// All paths are absolute at runtime; secrets never enter result files.

const fs = await import("node:fs/promises");
const path = await import("node:path");
const crypto = await import("node:crypto");

export const OUT = "__FULL_MATRIX_EVIDENCE_ROOT__";
export const PORT = __FULL_MATRIX_PORT__;
export const RUN_ID = "__FULL_MATRIX_RUN_ID__";
export const TASK_SPACE = "__FULL_MATRIX_TASK_SPACE__";
// The sandbox root is injected by the runner.  The TEMP fallback must not name
// a particular machine: a hard-coded `C:/Users/<someone>` path both leaks the
// operator's account name and breaks on every other host (项目规范 §29 bans
// hard-coded local paths).
const TEMP_ROOT = (process.env.TMPDIR || process.env.TEMP || process.env.TMP
  || (process.platform === "win32" ? "C:/Windows/Temp" : "/tmp")).replaceAll("\\", "/");
export const SANDBOX = process.env.FULL_MATRIX_SANDBOX_ROOT
  || `${TEMP_ROOT}/docnotice-e2e/${RUN_ID}`;
export const UPLOAD = `${SANDBOX}/e2e_files`;
export const ORIGINS = {
  admin: `http://admin.localhost:${PORT}`,
  user_a: `http://user-a.localhost:${PORT}`,
  user_b: `http://user-b.localhost:${PORT}`,
  target: `http://target.localhost:${PORT}`,
  outside: `http://outside.localhost:${PORT}`,
  anon: `http://anon.localhost:${PORT}`,
};
export const CREDS = {
  admin: {
    username: process.env.E2E_ADMIN || "admin_demo",
    password: process.env.E2E_ADMIN_PWD || "Demo@123456",
  },
  user_a: {
    username: process.env.E2E_USER || "user_rnd_1",
    password: process.env.E2E_USER_PWD || "Demo@123456",
  },
};
export const task = await taskSpace(TASK_SPACE);

export function now() {
  return new Date().toISOString();
}

export function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export async function ensureDir(dir) {
  await fs.mkdir(dir, { recursive: true });
}

export async function writeJson(file, value) {
  await ensureDir(path.dirname(file));
  await fs.writeFile(file, JSON.stringify(value, null, 2) + "\n", "utf8");
}

export async function readJson(file, fallback = null) {
  try {
    return JSON.parse(await fs.readFile(file, "utf8"));
  } catch (_) {
    return fallback;
  }
}

export async function sha256File(file) {
  const bytes = await fs.readFile(file);
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

export function safeText(value, max = 400) {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return (text || "").replace(/(password|passwd|pwd|authorization|set-cookie|access_token)\s*[=:]\s*[^\s,;]+/gi, "$1=[REDACTED]").slice(0, max);
}

export function caseRow({ id, phase, owner, role, mutability, precondition, depends_on = [], steps = [], expected = [], actual = {}, status = "PASS", severity = "NONE", evidence = [], repro = {}, attempt = 1, defect_id = null, fix_commit = null, duration_ms = 0 }) {
  return {
    id, phase, owner, role, mutability, precondition, depends_on,
    steps, expected, actual, status, severity, evidence, repro,
    attempt, defect_id, fix_commit, duration_ms,
  };
}

export function recordCase(bucket, row) {
  bucket.push(row);
  return row;
}

export function summarize(cases) {
  const summary = { total: cases.length, pass: 0, fail: 0, error: 0, blocked: 0, skipped: 0 };
  const map = { PASS: "pass", FAIL: "fail", ERROR: "error", BLOCKED: "blocked", SKIP: "skipped" };
  for (const item of cases) summary[map[item.status] || "error"] += 1;
  return summary;
}

export async function writeResult(roundId, cases, pages = [], extra = {}) {
  const summary = summarize(cases);
  const payload = {
    schemaVersion: "ego-test-result/v1",
    runId: RUN_ID,
    roundId,
    taskSpace: TASK_SPACE,
    app: { baseUrl: `http://127.0.0.1:${PORT}`, sandbox: SANDBOX, generatedAt: now() },
    cases,
    summary,
    pages,
    ...extra,
  };
  await writeJson(`${OUT}/ego/results/${roundId}/result.json`, payload);
  console.log(JSON.stringify({ roundId, summary }, null, 2));
  return payload;
}

/**
 * Declare Pages this round opened and no longer needs. The runner closes exactly
 * these after the round finishes (see recycle_pages.mjs). Never a keep-list: a
 * round only names what it created itself.
 */
export async function disposePages(roundId, labels = []) {
  if (!labels.length) return null;
  const file = `${OUT}/ego/results/${roundId}/disposable-pages.json`;
  await writeJson(file, labels);
  return file;
}

export async function finishSpace() {
  await task.finish({ keep: [] });
}

const PAGE_SLOTS = ["p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8"];
const PAGE_USAGE = `${OUT}/ego/results/page-usage.json`;

async function setupPage(page) {
  try { await page.cdp("Runtime.enable"); } catch (_) {}
  try { await page.cdp("Log.enable"); } catch (_) {}
  try { await page.cdp("Network.enable"); } catch (_) {}
  try { await page.cdp("egoAutomated.disable"); } catch (_) {}
  return { page, label: page.label };
}

// A Page recorded by an earlier round may have been closed, or may still exist but
// be attached to a document that already navigated away. Reusing such a Page makes
// CDP answer "Inspected target navigated or closed" somewhere deep in an event
// callback, which surfaces as an opaque crash far from the cause. Verify first.
async function pageIsUsable(page) {
  try {
    await page.url();
    await page.evaluate(() => 1);
    return true;
  } catch (_) {
    return false;
  }
}

// Ego enforces a hard Page budget (8). Reuse existing managed Pages instead of
// creating a new tab on every round, while keeping one TaskSpace for the goal.
export async function makePage(label) {
  const existing = await task.pages();
  const usage = await readJson(PAGE_USAGE, {}) || {};
  const preferred = usage[label];
  let page = preferred ? existing.find(item => item.label === preferred) : null;
  if (page && !(await pageIsUsable(page))) {
    try { await page.close(); } catch (_) {}
    page = null;
  }
  if (!page) {
    // Only consider Pages nobody else claims, and only ones that still answer.
    const used = new Set(Object.values(usage));
    for (const candidate of existing) {
      if (used.has(candidate.label)) continue;
      if (await pageIsUsable(candidate)) { page = candidate; break; }
    }
  }
  // After task.finish(), the profile usually has only p1 left. Reusing it for
  // multiple host-only roles overwrites cookies; create fresh managed Pages.
  if (!page) page = await task.newPage();
  usage[label] = page.label;
  await writeJson(PAGE_USAGE, usage);
  return setupPage(page);
}


export async function setViewport(page, width) {
  const height = width <= 390 ? 844 : 900;
  await page.cdp("Emulation.setDeviceMetricsOverride", {
    width, height, deviceScaleFactor: 1, mobile: width <= 768,
  });
}

export async function goto(page, origin, route = "/") {
  const url = route.startsWith("http") ? route : `${origin}${route}`;
  // `waitUntil: "domcontentloaded"` is the right target here: several pages hold
  // long-polling/streaming resources, so waiting for the full `load` lifecycle
  // times out even though the document is complete and interactive.
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
  try {
    await page.waitForLoadState("domcontentloaded", { timeout: 10000 });
  } catch (_) {
    // Document is usable; per-call selectors decide the rest.
  }
  return url;
}

export async function login(page, host, creds) {
  const origin = `http://${host}:${PORT}`;
  await goto(page, origin, "/login");
  const me = await page.fetch("/api/v1/auth/me");
  let meBody = me.body;
  if (typeof meBody === "string") { try { meBody = JSON.parse(meBody); } catch (_) { meBody = null; } }
  const currentName = meBody?.user?.username || meBody?.username;
  if (me.status !== 200 || currentName !== creds.username) {
    await page.waitForSelector("#username", { timeout: 12000 });
    await page.fill("#username", creds.username);
    await page.fill("#password", creds.password);
    await page.click("#login-submit");
  }
  let leftLogin = false;
  for (let i = 0; i < 80; i++) {
    leftLogin = await page.evaluate(() => location.pathname !== "/login");
    if (leftLogin) break;
    await sleep(250);
  }
  if (!leftLogin) throw new Error(`${host} login did not leave /login`);
  await page.waitForLoadState();
  const check = await page.fetch("/api/v1/auth/me");
  if (check.status !== 200) throw new Error(`${host} auth/me=${check.status}`);
  return check;
}

export async function currentMe(page) {
  const response = await page.fetch("/api/v1/auth/me");
  let body = response.body;
  if (typeof body === "string") {
    try { body = JSON.parse(body); } catch (_) { body = null; }
  }
  return { status: response.status, body: body?.user || body || null };
}

export async function api(page, route, options = {}) {
  const opts = { ...options };
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers = { ...(opts.headers || {}), "Content-Type": "application/json" };
  }
  return page.fetch(route, opts);
}

export async function apiJson(page, route, options = {}) {
  const response = await api(page, route, options);
  let body = response.body;
  if (typeof body === "string") {
    try { body = JSON.parse(body); } catch (_) {}
  }
  return { status: response.status, ok: response.ok, body, headers: response.headers || {} };
}

export async function waitForText(page, selector, timeout = 15000) {
  await page.waitForSelector(selector, { timeout });
  return page.evaluate((sel) => (document.querySelector(sel)?.innerText || "").trim(), selector);
}

export async function screenshot(page, name) {
  const file = `${OUT}/ego/screenshots/${name}`;
  await ensureDir(path.dirname(file));
  try {
    await page.screenshot({ path: file, fullPage: false });
  } catch (_) {
    return null;
  }
  return file;
}

export async function snapshot(page, name) {
  const file = `${OUT}/ego/snapshots/${name}.json`;
  await ensureDir(path.dirname(file));
  try {
    const value = await page.snapshot({ scope: "full_page", includeStableLocator: true });
    await writeJson(file, value);
    return file;
  } catch (_) {
    return null;
  }
}

export async function modalConfirm(page, { reason = "", timeout = 8000 } = {}) {
  await page.waitForSelector("#modal-root [data-confirm]", { timeout });
  if (reason) {
    try { await page.fill("#confirm-input", reason); } catch (_) {}
  }
  await page.click("#modal-root [data-confirm]", { label: "确认操作" });
  try {
    await page.waitForSelector("#modal-root [data-confirm]", { state: "detached", timeout: 8000 });
  } catch (_) {}
}

export async function drainEvents(page) {
  let events = [];
  try { events = await page.events(); } catch (_) {}
  const result = { consoleErrors: [], pageErrors: [], browserLogErrors: [], network: [], loadingFailed: [] };
  for (const ev of events || []) {
    const method = ev?.method || "";
    const params = ev?.params || {};
    if (method === "Runtime.consoleAPICalled" && params.type === "error") {
      result.consoleErrors.push({ source: params.type, text: safeText(params.args) });
    } else if (method === "Runtime.exceptionThrown") {
      result.pageErrors.push({ text: safeText(params.exceptionDetails?.exception?.description || params.exceptionDetails?.text) });
    } else if (method === "Log.entryAdded" && params.entry?.level === "error") {
      result.browserLogErrors.push({ text: safeText(params.entry.text), url: safeText(params.entry.url) });
    } else if (method === "Network.responseReceived") {
      const response = params.response || {};
      result.network.push({ url: safeText(response.url), status: response.status, mimeType: response.mimeType, requestId: params.requestId });
    } else if (method === "Network.loadingFailed") {
      result.loadingFailed.push({ errorText: safeText(params.errorText), blockedReason: params.blockedReason, requestId: params.requestId });
    }
  }
  return result;
}

export function allowed4xx(event, allowlist) {
  return (allowlist || []).some(item => event.url?.includes(item.path) && Number(event.status) === Number(item.status) && (!item.method || item.method === "GET" || true));
}

export async function captureDownload(page, origin, pathSuffix, filename, clickSelector) {
  const downloadPromise = page.waitForEvent("download", { timeout: 30000 });
  await page.click(clickSelector, { label: "下载" });
  const download = await downloadPromise;
  const file = `${OUT}/ego/downloads/${filename}`;
  await ensureDir(path.dirname(file));
  await download.saveAs(file);
  const stat = await fs.stat(file);
  return { file, suggestedFilename: download.suggestedFilename?.() || filename, size: stat.size, sha256: await sha256File(file), url: safeText(download.url?.() || `${origin}${pathSuffix}`) };
}

export async function upload(page, selector, files) {
  await page.setInputFiles(selector, files);
}

export async function pageState(page) {
  return page.evaluate(() => ({
    url: location.href,
    host: location.host,
    title: document.title,
    path: location.pathname,
    viewport: { width: innerWidth, height: innerHeight },
    scrollWidth: document.documentElement.scrollWidth,
    overflow: document.documentElement.scrollWidth > innerWidth + 2,
    bodyText: (document.body.innerText || "").slice(0, 500),
    modalCount: document.querySelectorAll("#modal-root [data-confirm]").length,
  }));
}

export async function getPageByPath(page, origin, pathname, label) {
  await goto(page, origin, pathname);
  return { label, ...(await pageState(page)) };
}

export async function expectStatus(label, actual, expected) {
  return { label, expected, actual, pass: actual === expected };
}

export async function getJsonList(page, route) {
  const result = await apiJson(page, route);
  if (result.status !== 200) throw new Error(`${route} -> ${result.status}`);
  return result.body || {};
}

export function firstId(listResult) {
  return listResult.items?.[0]?.id ?? null;
}

export async function ensureLogin(page, host, creds) {
  return login(page, host, creds);
}

export async function setSelect(page, selector, value) {
  await page.evaluate(({ selector, value }) => {
    const el = document.querySelector(selector);
    if (!el) throw new Error(`missing ${selector}`);
    el.value = value;
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }, { selector, value });
}

export async function assertNoForbiddenText(page, needles) {
  const text = await page.evaluate(() => document.body.innerText || "");
  return needles.filter(needle => text.includes(needle));
}

export async function apiBinaryHash(page, route) {
  const response = await page.fetch(route);
  const body = response.body;
  if (typeof body === "string") {
    return { status: response.status, content: safeText(body, 120000) };
  }
  return { status: response.status, content: body };
}

export const API = {
  authMe: "/api/v1/auth/me",
  documents: "/api/v1/documents",
  notices: "/api/v1/notices",
  categories: "/api/v1/categories",
  tags: "/api/v1/tags",
  departments: "/api/v1/departments",
  users: "/api/v1/users",
  noticeTypes: "/api/v1/notice-types",
  dashboard: "/api/v1/dashboard",
  recycle: "/api/v1/recycle-bin",
  backups: "/api/v1/backups",
  audits: "/api/v1/audit-logs",
};