// round-01-roles: admin/user/anon page coverage and responsive shell checks.

import {
  API, CREDS, ORIGINS, OUT, PORT, apiJson, caseRow, currentMe, disposePages, drainEvents, ensureDir, goto, login, makePage, pageState, recordCase, screenshot, setViewport, summarize, writeJson, writeResult,
} from "../lib/core.mjs";

const cases = [];
const pages = [];
const widths = [1440, 1024, 900, 768, 390];

async function visitPersona(page, host, role, path, id, { adminPage = false } = {}) {
  const states = [];
  const shots = [];
  const errors = [];
  for (const width of widths) {
    await setViewport(page, width);
    await goto(page, `http://${host}:${PORT}`, path);
    const state = await pageState(page);
    states.push({ width, ...state });
    if (width === 1440 || width === 390) {
      const shot = await screenshot(page, `${role}-${id}-${width}.png`);
      if (shot) shots.push({ kind: "screenshot", path: shot });
    }
    if (state.overflow) errors.push(`overflow@${width}:${state.scrollWidth - state.viewport.width}px`);
  }
  const me = await currentMe(page);
  return { host, role, path, states, shots, me, adminPage, errors };
}

async function main() {
  const admin = await makePage("admin");
  const userA = await makePage("user-a");
  const userB = await makePage("user-b");
  const anon = await makePage("anon");
  pages.push({ label: "admin", host: "admin.localhost" }, { label: "user-a", host: "user-a.localhost" }, { label: "user-b", host: "user-b.localhost" }, { label: "anon", host: "anon.localhost" });
  await login(admin.page, "admin.localhost", CREDS.admin);
  await login(userA.page, "user-a.localhost", CREDS.user_a);
  await login(userB.page, "user-b.localhost", { username: "user_fin_1", password: CREDS.user_a.password });

  const docs = await apiJson(admin.page, "/api/v1/documents?page_size=5");
  const notices = await apiJson(admin.page, "/api/v1/notices?page_size=5");
  const docId = docs.body?.items?.[0]?.id || 1;
  const noticeId = notices.body?.items?.[0]?.id || 1;

  const adminPaths = [
    ["dashboard-alias", "/dashboard", "admin"],
    ["documents", "/documents", "admin"],
    ["document-detail", `/documents/${docId}`, "admin"],
    ["document-new", "/admin/documents/new", "admin"],
    ["document-edit", `/admin/documents/${docId}/edit`, "admin"],
    ["notices", "/notices", "admin"],
    ["notice-detail", `/notices/${noticeId}`, "admin"],
    ["notice-new", "/admin/notices/new", "admin"],
    ["notice-edit", `/admin/notices/${noticeId}/edit`, "admin"],
    ["notice-stats", `/admin/notices/${noticeId}/stats`, "admin"],
    ["users", "/admin/users", "admin"],
    ["departments", "/admin/departments", "admin"],
    ["categories", "/admin/categories", "admin"],
    ["tags", "/admin/tags", "admin"],
    ["notice-types", "/admin/notice-types", "admin"],
    ["recycle-bin", "/admin/recycle-bin", "admin"],
    ["audit-logs", "/admin/audit-logs", "admin"],
    ["backups", "/admin/backups", "admin"],
    ["change-password", "/change-password", "admin"],
  ];
  const publicPaths = [
    ["dashboard", "/", "user"],
    ["documents", "/documents", "user"],
    ["document-detail", `/documents/${docId}`, "user"],
    ["notices", "/notices", "user"],
    ["notice-detail", `/notices/${noticeId}`, "user"],
    ["change-password", "/change-password", "user"],
    ["login", "/login", "anon"],
  ];

  for (const [label, path] of adminPaths) {
    const data = await visitPersona(admin.page, "admin.localhost", "admin", path, label, { adminPage: true });
    const visible = data.states.every(item => item.host.startsWith("admin.localhost") && !item.overflow);
    recordCase(cases, caseRow({
      id: `EGO-ROLE-ADMIN-${label}`,
      phase: "round-01-roles",
      owner: "D",
      role: "admin",
      mutability: "READ_SHARED",
      precondition: "admin_demo logged in on admin.localhost",
      expected: ["admin page remains on admin.localhost", "page renders at 1440/1024/900/768/390 without horizontal overflow"],
      actual: data,
      status: visible && data.me.body?.role === "admin" ? "PASS" : "FAIL",
      severity: "P1",
      evidence: data.shots,
      repro: { command: `python -m scripts.full_matrix.run ego round-01-roles`, path: path },
    }));
  }

  for (const [label, path, expectedRole] of publicPaths) {
    const persona = expectedRole === "anon" ? anon : userA;
    const host = expectedRole === "anon" ? "anon.localhost" : "user-a.localhost";
    const data = await visitPersona(persona.page, host, expectedRole, path, label);
    const ok = data.states.every(item => !item.overflow) && (expectedRole === "anon" ? data.states.every(item => item.path === "/login" || item.path.startsWith("/login")) : data.me.status === 200);
    recordCase(cases, caseRow({
      id: `EGO-ROLE-${expectedRole.toUpperCase()}-${label}`,
      phase: "round-01-roles",
      owner: "D",
      role: expectedRole,
      mutability: "READ_SHARED",
      precondition: `${host} session established`,
      expected: expectedRole === "anon" ? ["anonymous protected routes redirect to /login", "no overflow"] : ["user page renders", "no overflow"],
      actual: data,
      status: ok ? "PASS" : "FAIL",
      severity: "P0",
      evidence: data.shots,
      repro: { command: "python -m scripts.full_matrix.run ego round-01-roles", path },
    }));
  }

  await goto(anon.page, ORIGINS.anon, "/documents");
  const anonState = await pageState(anon.page);
  recordCase(cases, caseRow({
    id: "EGO-ROLE-ANON-REDIRECT",
    phase: "round-01-roles",
    owner: "D",
    role: "anon",
    mutability: "READ_SHARED",
    precondition: "no access_token for anon.localhost",
    expected: ["protected documents page redirects to /login", "login form is present"],
    actual: anonState,
    status: anonState.path.startsWith("/login") ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "screenshot", path: await screenshot(anon.page, "anon-redirect-390.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-01-roles", path: "/documents" },
  }));

  await goto(userA.page, ORIGINS.user_a, "/admin/users");
  const denied = await pageState(userA.page);
  recordCase(cases, caseRow({
    id: "EGO-ROLE-USER-ADMIN-DENIED",
    phase: "round-01-roles",
    owner: "D",
    role: "user",
    mutability: "READ_SHARED",
    precondition: "user_rnd_1 logged in on user-a.localhost",
    expected: ["ordinary user cannot render admin page", "server redirects to home/denied"],
    actual: denied,
    status: denied.path === "/" || denied.path.startsWith("/?denied") ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "screenshot", path: await screenshot(userA.page, "user-admin-denied-390.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-01-roles", path: "/admin/users" },
  }));

  const events = await drainEvents(admin.page);
  const eventsOk = events.consoleErrors.length === 0 && events.pageErrors.length === 0;
  recordCase(cases, caseRow({
    id: "EGO-ROLE-CONSOLE",
    phase: "round-01-roles",
    owner: "F",
    role: "admin",
    mutability: "READ_SHARED",
    precondition: "admin page traversal complete",
    expected: ["no console error or pageerror"],
    actual: events,
    status: eventsOk ? "PASS" : "FAIL",
    severity: "P1",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/round-01-roles/events.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-01-roles" },
  }));
  await ensureDir(`${OUT}/ego/results/round-01-roles`);
  await writeJson(`${OUT}/ego/results/round-01-roles/events.json`, events);
  await writeJson(`${OUT}/ego/results/round-01-roles/pages.json`, pages);
}

try {
  await main();
  await disposePages("round-01-roles", ["admin", "user-a", "user-b", "anon"]);
  await writeResult("round-01-roles", cases, pages);
} catch (error) {
  recordCase(cases, caseRow({
    id: "EGO-ROLE-RUNNER", phase: "round-01-roles", owner: "D", role: "system", mutability: "READ_SHARED",
    precondition: "ego runtime", expected: ["round completes"], actual: { error: String(error), stack: error?.stack || null },
    status: "ERROR", severity: "P0", repro: { command: "python -m scripts.full_matrix.run ego round-01-roles" },
  }));
  await disposePages("round-01-roles", ["admin", "user-a", "user-b", "anon"]);
  await writeResult("round-01-roles", cases, pages);
  process.exitCode = 1;
}