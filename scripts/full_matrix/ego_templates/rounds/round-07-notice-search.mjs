// round-07-notice-search: true-browser evidence for notice content keyword and linked-document summary keyword.

import {
  API, CREDS, ORIGINS, OUT, apiJson, caseRow, disposePages, drainEvents, goto, login, makePage, pageState, recordCase, screenshot, writeJson, writeResult,
} from "../lib/core.mjs";

const cases = [];
const pages = [];
const tag = `E2E-20261111-impl01-SEARCH-${Date.now()}`;

function bodyOf(resp) {
  const body = resp?.body;
  if (!body || typeof body !== "object") return {};
  return body;
}

async function postJson(page, route, body) {
  return apiJson(page, route, { method: "POST", body });
}

async function findSeedUserId(page, username = CREDS.user_a.username) {
  const result = await apiJson(page, `/api/v1/users?keyword=${encodeURIComponent(username)}&page_size=20`);
  return bodyOf(result).items?.[0]?.id || null;
}

async function waitForListText(page, selector, needle, timeoutMs = 8000) {
  const deadline = Date.now() + timeoutMs;
  let text = "";
  while (Date.now() < deadline) {
    text = await page.evaluate(sel => document.querySelector(sel)?.innerText || "", selector);
    if (text.includes(needle)) return text;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  return text;
}

async function main() {
  const admin = await makePage("admin");
  const user = await makePage("user_a");
  pages.push({ label: "admin", host: "admin.localhost" }, { label: "user_a", host: "user-a.localhost" });
  await login(admin.page, "admin.localhost", CREDS.admin);
  await login(user.page, "user-a.localhost", CREDS.user_a);

  const userId = await findSeedUserId(admin.page);
  const outsideUserId = await findSeedUserId(admin.page, "user_fin_1");
  const contentMarker = `${tag}-正文`;
  const outsideMarker = `${tag}-范围外`;
  const ownTitle = `${tag}-用户可见`;
  const outsideTitle = `${tag}-范围外通知`;
  const ownNotice = await postJson(admin.page, API.notices, {
    title: ownTitle,
    content: `请在本周五前完成${contentMarker}`,
    notice_type: "发布",
    target: userId ? { scope: "user", department_ids: [], user_ids: [userId] } : { scope: "all", department_ids: [], user_ids: [] },
    document_ids: [],
  });
  const outsideNotice = await postJson(admin.page, API.notices, {
    title: outsideTitle,
    content: `不应被普通用户搜到：${outsideMarker}`,
    notice_type: "发布",
    target: outsideUserId ? { scope: "user", department_ids: [], user_ids: [outsideUserId] } : { scope: "all", department_ids: [], user_ids: [] },
    document_ids: [],
  });
  recordCase(cases, caseRow({
    id: "EGO-SEARCH-NOTICE-CREATE-FIXTURE",
    phase: "round-07-notice-search",
    owner: "B",
    role: "admin",
    mutability: "GLOBAL_WRITE",
    precondition: "admin/user sessions and seed user id",
    expected: ["published notices with distinct content markers are created"],
    actual: { userId, outsideUserId, ownStatus: ownNotice.status, outsideStatus: outsideNotice.status },
    status: ownNotice.status === 201 && outsideNotice.status === 201 && userId && outsideUserId ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/round-07-notice-search/fixtures.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-07-notice-search" },
  }));

  await goto(admin.page, ORIGINS.admin, "/notices");
  await admin.page.waitForSelector("#f-keyword", { timeout: 12000 });
  const noticePlaceholder = await admin.page.evaluate(() => document.querySelector("#f-keyword")?.placeholder || "");
  await admin.page.fill("#f-keyword", contentMarker);
  await admin.page.click("#btn-search");
  const adminListText = await waitForListText(admin.page, "#notice-list", ownTitle);
  recordCase(cases, caseRow({
    id: "EGO-SEARCH-NOTICE-ADMIN-CONTENT",
    phase: "round-07-notice-search",
    owner: "B",
    role: "admin",
    mutability: "READ_SHARED",
    precondition: "published notice with content marker",
    expected: ["placeholder mentions title/content", "admin browser list hits notice by pure-text content"],
    actual: { placeholder: noticePlaceholder, listText: adminListText.slice(0, 600) },
    status: noticePlaceholder.includes("标题") && noticePlaceholder.includes("正文") && adminListText.includes(ownTitle) ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "round-07-notice-admin-content.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-07-notice-search" },
  }));

  await goto(user.page, ORIGINS.user_a, "/notices");
  await user.page.waitForSelector("#f-keyword", { timeout: 12000 });
  await user.page.fill("#f-keyword", contentMarker);
  await user.page.click("#btn-search");
  const ownText = await waitForListText(user.page, "#notice-list", ownTitle);
  await user.page.fill("#f-keyword", outsideMarker);
  await user.page.click("#btn-search");
  await user.page.waitForTimeout(1000);
  const outsideText = await waitForListText(user.page, "#notice-list", "没有", 3000);
  const outsideHidden = !outsideText.includes(outsideTitle);
  recordCase(cases, caseRow({
    id: "EGO-SEARCH-NOTICE-USER-SCOPE",
    phase: "round-07-notice-search",
    owner: "G",
    role: "user",
    mutability: "READ_SHARED",
    precondition: "one target notice and one out-of-scope notice",
    expected: ["user can hit own notice by content", "out-of-scope notice is not exposed by content keyword"],
    actual: { ownText: ownText.slice(0, 400), outsideText: outsideText.slice(0, 400), outsideHidden },
    status: ownText.includes(ownTitle) && outsideHidden ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "screenshot", path: await screenshot(user.page, "round-07-notice-user-scope.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-07-notice-search" },
  }));

  const summaryMarker = `${tag}-摘要`;
  const titleOnlyDoc = `${tag}-标题资料`;
  const summaryDocTitle = "E2E-普通资料-标题无关键词";
  const d1 = await postJson(admin.page, API.documents, {
    title: titleOnlyDoc, summary: "普通说明", effective_from: "2026-01-01", audience_scope: "all",
  });
  const d2 = await postJson(admin.page, API.documents, {
    title: summaryDocTitle, summary: `关联候选说明命中：${summaryMarker}`, effective_from: "2026-01-01", audience_scope: "all",
  });
  await goto(admin.page, ORIGINS.admin, "/admin/notices/new");
  await admin.page.waitForSelector("#doc-search", { timeout: 12000 });
  // The form is taller than the viewport; scroll the candidate search into view
  // before ego's visible-in-viewport action check.
  await admin.page.evaluate(() => document.querySelector("#doc-search")?.scrollIntoView({ block: "center" }));
  await admin.page.waitForTimeout(250);
  const docPlaceholder = await admin.page.evaluate(() => document.querySelector("#doc-search")?.placeholder || "");
  await admin.page.fill("#doc-search", summaryMarker);
  // Debounce is 300ms; wait until the server-filtered pool has replaced the
  // initial all-documents pool before reading the candidate list.
  await admin.page.waitForTimeout(1200);
  const candidateText = await admin.page.evaluate(() => document.querySelector("#doc-list")?.innerText || "");
  const candidateCount = await admin.page.evaluate(() => document.querySelectorAll("#doc-list input[type=checkbox]").length);
  const titleOnlyLeaked = candidateText.includes(titleOnlyDoc);
  recordCase(cases, caseRow({
    id: "EGO-SEARCH-LINKED-DOCUMENT-SUMMARY",
    phase: "round-07-notice-search",
    owner: "A",
    role: "admin",
    mutability: "READ_SHARED",
    precondition: "one summary-hit document and one title-only document",
    expected: ["candidate placeholder matches document search wording", "summary keyword shows summary-hit document", "title-only document does not leak into result"],
    actual: { d1: d1.status, d2: d2.status, placeholder: docPlaceholder, candidateText: candidateText.slice(0, 600), candidateCount, titleOnlyLeaked },
    status: d1.status === 201 && d2.status === 201 && docPlaceholder.includes("标题") && docPlaceholder.includes("说明") && candidateText.includes(summaryDocTitle) && !titleOnlyLeaked && candidateCount === 1 ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "round-07-linked-document-summary.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-07-notice-search" },
  }));

  const state = await pageState(admin.page);
  const events = await drainEvents(admin.page);
  recordCase(cases, caseRow({
    id: "EGO-SEARCH-CONSOLE",
    phase: "round-07-notice-search",
    owner: "F",
    role: "admin",
    mutability: "READ_SHARED",
    precondition: "search UI rounds complete",
    expected: ["no horizontal overflow", "no console error or pageerror"],
    actual: { state, events },
    status: !state.overflow && events.consoleErrors.length === 0 && events.pageErrors.length === 0 ? "PASS" : "FAIL",
    severity: "P1",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/round-07-notice-search/events.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-07-notice-search" },
  }));

  await writeJson(`${OUT}/ego/results/round-07-notice-search/fixtures.json`, {
    userId, outsideUserId, ownNotice: { status: ownNotice.status, body: ownNotice.body }, outsideNotice: { status: outsideNotice.status, body: outsideNotice.body },
    documents: [{ status: d1.status, body: d1.body }, { status: d2.status, body: d2.body }],
  });
  await writeJson(`${OUT}/ego/results/round-07-notice-search/searches.json`, {
    noticePlaceholder, adminListText, ownText, outsideText, docPlaceholder, candidateText, candidateCount, state,
  });
  await writeJson(`${OUT}/ego/results/round-07-notice-search/events.json`, events);
}

try {
  await main();
  await disposePages("round-07-notice-search", ["admin", "user_a"]);
  await writeResult("round-07-notice-search", cases, pages);
} catch (error) {
  recordCase(cases, caseRow({
    id: "EGO-SEARCH-RUNNER", phase: "round-07-notice-search", owner: "H", role: "system", mutability: "GLOBAL_WRITE",
    precondition: "isolated synthetic sandbox", expected: ["round completes"],
    actual: { error: String(error), stack: error?.stack || null }, status: "ERROR", severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-07-notice-search" },
  }));
  await disposePages("round-07-notice-search", ["admin", "user_a"]);
  await writeResult("round-07-notice-search", cases, pages);
  process.exitCode = 1;
}
