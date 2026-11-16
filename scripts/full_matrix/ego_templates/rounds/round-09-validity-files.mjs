// round-09-validity-files: date-boundary rules, historical-date lookup, the
// locked "暂无失效日期" wording, validity-conflict handling, file replacement and
// multi-image merge — all driven through the real browser.
//
// Gap this closes: no ego round touched validity at all, and file work was
// limited to "upload + scheme-A download" in round-02.

import {
  API, CREDS, ORIGINS, OUT, UPLOAD, apiJson, caseRow, disposePages, drainEvents, goto, login, makePage, recordCase, screenshot, writeJson, writeResult,
} from "../lib/core.mjs";

const ROUND = "round-09-validity-files";
const TAG = `E2E-VF-${Date.now()}`;
const cases = [];
const pages = [];

const listOf = (resp) => {
  const body = resp?.body && typeof resp.body === "object" ? resp.body : {};
  return Array.isArray(body) ? body : (body.items || []);
};

async function createDoc(page, body) {
  return apiJson(page, API.documents, { method: "POST", body });
}

async function findDoc(page, title) {
  const resp = await apiJson(page, `${API.documents}?keyword=${encodeURIComponent(title)}&page_size=50`);
  return listOf(resp).find(d => (d.title || "") === title) || null;
}

async function statusOn(page, docId, dateIso) {
  const resp = await apiJson(page, `${API.documents}/${docId}/status?date=${dateIso}`);
  return { status: resp.status, body: resp.body };
}

async function main() {
  const admin = await makePage("admin");
  const user = await makePage("user-a");
  pages.push({ label: "admin", host: "admin.localhost" }, { label: "user-a", host: "user-a.localhost" });
  await login(admin.page, "admin.localhost", CREDS.admin);
  await login(user.page, "user-a.localhost", CREDS.user_a);

  // ------------------------------------------------------------------
  // A. 九类日期边界 + 闭区间（§14/§31）
  // ------------------------------------------------------------------
  // One window 2026-03-01..2026-03-31 with no archive lets every boundary be
  // probed from the same row.
  const boundaryTitle = `${TAG}-边界`;
  const created = await createDoc(admin.page, {
    title: boundaryTitle, effective_from: "2026-03-01", effective_to: "2026-03-31", audience_scope: "all",
  });
  const boundaryDoc = await findDoc(admin.page, boundaryTitle);

  const probes = [
    ["2026-02-28", "pending", "生效前一天"],
    ["2026-03-01", "active", "生效当天"],
    ["2026-03-02", "active", "生效后一天"],
    ["2026-03-30", "active", "失效前一天"],
    ["2026-03-31", "active", "失效当天（闭区间）"],
    ["2026-04-01", "expired", "失效后一天"],
  ];
  const observed = [];
  for (const [date, expected, label] of probes) {
    const got = await statusOn(admin.page, boundaryDoc?.id, date);
    observed.push({ date, label, expected, actual: got.body?.status ?? null, http: got.status });
  }
  const boundariesOk = observed.every(o => o.actual === o.expected);
  recordCase(cases, caseRow({
    id: "EGO-VF-DATE-BOUNDARIES",
    phase: ROUND, owner: "B", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: "a document effective 2026-03-01..2026-03-31",
    expected: ["前后各一天与两端当天都按闭区间计算",
               "生效前一天=pending / 生效当天=active / 失效当天=active / 失效后一天=expired"],
    actual: { created: created.status, docId: boundaryDoc?.id ?? null, observed },
    status: boundariesOk ? "PASS" : "FAIL",
    severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-09-validity-files", path: "/api/v1/documents/{id}/status" },
  }));

  // ------------------------------------------------------------------
  // B. effective_to 为空 = 「暂无失效日期」（锁定决策，禁「长期/永久有效」）
  // ------------------------------------------------------------------
  const openTitle = `${TAG}-无失效日`;
  await createDoc(admin.page, {
    title: openTitle, effective_from: "2026-01-01", effective_to: null, audience_scope: "all",
  });
  const openDoc = await findDoc(admin.page, openTitle);

  await goto(admin.page, ORIGINS.admin, `/documents/${openDoc?.id}`);
  await admin.page.waitForTimeout(1200);
  const detailText = await admin.page.evaluate(() => (document.body.innerText || ""));
  const hasRequired = detailText.includes("暂无失效日期");
  const bannedTerms = ["长期有效", "永久有效", "暂无已知失效日"];
  const foundBanned = bannedTerms.filter(term => detailText.includes(term));

  const futureStatus = await statusOn(admin.page, openDoc?.id, "2099-12-31");
  recordCase(cases, caseRow({
    id: "EGO-VF-EMPTY-EFFECTIVE-TO",
    phase: ROUND, owner: "B", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: "a document created with effective_to = null",
    expected: ["详情页出现「暂无失效日期」",
               "不得出现「长期有效 / 永久有效 / 暂无已知失效日」",
               "未来任意日期仍为 active（空失效日不表示永久有效，但状态计算为有效期未结束）"],
    actual: { hasRequired, foundBanned, status2099: futureStatus.body?.status ?? null },
    status: hasRequired && foundBanned.length === 0 && futureStatus.body?.status === "active" ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "vf-empty-effective-to.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-09-validity-files", path: `/documents/${openDoc?.id}` },
  }));

  // ------------------------------------------------------------------
  // C. 详情页历史日期查询控件（§15）—— 真实 UI
  // ------------------------------------------------------------------
  let uiQuery = null;
  try {
    await admin.page.fill("#status-date", "2026-06-15");
    await admin.page.click("#btn-status-query");
    await admin.page.waitForTimeout(1200);
    uiQuery = await admin.page.evaluate(() => ({
      result: (document.querySelector("#status-result")?.innerText || "").trim(),
    }));
  } catch (e) {
    uiQuery = { error: String(e) };
  }
  recordCase(cases, caseRow({
    id: "EGO-VF-UI-DATE-QUERY",
    phase: ROUND, owner: "B", role: "admin", mutability: "READ_SHARED",
    precondition: "document detail page open for a document without an end date",
    expected: ["填写日期后点击查询，结果区返回状态而不是空白"],
    actual: uiQuery,
    status: uiQuery && !uiQuery.error && (uiQuery.result || "").length > 0 ? "PASS" : "FAIL",
    severity: "P1",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "vf-date-query.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-09-validity-files", path: "/documents/{id}" },
  }));

  // ------------------------------------------------------------------
  // D. 有效期冲突：R1 倒挂必须拒绝
  // ------------------------------------------------------------------
  const inverted = await createDoc(admin.page, {
    title: `${TAG}-倒挂`, effective_from: "2026-09-01", effective_to: "2026-08-01", audience_scope: "all",
  });
  recordCase(cases, caseRow({
    id: "EGO-VF-R1-INVERTED",
    phase: ROUND, owner: "B", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: "effective_from later than effective_to",
    expected: ["R1 倒挂被拒绝（409 VALIDITY_CONFLICT）"],
    actual: { status: inverted.status, code: inverted.body?.error?.code ?? null },
    status: inverted.status === 409 && (inverted.body?.error?.code === "VALIDITY_CONFLICT") ? "PASS" : "FAIL",
    severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-09-validity-files", path: "/api/v1/documents" },
  }));

  // ------------------------------------------------------------------
  // E. R2 覆盖通道：改既有窗口默认阻止，带 confirm_overwrite + 原因才通过
  // ------------------------------------------------------------------
  const r2Title = `${TAG}-覆盖`;
  await createDoc(admin.page, {
    title: r2Title, effective_from: "2026-05-01", effective_to: "2026-06-30", audience_scope: "all",
  });
  const r2Doc = await findDoc(admin.page, r2Title);

  const blocked = await apiJson(admin.page, `${API.documents}/${r2Doc?.id}`, {
    method: "PUT", body: { effective_from: "2026-05-15", effective_to: "2026-07-15" },
  });
  const confirmed = await apiJson(admin.page, `${API.documents}/${r2Doc?.id}`, {
    method: "PUT",
    body: {
      effective_from: "2026-05-15", effective_to: "2026-07-15",
      confirm_overwrite: true, modify_reason: `${TAG} 覆盖原因`,
    },
  });
  const reasonMissing = await apiJson(admin.page, `${API.documents}/${r2Doc?.id}`, {
    method: "PUT", body: { effective_from: "2026-05-16", effective_to: "2026-07-16", confirm_overwrite: true },
  });

  // The override must leave an audit trail.
  const audit = await apiJson(admin.page, `${API.audits}?keyword=${encodeURIComponent(TAG)}&page_size=50`);
  const auditItems = listOf(audit);
  const validityAudit = auditItems.filter(a => (a.action || "").includes("validity") || (a.detail || "").includes(TAG));

  recordCase(cases, caseRow({
    id: "EGO-VF-R2-OVERWRITE",
    phase: ROUND, owner: "B", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: "a document whose validity window is being widened",
    expected: ["不带 confirm_overwrite 的改动被阻止",
               "带 confirm_overwrite + 原因可以通过",
               "只带 confirm_overwrite 不填原因被拒绝",
               "强制覆盖写入审计"],
    actual: {
      blocked: { status: blocked.status, code: blocked.body?.error?.code ?? null },
      confirmed: confirmed.status,
      reasonMissing: { status: reasonMissing.status, code: reasonMissing.body?.error?.code ?? null },
      auditHits: validityAudit.length,
    },
    status: blocked.status === 409 && confirmed.status === 200
      && (reasonMissing.status === 400 || reasonMissing.status === 409)
      && validityAudit.length > 0 ? "PASS" : "FAIL",
    severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-09-validity-files", path: "/api/v1/documents/{id}" },
  }));

  // ------------------------------------------------------------------
  // F. 替换文件：旧文件进回收站、版本号 +1、不覆盖
  // ------------------------------------------------------------------
  const replaceTitle = `${TAG}-替换`;
  await createDoc(admin.page, {
    title: replaceTitle, effective_from: "2026-01-01", effective_to: null, audience_scope: "all",
  });
  const replaceDoc = await findDoc(admin.page, replaceTitle);

  // Upload the first main file through the real form.
  //
  // The form is rendered by JS from the document payload, so `goto` resolving on
  // `load` is not enough: wait for the edit page's own form id first, then for
  // the upload control it injects.
  await goto(admin.page, ORIGINS.admin, `/admin/documents/${replaceDoc?.id}/edit`);
  await admin.page.waitForSelector("#doc-form", { timeout: 20000 });
  await admin.page.waitForSelector("#upload-input", { state: "attached", timeout: 20000 });
  await admin.page.setInputFiles("#upload-input", [`${UPLOAD}/员工手册2026.pdf`]);
  await admin.page.click("#upload-submit");
  await admin.page.waitForTimeout(3500);
  const afterFirst = await apiJson(admin.page, `${API.documents}/${replaceDoc?.id}`);

  // Replace it with a DOCX: revision must increase and the old file must land in
  // the recycle bin rather than being overwritten.
  await admin.page.waitForSelector("#upload-input", { state: "attached", timeout: 20000 });
  await admin.page.setInputFiles("#upload-input", [`${UPLOAD}/公司制度.docx`]);
  await admin.page.click("#upload-submit");
  await admin.page.waitForTimeout(4000);
  const afterSecond = await apiJson(admin.page, `${API.documents}/${replaceDoc?.id}`);
  const recycle = await apiJson(admin.page, `${API.recycle}?reason=replaced&page_size=50`);
  const replacedEntries = listOf(recycle).filter(r => (r.original_filename || "").includes("员工手册"));

  recordCase(cases, caseRow({
    id: "EGO-VF-REPLACE-FILE",
    phase: ROUND, owner: "A", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: "a document with a PDF main file, replaced by a DOCX",
    expected: ["替换后当前文件变为新文件", "版本号 +1", "旧文件进入回收站（reason=replaced），不被覆盖"],
    actual: {
      first: { status: afterFirst.status, rev: afterFirst.body?.rev_count ?? null, file: afterFirst.body?.current_file?.original_filename ?? null },
      second: { status: afterSecond.status, rev: afterSecond.body?.rev_count ?? null, file: afterSecond.body?.current_file?.original_filename ?? null },
      recycleHits: replacedEntries.length,
    },
    status: afterFirst.body?.current_file?.original_filename === "员工手册2026.pdf"
      && afterSecond.body?.current_file?.original_filename === "公司制度.docx"
      && Number(afterSecond.body?.rev_count) > Number(afterFirst.body?.rev_count)
      && replacedEntries.length > 0 ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "vf-replace.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-09-validity-files", path: "/admin/documents/{id}/edit" },
  }));

  // ------------------------------------------------------------------
  // G. 多图合成一份多页 PDF（一次选 3 张）
  // ------------------------------------------------------------------
  const mergeTitle = `${TAG}-多图`;
  await createDoc(admin.page, {
    title: mergeTitle, effective_from: "2026-01-01", effective_to: null, audience_scope: "all",
  });
  const mergeDoc = await findDoc(admin.page, mergeTitle);

  // Drive the real upload control: the browser builds the multipart body itself,
  // which is what the shipped code path expects (several files, all images).
  await goto(admin.page, ORIGINS.admin, `/admin/documents/${mergeDoc?.id}/edit`);
  await admin.page.waitForSelector("#doc-form", { timeout: 20000 });
  await admin.page.waitForSelector("#upload-input", { state: "attached", timeout: 20000 });
  await admin.page.setInputFiles("#upload-input", [
    `${UPLOAD}/测试图片.png`,
    `${UPLOAD}/测试图片.png`,
    `${UPLOAD}/测试图片.png`,
  ]);
  await admin.page.click("#upload-submit");
  await admin.page.waitForTimeout(5000);
  const merged = await apiJson(admin.page, `${API.documents}/${mergeDoc?.id}`);

  recordCase(cases, caseRow({
    id: "EGO-VF-MULTI-IMAGE-MERGE",
    phase: ROUND, owner: "A", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: "three images selected at once in the upload control",
    expected: ["三张图片合成一份多页 PDF", "文件名体现合并（含张数）", "仍只有一个当前文件"],
    actual: {
      apiStatus: merged.status,
      currentFile: merged.body?.current_file?.original_filename ?? null,
      rev: merged.body?.rev_count ?? null,
    },
    status: (merged.body?.current_file?.original_filename || "").endsWith(".pdf")
      && /3/.test(merged.body?.current_file?.original_filename || "") ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "vf-multi-image.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-09-validity-files", path: "/admin/documents/{id}/edit" },
  }));

  // ------------------------------------------------------------------
  // H. 清理 + 控制台
  // ------------------------------------------------------------------
  const docsNow = listOf(await apiJson(admin.page, `${API.documents}?page_size=100`));
  const swept = [];
  for (const d of docsNow.filter(x => (x.title || "").startsWith("E2E-VF-"))) {
    swept.push({ title: d.title, status: (await apiJson(admin.page, `${API.documents}/${d.id}`, { method: "DELETE" })).status });
  }
  await writeJson(`${OUT}/ego/results/${ROUND}/cleanup.json`, swept);

  const events = await drainEvents(admin.page);
  const clean = events.consoleErrors.length === 0 && events.pageErrors.length === 0;
  recordCase(cases, caseRow({
    id: "EGO-VF-CONSOLE",
    phase: ROUND, owner: "F", role: "admin", mutability: "READ_SHARED",
    precondition: "validity and file flows exercised in the browser",
    expected: ["no console error and no pageerror"],
    actual: events,
    status: clean ? "PASS" : "FAIL",
    severity: "P1",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/${ROUND}/events.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-09-validity-files" },
  }));
  await writeJson(`${OUT}/ego/results/${ROUND}/events.json`, events);
  await writeJson(`${OUT}/ego/results/${ROUND}/pages.json`, pages);
}

try {
  await main();
  await disposePages(ROUND, ["admin", "user-a"]);
  await writeResult(ROUND, cases, pages);
} catch (error) {
  recordCase(cases, caseRow({
    id: "EGO-VF-RUNNER", phase: ROUND, owner: "B", role: "system", mutability: "GLOBAL_WRITE",
    precondition: "ego runtime", expected: ["round completes"],
    actual: { error: String(error), stack: error?.stack || null },
    status: "ERROR", severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-09-validity-files" },
  }));
  await disposePages(ROUND, ["admin", "user-a"]);
  await writeResult(ROUND, cases, pages);
  process.exitCode = 1;
}
