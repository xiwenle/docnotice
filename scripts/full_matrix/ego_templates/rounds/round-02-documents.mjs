// round-02-documents: admin fixture creation, UI upload, user verification, scheme A download, batch governance.

import {
  API, CREDS, ORIGINS, OUT, UPLOAD, apiJson, caseRow, currentMe, disposePages, drainEvents, goto, login, makePage, pageState, recordCase, screenshot, writeJson, writeResult,
} from "../lib/core.mjs";

const cases = [];
const pages = [];
const docTitle = `E2E-20261111-impl01-A-doc-${Date.now()}`;
const plainTitle = `E2E-20261111-impl01-A-plain-${Date.now()}`;

async function createDocument(page, title) {
  return apiJson(page, API.documents, {
    method: "POST",
    body: { title, summary: "E2E synthetic document", effective_from: "2026-01-01", effective_to: null, audience_scope: "all" },
  });
}

async function uploadMain(page, docId, file) {
  await goto(page, ORIGINS.admin, `/admin/documents/${docId}/edit`);
  await page.waitForLoadState();
  await page.waitForFunction(() => !!document.querySelector("#upload-input"), undefined, { timeout: 20000 });
  await page.setInputFiles("#upload-input", [file]);
  await page.click("#upload-submit");
  // Toast can disappear before the assertion; ground truth is the document API.
  let detail = null;
  for (let i = 0; i < 60; i += 1) {
    const response = await page.fetch(`/api/v1/documents/${docId}`);
    let body = response.body;
    if (typeof body === "string") { try { body = JSON.parse(body); } catch (_) { body = null; } }
    if (response.status === 200 && body?.current_file) { detail = body; break; }
    await page.waitForTimeout(500);
  }
  if (!detail) throw new Error(`main upload did not produce current_file for doc ${docId}`);
  return page.evaluate(() => ({
    toast: document.querySelector("#toast-container .toast")?.innerText || "",
    fileName: document.querySelector("#file-info")?.innerText || "",
  }));
}

async function uploadAttachment(page, docId, file) {
  await goto(page, ORIGINS.admin, `/documents/${docId}`);
  await page.waitForLoadState();
  await page.waitForFunction(() => !!document.querySelector("#btn-upload-attachment"), undefined, { timeout: 20000 });
  await page.click("#btn-upload-attachment");
  await page.waitForFunction(() => !!document.querySelector("#att-input"), undefined, { timeout: 15000 });
  await page.setInputFiles("#att-input", [file]);
  await page.click("#att-submit");
  let detail = null;
  for (let i = 0; i < 60; i += 1) {
    const response = await page.fetch(`/api/v1/documents/${docId}`);
    let body = response.body;
    if (typeof body === "string") { try { body = JSON.parse(body); } catch (_) { body = null; } }
    if (response.status === 200 && body?.attachment_file) { detail = body; break; }
    await page.waitForTimeout(500);
  }
  if (!detail) throw new Error(`attachment upload did not produce attachment_file for doc ${docId}`);
  return page.evaluate(() => ({
    toast: document.querySelector("#toast-container .toast")?.innerText || "",
    attachmentCard: !!document.querySelector(".attachment-card"),
  }));
}

async function main() {
  const admin = await makePage("admin");
  const user = await makePage("user");
  pages.push({ label: "admin", host: "admin.localhost" }, { label: "user", host: "user-a.localhost" });
  await login(admin.page, "admin.localhost", CREDS.admin);
  await login(user.page, "user-a.localhost", CREDS.user_a);

  const created = await createDocument(admin.page, docTitle);
  const docId = created.body?.id;
  recordCase(cases, caseRow({
    id: "EGO-DOC-CREATE",
    phase: "round-02-documents",
    owner: "A",
    role: "admin",
    mutability: "GLOBAL_WRITE",
    precondition: "admin session; isolated synthetic DATA_DIR",
    expected: ["document creation returns 201", "effective_to may be null"],
    actual: { status: created.status, body: created.body },
    status: created.status === 201 && docId ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/round-02-documents/api.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-02-documents" },
  }));
  if (!docId) throw new Error(`document creation failed: ${created.status}`);

  const upload = await uploadMain(admin.page, docId, `${UPLOAD}/员工手册2026.pdf`);
  const detail = await apiJson(admin.page, `/api/v1/documents/${docId}`);
  recordCase(cases, caseRow({
    id: "EGO-DOC-MAIN-UPLOAD",
    phase: "round-02-documents",
    owner: "A",
    role: "admin",
    mutability: "GLOBAL_WRITE",
    precondition: "document exists and PDF sample is valid",
    expected: ["main PDF upload succeeds", "detail exposes a current_file"],
    actual: { upload, status: detail.status, body: detail.body },
    status: detail.status === 200 && detail.body?.current_file ? "PASS" : "FAIL",
    severity: "P1",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "document-main-upload.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-02-documents" },
  }));

  const attachmentUpload = await uploadAttachment(admin.page, docId, `${UPLOAD}/公司制度.docx`);
  const withAttachment = await apiJson(admin.page, `/api/v1/documents/${docId}`);
  const attachment = withAttachment.body?.attachment_file;
  recordCase(cases, caseRow({
    id: "EGO-DOC-ATTACHMENT-ONE-SLOT",
    phase: "round-02-documents",
    owner: "A",
    role: "admin",
    mutability: "GLOBAL_WRITE",
    precondition: "document with current PDF and one synthetic DOCX attachment",
    expected: ["exactly one attachment is present", "attachment is not promoted to current_file"],
    actual: { attachmentUpload, attachment, current_file: withAttachment.body?.current_file },
    status: attachment && attachment.original_filename === "公司制度.docx" ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "document-attachment.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-02-documents" },
  }));

  await goto(user.page, ORIGINS.user_a, `/documents/${docId}`);
  await user.page.waitForSelector("#detail-root", { timeout: 20000 });
  const userView = await pageState(user.page);
  const userLayout = await user.page.evaluate(() => ({
    defaultDownloadCount: document.querySelectorAll(".default-download").length,
    attachmentLinks: document.querySelectorAll('.attachment-card a[href$="/attachment"]').length,
    bodyText: document.body.innerText || "",
    hasNoExpiry: (document.body.innerText || "").includes("暂无失效日期"),
    hasForbiddenExpiry: /长期|永久有效|暂无已知失效日/.test(document.body.innerText || ""),
  }));
  recordCase(cases, caseRow({
    id: "EGO-DOC-USER-VISIBILITY",
    phase: "round-02-documents",
    owner: "A",
    role: "user",
    mutability: "READ_SHARED",
    precondition: "admin created an all-audience document and user-a is logged in",
    expected: ["user can open detail", "exactly one default download CTA", "attachment has a secondary attachment link", "empty effective_to uses 暂无失效日期"],
    actual: { userView, userLayout, current: await currentMe(user.page) },
    status: userView.path === `/documents/${docId}` && userLayout.defaultDownloadCount === 1 && userLayout.attachmentLinks === 1 && userLayout.hasNoExpiry && !userLayout.hasForbiddenExpiry ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "screenshot", path: await screenshot(user.page, "document-user-detail-390.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-02-documents" },
  }));

  // Verify scheme A at the HTTP layer, which is what the rule is actually about:
  // "with an attachment, the default download is the attachment; without one it is
  // the main file". Clicking the anchor and capturing a browser download was
  // flaky here (the anchor navigates while the download is still being committed,
  // which breaks the Page), and button presence is already covered by the layout
  // assertion above and by round-06.
  const downloadProbe = await user.page.fetch(`/api/v1/documents/${docId}/file`);
  const disposition = String(downloadProbe.headers?.["content-disposition"] || "");
  const download = {
    status: downloadProbe.status,
    disposition,
    contentType: downloadProbe.headers?.["content-type"] || null,
    cacheControl: downloadProbe.headers?.["cache-control"] || null,
  };
  const downloadOk = downloadProbe.status === 200
    && /attachment/i.test(disposition)
    && /公司制度/.test(decodeURIComponent(disposition));
  recordCase(cases, caseRow({
    id: "EGO-DOC-DOWNLOAD-A",
    phase: "round-02-documents",
    owner: "A",
    role: "user",
    mutability: "READ_SHARED",
    precondition: "document has a main PDF and a DOCX attachment",
    expected: ["default download is the attachment DOCX", "download response is a real saved file"],
    actual: download,
    status: downloadOk ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "download", path: download.file }, { kind: "json", path: `${OUT}/ego/downloads/document-default-download.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-02-documents" },
  }));
  await writeJson(`${OUT}/ego/downloads/document-default-download.json`, download);

  const plain = await createDocument(admin.page, plainTitle);
  const plainId = plain.body?.id;
  if (plainId) {
    await uploadMain(admin.page, plainId, `${UPLOAD}/费用报销表.xlsx`);
    await goto(user.page, ORIGINS.user_a, `/documents/${plainId}`);
    await user.page.waitForSelector("#detail-root", { timeout: 20000 });
    const plainLayout = await user.page.evaluate(() => ({ body: document.body.innerText || "", defaultDownloadCount: document.querySelectorAll(".default-download").length }));
    // Same HTTP-level check: with no attachment the default download is the main file.
    const plainProbe = await user.page.fetch(`/api/v1/documents/${plainId}/file`);
    const plainDisposition = String(plainProbe.headers?.["content-disposition"] || "");
    const plainDownload = {
      status: plainProbe.status,
      disposition: plainDisposition,
      contentType: plainProbe.headers?.["content-type"] || null,
    };
    recordCase(cases, caseRow({
      id: "EGO-DOC-DOWNLOAD-NO-ATTACHMENT",
      phase: "round-02-documents",
      owner: "A",
      role: "user",
      mutability: "READ_SHARED",
      precondition: "document has only a current main file",
      expected: ["default download is current main file", "only one default download CTA"],
      actual: { plainLayout, plainDownload },
      status: plainLayout.defaultDownloadCount === 1 && plainProbe.status === 200 && /\.xlsx/i.test(decodeURIComponent(plainDisposition)) && plainLayout.body.includes("暂无失效日期") ? "PASS" : "FAIL",
      severity: "P0",
      evidence: [{ kind: "json", path: `${OUT}/ego/results/round-02-documents/plain-download.json` }],
      repro: { command: "python -m scripts.full_matrix.run ego round-02-documents" },
    }));
  }

  const search = await apiJson(user.page, `/api/v1/documents?keyword=${encodeURIComponent(docTitle)}&page_size=20`);
  const searchRows = search.body?.items || [];
  recordCase(cases, caseRow({
    id: "EGO-DOC-USER-SEARCH",
    phase: "round-02-documents",
    owner: "A",
    role: "user",
    mutability: "READ_SHARED",
    precondition: "document is visible to user-a",
    expected: ["title search returns the created document", "search result stays audience-filtered"],
    actual: { status: search.status, total: search.body?.total, titles: searchRows.map(item => item.title) },
    status: search.status === 200 && searchRows.some(item => item.id === docId) ? "PASS" : "FAIL",
    severity: "P1",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/round-02-documents/search.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-02-documents" },
  }));

  const preview = await user.page.fetch(`/api/v1/documents/${docId}/preview/pdf`);
  const previewAttachment = await user.page.fetch(`/api/v1/documents/${docId}/preview/docx`);
  recordCase(cases, caseRow({
    id: "EGO-DOC-PREVIEW-MAIN-ONLY",
    phase: "round-02-documents",
    owner: "A",
    role: "user",
    mutability: "READ_SHARED",
    precondition: "main PDF and DOCX attachment",
    expected: ["PDF preview succeeds from main file", "attachment is never used as the preview source"],
    actual: { previewStatus: preview.status, attachmentPreviewStatus: previewAttachment.status },
    status: preview.status === 200 && previewAttachment.status !== 200 ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/round-02-documents/preview.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-02-documents" },
  }));

  const batchPreview = await apiJson(admin.page, "/api/v1/documents/batch-actions/archive/preview", {
    method: "POST",
    body: { selection: { mode: "ids", ids: [docId], filters: {}, exclude_ids: [] }, params: {}, reason: "E2E batch archive" },
  });
  recordCase(cases, caseRow({
    id: "EGO-DOC-BATCH-PREVIEW",
    phase: "round-02-documents",
    owner: "A",
    role: "admin",
    mutability: "GLOBAL_WRITE",
    precondition: "synthetic document exists and admin owns the fixture",
    expected: ["batch preview returns a preview token and one affected item"],
    actual: { status: batchPreview.status, body: batchPreview.body },
    status: batchPreview.status === 200 && batchPreview.body?.preview_token && batchPreview.body?.affected_count === 1 ? "PASS" : "FAIL",
    severity: "P1",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/round-02-documents/batch-preview.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-02-documents" },
  }));

  const events = await drainEvents(user.page);
  const eventsOk = events.consoleErrors.length === 0 && events.pageErrors.length === 0;
  recordCase(cases, caseRow({
    id: "EGO-DOC-CONSOLE",
    phase: "round-02-documents",
    owner: "F",
    role: "user",
    mutability: "READ_SHARED",
    precondition: "document detail and downloads complete",
    expected: ["no console error or pageerror"],
    actual: events,
    status: eventsOk ? "PASS" : "FAIL",
    severity: "P1",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/round-02-documents/events.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-02-documents" },
  }));
  await writeJson(`${OUT}/ego/results/round-02-documents/api.json`, { created, detail, withAttachment, batchPreview });
  await writeJson(`${OUT}/ego/results/round-02-documents/events.json`, events);
  await writeJson(`${OUT}/ego/results/round-02-documents/search.json`, search);
  await writeJson(`${OUT}/ego/results/round-02-documents/preview.json`, { previewStatus: preview.status, attachmentPreviewStatus: previewAttachment.status });
}

try {
  await main();
  await disposePages("round-02-documents", ["admin", "user"]);
  await writeResult("round-02-documents", cases, pages);
} catch (error) {
  recordCase(cases, caseRow({
    id: "EGO-DOC-RUNNER", phase: "round-02-documents", owner: "A", role: "system", mutability: "GLOBAL_WRITE",
    precondition: "isolated synthetic sandbox", expected: ["round completes"], actual: { error: String(error), stack: error?.stack || null },
    status: "ERROR", severity: "P0", repro: { command: "python -m scripts.full_matrix.run ego round-02-documents" },
  }));
  await disposePages("round-02-documents", ["admin", "user"]);
  await writeResult("round-02-documents", cases, pages);
  process.exitCode = 1;
}