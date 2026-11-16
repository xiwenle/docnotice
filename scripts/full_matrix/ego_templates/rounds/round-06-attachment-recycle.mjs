// round-06-attachment-recycle: attachment delete/restore, archive bundle, recycle UI, permissions.
// Three Pages in one TaskSpace; leave the TaskSpace open for later rounds.

import { API, CREDS, ORIGINS, RUN_ID, TASK_SPACE, UPLOAD, apiJson, caseRow,
  currentMe, disposePages, drainEvents, goto, login, makePage, modalConfirm, pageState,
  recordCase, safeText, sha256File, summarize, writeJson } from "../lib/core.mjs";

const fs = await import("node:fs/promises");
const path = await import("node:path");

export const OUT = "__FULL_MATRIX_EVIDENCE_ROOT__";
const ROUND = "round-06-attachment-recycle";
const cases = [];
const pages = [];
const tag = `E2E-20261111-impl01-ATT-${Date.now()}`;

function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
async function ensureDir(dir) { await fs.mkdir(dir, { recursive: true }); }

async function shot(page, name) {
  const file = `${OUT}/ego/screenshots/${name}`;
  await ensureDir(path.dirname(file));
  try { await page.screenshot({ path: file, fullPage: false }); return file; } catch (_) { return null; }
}

async function saveDownload(page, filename, selector) {
  const pending = page.waitForEvent("download", { timeout: 30000 });
  await page.click(selector, { label: "下载" });
  const download = await pending;
  const file = `${OUT}/ego/downloads/${filename}`;
  await ensureDir(path.dirname(file));
  await download.saveAs(file);
  const stat = await fs.stat(file);
  return {
    file,
    suggestedFilename: download.suggestedFilename?.() || filename,
    size: stat.size,
    sha256: await sha256File(file),
    url: safeText(download.url?.() || ""),
  };
}

async function fetchStatus(page, route, options = {}) {
  const response = await page.fetch(route, options);
  return { status: response.status, body: response.body, headers: response.headers || null };
}

async function apiDetail(page, docId) {
  const result = await apiJson(page, `/api/v1/documents/${docId}`);
  return { status: result.status, body: result.body || null };
}

async function pollDocument(page, docId, predicate, attempts = 60) {
  for (let i = 0; i < attempts; i += 1) {
    const result = await apiDetail(page, docId);
    if (result.status === 200 && predicate(result.body || {})) return result.body;
    await page.waitForTimeout(500);
  }
  return null;
}

async function createDoc(page, title, effectiveTo = null) {
  const result = await apiJson(page, API.documents, {
    method: "POST",
    body: { title, summary: "round-06 synthetic fixture", effective_from: "2026-01-01", effective_to: effectiveTo, audience_scope: "all" },
  });
  return { status: result.status, body: result.body || null };
}

async function uploadMain(page, docId, file) {
  await goto(page, ORIGINS.admin, `/admin/documents/${docId}/edit`);
  await page.waitForLoadState();
  await page.waitForFunction(() => !!document.querySelector("#upload-input"), undefined, { timeout: 25000 });
  await page.setInputFiles("#upload-input", [file]);
  await page.click("#upload-submit");
  const detail = await pollDocument(page, docId, body => !!body.current_file);
  if (!detail) throw new Error(`main upload pending for ${docId}`);
  return detail;
}

async function uploadAttachment(page, docId, file, expectedName) {
  await goto(page, ORIGINS.admin, `/documents/${docId}`);
  await page.waitForLoadState();
  await page.waitForFunction(() => !!document.querySelector("#btn-upload-attachment"), undefined, { timeout: 25000 });
  await page.click("#btn-upload-attachment");
  await page.waitForFunction(() => !!document.querySelector("#att-input"), undefined, { timeout: 20000 });
  await page.setInputFiles("#att-input", [file]);
  await page.click("#att-submit");
  const detail = await pollDocument(page, docId, body => !!body.attachment_file && (!expectedName || body.attachment_file.original_filename === expectedName));
  if (!detail) throw new Error(`attachment upload pending for ${docId}`);
  return detail;
}

async function replaceAttachment(page, docId, file, expectedName) {
  await goto(page, ORIGINS.admin, `/documents/${docId}`);
  await page.waitForLoadState();
  await page.waitForFunction(() => !!document.querySelector("#btn-replace-attachment"), undefined, { timeout: 25000 });
  await page.click("#btn-replace-attachment");
  await page.waitForFunction(() => !!document.querySelector("#att-input"), undefined, { timeout: 20000 });
  await page.setInputFiles("#att-input", [file]);
  await page.click("#att-submit");
  const detail = await pollDocument(page, docId, body => !!body.attachment_file && (!expectedName || body.attachment_file.original_filename === expectedName));
  if (!detail) throw new Error(`attachment replace pending for ${docId}`);
  return detail;
}

async function deleteAttachment(page, docId) {
  await goto(page, ORIGINS.admin, `/documents/${docId}`);
  await page.waitForLoadState();
  await page.waitForFunction(() => !!document.querySelector("#btn-delete-attachment"), undefined, { timeout: 25000 });
  await page.click("#btn-delete-attachment");
  await modalConfirm(page, { reason: "" });
  const detail = await pollDocument(page, docId, body => !body.attachment_file);
  if (!detail) throw new Error(`attachment delete pending for ${docId}`);
  return detail;
}

async function archiveDoc(page, docId) {
  await goto(page, ORIGINS.admin, `/documents/${docId}`);
  await page.waitForLoadState();
  await page.waitForFunction(() => !!document.querySelector("#btn-archive"), undefined, { timeout: 25000 });
  await page.click("#btn-archive");
  await modalConfirm(page, { reason: "round-06 archive" });
  const detail = await pollDocument(page, docId, body => body.archived === true || body.status === "archived");
  if (!detail) throw new Error(`archive pending for ${docId}`);
  return detail;
}

async function recycleItems(page, reason = "") {
  const suffix = reason ? `&reason=${encodeURIComponent(reason)}` : "";
  const result = await apiJson(page, `/api/v1/recycle-bin?page=1&page_size=100${suffix}`);
  return result.body?.items || [];
}

async function noticeItems(page) {
  const result = await apiJson(page, "/api/v1/recycle-bin/notices");
  return result.body?.items || [];
}

async function openRecycle(page) {
  await goto(page, ORIGINS.admin, "/admin/recycle-bin");
  await page.waitForSelector("#rb-tbody tr", { timeout: 20000 });
  await page.waitForTimeout(1000);
}

async function rowIndex(page, tbody, hint) {
  return page.evaluate(({ tbody, hint }) => {
    const rows = Array.from(document.querySelectorAll(`${tbody} tr`));
    return rows.findIndex(row => (row.innerText || "").includes(hint));
  }, { tbody, hint });
}

async function clickRowAction(page, tbody, hint, action) {
  const index = await rowIndex(page, tbody, hint);
  if (index < 0) throw new Error(`row not found: ${hint}`);
  const selector = `${tbody} tr:nth-child(${index + 1}) button[data-action="${action}"]`;
  // Recycle tables can be taller than the viewport; ego page.click refuses
  // elements outside it. Scroll first, then fall back to a DOM click.
  await page.evaluate(({ selector }) => {
    const el = document.querySelector(selector);
    if (el) el.scrollIntoView({ block: "center", inline: "nearest" });
  }, { selector });
  try {
    await page.click(selector, { label: action });
  } catch (_) {
    const clicked = await page.evaluate(({ selector }) => {
      const el = document.querySelector(selector);
      if (!el) return false;
      el.click();
      return true;
    }, { selector });
    if (!clicked) throw new Error(`row action not found: ${action}`);
  }
}

async function createNotice(page, title) {
  const result = await apiJson(page, API.notices, {
    method: "POST",
    body: { title, content: "round-06 synthetic notice", notice_type: "发布", target: { scope: "all" } },
  });
  return { status: result.status, body: result.body || null };
}

async function cancelNotice(page, noticeId) {
  return apiJson(page, `/api/v1/notices/${noticeId}/cancel`, { method: "POST", body: { reason: "round-06 recycle fixture" } });
}

async function auditLogs(page, action) {
  const result = await apiJson(page, `/api/v1/audit-logs?page=1&page_size=100&action=${encodeURIComponent(action)}`);
  return result.body || {};
}

function result(id, owner, role, mutability, precondition, expected, actual, passed, severity = "P1", evidence = [], depends_on = []) {
  return caseRow({ id, phase: ROUND, owner, role, mutability, precondition, depends_on, expected, actual, status: passed ? "PASS" : "FAIL", severity, evidence, repro: { command: "python -m scripts.full_matrix.run ego round-06-attachment-recycle" } });
}

async function runCase(id, owner, role, mutability, precondition, expected, body, severity = "P1", depends_on = []) {
  const started = Date.now();
  try {
    const value = await body();
    const row = result(id, owner, role, mutability, precondition, expected, value.actual, value.pass, severity, value.evidence || [], depends_on);
    row.duration_ms = Date.now() - started;
    recordCase(cases, row);
  } catch (error) {
    const row = result(id, owner, role, mutability, precondition, expected,
      { error: safeText(String(error)), stack: safeText(error?.stack || "") }, false, severity,
      [{ kind: "screenshot", path: await shot(pages.user?.page || pages.admin?.page, `${id}.png`) }], depends_on);
    row.status = "ERROR";
    row.duration_ms = Date.now() - started;
    recordCase(cases, row);
  }
}

async function writeRoundResult() {
  // Hand every Page this round opened back to the runner.  The TaskSpace budget
  // is 8 Pages for the whole run, and a round that keeps its own Pages starves
  // the rounds after it (round-07 died on PageBudgetError before this existed).
  await disposePages(ROUND, ["admin", "user-a", "anon"]);
  await writeJson(`${OUT}/ego/results/${ROUND}/result.json`, {
    schemaVersion: "ego-test-result/v1", runId: RUN_ID, roundId: ROUND, taskSpace: TASK_SPACE,
    app: { origins: ORIGINS, generatedAt: new Date().toISOString() }, cases, summary: summarize(cases), pages,
  });
}
async function main() {
  const admin = await makePage("admin");
  const user = await makePage("user-a");
  const anon = await makePage("anon");
  pages.push({ label: "admin", host: "admin.localhost" }, { label: "user-a", host: "user-a.localhost" }, { label: "anon", host: "anon.localhost" });
  await login(admin.page, "admin.localhost", CREDS.admin);
  await login(user.page, "user-a.localhost", CREDS.user_a);
  await goto(anon.page, ORIGINS.anon, "/login");
  pages.admin = admin;
  pages.user = user;

  const docTitle = `${tag}-doc`;
  const mainName = "员工手册2026.pdf";
  const attachmentName = "公司制度.docx";
  const pptxName = "季度汇报.pptx";
  const created = await createDoc(admin.page, docTitle);
  if (![200, 201].includes(created.status)) throw new Error(`document create failed: ${created.status}`);
  const docId = created.body?.id;
  const mainDetail = await uploadMain(admin.page, docId, `${UPLOAD}/${mainName}`);
  const withAttachment = await uploadAttachment(admin.page, docId, `${UPLOAD}/${attachmentName}`, attachmentName);
  const mainSha = await sha256File(`${UPLOAD}/${mainName}`);
  const docxSha = await sha256File(`${UPLOAD}/${attachmentName}`);
  const pptxSha = await sha256File(`${UPLOAD}/${pptxName}`);
  await writeJson(`${OUT}/ego/results/${ROUND}/fixture.json`, { docId, docTitle, mainDetail, withAttachment, mainSha, docxSha, pptxSha });
  const state = {};

  await runCase("EGO-ATT-SCHEME-A", "A", "admin+user", "GLOBAL_WRITE",
    "admin uploads a main PDF and a DOCX attachment; user-a is logged in",
    ["user can open the all-audience document", "preview uses the main PDF", "default download is the DOCX attachment", "one default CTA and one secondary attachment link", "empty effective_to shows 暂无失效日期"],
    async () => {
      const detail = await apiJson(admin.page, `/api/v1/documents/${docId}`);
      await goto(user.page, ORIGINS.user_a, `/documents/${docId}`);
      await user.page.waitForSelector(".attachment-card", { timeout: 20000 });
      const layout = await user.page.evaluate(() => ({
        defaultCount: document.querySelectorAll(".default-download").length,
        attachmentLinks: document.querySelectorAll('.attachment-card a[href$="/attachment"]').length,
        bodyText: document.body.innerText || "",
        pdfFrame: !!document.querySelector("#preview-body iframe.pdf-frame"),
      }));
      const download = await saveDownload(user.page, "round06-default-docx.bin", ".default-download");
      const preview = await fetchStatus(user.page, `/api/v1/documents/${docId}/preview/pdf`);
      const attachment = await fetchStatus(user.page, `/api/v1/documents/${docId}/attachment`);
      const actual = { detailStatus: detail.status, attachmentFile: detail.body?.attachment_file, layout, download, previewStatus: preview.status, attachmentStatus: attachment.status };
      const pass = detail.status === 200 && detail.body?.attachment_file?.file_type === "docx"
        && layout.defaultCount === 1 && layout.attachmentLinks === 1 && layout.pdfFrame
        && download.suggestedFilename.includes("公司制度") && /\.docx$/i.test(download.suggestedFilename)
        && download.sha256 === docxSha && preview.status === 200 && attachment.status === 200
        && layout.bodyText.includes("暂无失效日期") && !/长期|永久有效|暂无已知失效日/.test(layout.bodyText);
      return { actual, pass, evidence: [{ kind: "download", path: download.file }, { kind: "screenshot", path: await shot(user.page, "round06-scheme-a.png") }] };
    }, "P0");

  await runCase("EGO-ATT-REPLACE-PPTX", "A", "admin+user", "GLOBAL_WRITE",
    "DOCX attachment exists; admin replaces it with a PPTX through the attachment UI",
    ["PPTX attachment is active", "old attachment appears as replaced in recycle bin", "user default download changes to PPTX", "main PDF preview is unchanged"],
    async () => {
      const replacedDetail = await replaceAttachment(admin.page, docId, `${UPLOAD}/${pptxName}`, pptxName);
      const replaced = await recycleItems(admin.page, "replaced");
      const replacedEntry = replaced.find(item => String(item.document_id) === String(docId));
      await goto(user.page, ORIGINS.user_a, `/documents/${docId}`);
      await user.page.waitForSelector(".default-download", { timeout: 20000 });
      const mainText = await user.page.evaluate(() => document.querySelector(".default-download")?.innerText || "");
      const download = await saveDownload(user.page, "round06-replaced-pptx.bin", ".default-download");
      const preview = await fetchStatus(user.page, `/api/v1/documents/${docId}/preview/pdf`);
      const actual = { replacedStatus: replacedDetail.attachment_file, replacedEntry, mainText, download, previewStatus: preview.status };
      const pass = replacedDetail.attachment_file?.file_type === "pptx" && !!replacedEntry
        && mainText.includes("季度汇报.pptx") && /\.pptx$/i.test(download.suggestedFilename || "")
        && preview.status === 200 && download.sha256 !== docxSha;
      return { actual, pass, evidence: [{ kind: "download", path: download.file }, { kind: "screenshot", path: await shot(user.page, "round06-replaced.png") }] };
    }, "P0", ["EGO-ATT-SCHEME-A"]);

  await runCase("EGO-ATT-DELETE", "A", "admin+user", "GLOBAL_WRITE",
    "active PPTX attachment exists; admin deletes it through the attachment card",
    ["attachment is detached", "attachment_deleted record is visible", "user default download falls back to the main PDF", "attachment direct endpoint becomes 404"],
    async () => {
      await deleteAttachment(admin.page, docId);
      const detail = await apiDetail(admin.page, docId);
      const bin = await recycleItems(admin.page, "attachment_deleted");
      state.attachmentEntry = bin.find(item => String(item.document_id) === String(docId));
      await goto(user.page, ORIGINS.user_a, `/documents/${docId}`);
      await user.page.waitForSelector(".default-download", { timeout: 20000 });
      const mainText = await user.page.evaluate(() => document.querySelector(".default-download")?.innerText || "");
      const download = await saveDownload(user.page, "round06-after-delete.bin", ".default-download");
      const attachment = await fetchStatus(user.page, `/api/v1/documents/${docId}/attachment`);
      const actual = { detailAttachment: detail.body?.attachment_file, binEntry: state.attachmentEntry, mainText, download, attachmentStatus: attachment.status };
      const pass = !detail.body?.attachment_file && !!state.attachmentEntry && mainText.includes("主文件")
        && /\.pdf$/i.test(download.suggestedFilename || "") && download.sha256 === mainSha && attachment.status === 404;
      return { actual, pass, evidence: [{ kind: "download", path: download.file }, { kind: "screenshot", path: await shot(user.page, "round06-deleted.png") }] };
    }, "P0", ["EGO-ATT-REPLACE-PPTX"]);

  await runCase("EGO-ATT-RESTORE-DETACHED", "A", "admin+user", "GLOBAL_WRITE",
    "attachment_deleted entry exists in recycle bin",
    ["restore succeeds from the recycle-bin page", "deleted attachment is reattached to the document", "attachment endpoint returns the restored PPTX", "default download returns the restored PPTX", "preview still uses the main PDF"],
    async () => {
      await openRecycle(admin.page);
      await clickRowAction(admin.page, "#rb-tbody", docTitle, "restore");
      await modalConfirm(admin.page, { reason: "round-06 restore detached file" });
      await admin.page.waitForTimeout(1800);
      const remaining = (await recycleItems(admin.page, "attachment_deleted")).filter(item => String(item.document_id) === String(docId));
      const detail = await apiDetail(admin.page, docId);
      const attachment = await fetchStatus(user.page, `/api/v1/documents/${docId}/attachment`);
      const download = await saveDownload(user.page, "round06-restored-detached.bin", ".default-download");
      const actual = { remaining, detailAttachment: detail.body?.attachment_file, attachmentStatus: attachment.status, download };
      const pass = remaining.length === 0 && !!detail.body?.attachment_file && attachment.status === 200 && download.sha256 === pptxSha;
      return { actual, pass, evidence: [{ kind: "download", path: download.file }, { kind: "screenshot", path: await shot(admin.page, "round06-restored-detached.png") }] };
    }, "P0", ["EGO-ATT-DELETE"]);

  const doc2Title = `${tag}-archive`;
  const doc2Cs = await createDoc(admin.page, doc2Title);
  if (doc2Cs.status !== 201) throw new Error(`archive fixture create failed: ${doc2Cs.status}`);
  const doc2Id = doc2Cs.body.id;
  await uploadMain(admin.page, doc2Id, `${UPLOAD}/${mainName}`);
  await uploadAttachment(admin.page, doc2Id, `${UPLOAD}/${attachmentName}`, attachmentName);
  state.doc2Id = doc2Id;

  await runCase("EGO-ARCHIVE-SINGLE", "A", "admin+user", "DESTRUCTIVE",
    "document has both a main PDF and a DOCX attachment",
    ["archive creates exactly one recycle-bin record", "record contains both file ids", "ordinary user cannot read or download the archived document"],
    async () => {
      await archiveDoc(admin.page, doc2Id);
      const archived = (await recycleItems(admin.page, "archived")).filter(item => String(item.document_id) === String(doc2Id));
      const userDetail = await fetchStatus(user.page, `/api/v1/documents/${doc2Id}`);
      const userFile = await fetchStatus(user.page, `/api/v1/documents/${doc2Id}/file`);
      const actual = { archived, userDetailStatus: userDetail.status, userFileStatus: userFile.status };
      const pass = archived.length === 1 && !!archived[0].file_record_id && !!archived[0].attachment_file_record_id
        && [403, 404].includes(userDetail.status) && [403, 404].includes(userFile.status);
      return { actual, pass, evidence: [{ kind: "screenshot", path: await shot(admin.page, "round06-archive-single.png") }] };
    }, "P0", ["EGO-ATT-SCHEME-A"]);

  await runCase("EGO-ARCHIVE-RESTORE-BUNDLE", "A", "admin+user", "DESTRUCTIVE",
    "one archived record contains both main and attachment",
    ["restore brings both files back", "document becomes visible to user", "default download points to DOCX", "preview stays on main PDF"],
    async () => {
      await openRecycle(admin.page);
      await clickRowAction(admin.page, "#rb-tbody", doc2Title, "restore");
      await modalConfirm(admin.page, { reason: "round-06 restore bundle" });
      await admin.page.waitForTimeout(2200);
      const detail = await apiDetail(admin.page, doc2Id);
      await goto(user.page, ORIGINS.user_a, `/documents/${doc2Id}`);
      await user.page.waitForSelector(".default-download", { timeout: 20000 });
      const mainText = await user.page.evaluate(() => document.querySelector(".default-download")?.innerText || "");
      const download = await saveDownload(user.page, "round06-restored-bundle.bin", ".default-download");
      const preview = await fetchStatus(user.page, `/api/v1/documents/${doc2Id}/preview/pdf`);
      const actual = { currentFile: detail.body?.current_file, attachmentFile: detail.body?.attachment_file, mainText, download, previewStatus: preview.status };
      const pass = !!detail.body?.current_file && !!detail.body?.attachment_file && mainText.includes("公司制度.docx")
        && /\.docx$/i.test(download.suggestedFilename || "") && download.sha256 === docxSha && preview.status === 200;
      return { actual, pass, evidence: [{ kind: "download", path: download.file }, { kind: "screenshot", path: await shot(user.page, "round06-restored-bundle.png") }] };
    }, "P0", ["EGO-ARCHIVE-SINGLE"]);
  const doc3Title = `${tag}-purge`;
  const doc3Cs = await createDoc(admin.page, doc3Title);
  if (doc3Cs.status !== 201) throw new Error(`purge fixture create failed: ${doc3Cs.status}`);
  const doc3Id = doc3Cs.body.id;
  await uploadMain(admin.page, doc3Id, `${UPLOAD}/${mainName}`);
  await uploadAttachment(admin.page, doc3Id, `${UPLOAD}/${attachmentName}`, attachmentName);
  await archiveDoc(admin.page, doc3Id);

  await runCase("EGO-RECYCLE-PURGE-CONFIRM", "E", "admin+user", "DESTRUCTIVE",
    "archived document has a recycle-bin entry",
    ["DELETE without confirm is rejected", "page permanent-delete requires confirmation", "entry disappears after confirm", "document and file endpoints return 404 afterwards"],
    async () => {
      const entries = await recycleItems(admin.page);
      const entry = entries.find(item => String(item.document_id) === String(doc3Id));
      if (!entry) throw new Error("purge fixture recycle entry missing");
      const noConfirm = await fetchStatus(admin.page, `/api/v1/recycle-bin/${entry.id}`, { method: "DELETE" });
      await openRecycle(admin.page);
      await clickRowAction(admin.page, "#rb-tbody", doc3Title, "purge");
      await admin.page.waitForSelector("#modal-root [data-confirm]", { timeout: 8000 });
      const dangerText = await admin.page.evaluate(() => document.querySelector("#modal-root")?.innerText || "");
      const dangerOk = dangerText.includes("不可恢复") || dangerText.includes("永久删除");
      await modalConfirm(admin.page, {});
      await admin.page.waitForTimeout(2200);
      const remaining = (await recycleItems(admin.page)).filter(item => String(item.document_id) === String(doc3Id));
      const detail = await fetchStatus(user.page, `/api/v1/documents/${doc3Id}`);
      const file = await fetchStatus(user.page, `/api/v1/documents/${doc3Id}/file`);
      const audit = await auditLogs(admin.page, "recycle_purge");
      const pass = dangerOk && noConfirm.status >= 400 && remaining.length === 0 && [403, 404].includes(detail.status) && [403, 404].includes(file.status) && (audit.total || 0) > 0;
      return { actual: { dangerText, noConfirmStatus: noConfirm.status, remaining, detailStatus: detail.status, fileStatus: file.status, auditTotal: audit.total || 0 }, pass, evidence: [{ kind: "screenshot", path: await shot(admin.page, "round06-purge-confirm.png") }] };
    }, "P0", ["EGO-ARCHIVE-SINGLE"]);

  const noticeTitle = `${tag}-notice-restore`;
  const noticeCs = await createNotice(admin.page, noticeTitle);
  if (noticeCs.status !== 201) throw new Error(`notice fixture create failed: ${noticeCs.status}`);
  const noticeId = noticeCs.body.id;
  await cancelNotice(admin.page, noticeId);

  await runCase("EGO-NOTICE-CANCEL-RESTORE", "B", "admin+user", "GLOBAL_WRITE",
    "published notice has been cancelled into the notice recycle bin",
    ["cancelled notice appears in the notice recycle section", "restore returns it to published", "detail becomes accessible"],
    async () => {
      await openRecycle(admin.page);
      await admin.page.waitForSelector("#notice-rb-tbody tr", { timeout: 15000 });
      const text = await admin.page.evaluate(() => document.querySelector("#notice-rb-tbody")?.innerText || "");
      await clickRowAction(admin.page, "#notice-rb-tbody", noticeTitle, "n-restore");
      await modalConfirm(admin.page, { reason: "" });
      await admin.page.waitForTimeout(1800);
      const detail = await apiJson(admin.page, `/api/v1/notices/${noticeId}`);
      const list = await apiJson(admin.page, "/api/v1/recycle-bin/notices");
      const userList = await apiJson(user.page, "/api/v1/notices?page=1&page_size=100");
      const userVisible = (userList.body?.items || []).some(item => item.id === noticeId);
      const pass = text.includes(noticeTitle) && detail.status === 200 && !(list.body?.items || []).some(item => item.id === noticeId) && userVisible;
      return { actual: { recycleText: text, detailStatus: detail.status, noticeBin: list.body?.items || [], userVisible }, pass, evidence: [{ kind: "screenshot", path: await shot(admin.page, "round06-notice-restored.png") }] };
    }, "P0");

  const notice2Title = `${tag}-notice-purge`;
  const notice2Cs = await createNotice(admin.page, notice2Title);
  if (notice2Cs.status !== 201) throw new Error(`notice purge fixture create failed: ${notice2Cs.status}`);
  const notice2Id = notice2Cs.body.id;
  await cancelNotice(admin.page, notice2Id);

  await runCase("EGO-NOTICE-PURGE", "B", "admin", "DESTRUCTIVE",
    "cancelled notice has a permanent-delete action",
    ["danger confirmation is shown", "notice disappears from recycle bin", "notice detail returns 404"],
    async () => {
      await openRecycle(admin.page);
      await admin.page.waitForSelector("#notice-rb-tbody tr", { timeout: 15000 });
      await clickRowAction(admin.page, "#notice-rb-tbody", notice2Title, "n-purge");
      await modalConfirm(admin.page, { reason: "" });
      await admin.page.waitForTimeout(1800);
      const detail = await apiJson(admin.page, `/api/v1/notices/${notice2Id}`);
      const list = await apiJson(admin.page, "/api/v1/recycle-bin/notices");
      const pass = detail.status === 404 && !(list.body?.items || []).some(item => item.id === notice2Id);
      return { actual: { detailStatus: detail.status, noticeBin: list.body?.items || [] }, pass, evidence: [{ kind: "screenshot", path: await shot(admin.page, "round06-notice-purged.png") }] };
    }, "P0");

  const bulkIds = [];
  for (const suffix of ["A", "B"]) {
    const title = `${tag}-bulk-${suffix}`;
    const cs = await createNotice(admin.page, title);
    if (cs.status === 201) {
      await cancelNotice(admin.page, cs.body.id);
      bulkIds.push({ id: cs.body.id, title });
    }
  }

  await runCase("EGO-NOTICE-BULK-RESTORE", "B", "admin", "DESTRUCTIVE",
    "two cancelled notices are present in the notice recycle section",
    ["both rows can be selected", "bulk restore confirmation succeeds", "result reports two successes", "both notices return published"],
    async () => {
      await openRecycle(admin.page);
      await admin.page.waitForSelector("#notice-rb-tbody tr", { timeout: 15000 });
      for (const row of bulkIds) {
        await admin.page.click(`.notice-rb-check[data-id="${row.id}"]`, { label: `选择通知：${row.title}` });
      }
      const barVisible = await admin.page.evaluate(() => !document.querySelector("#notice-rb-bulk-bar")?.hidden);
      await admin.page.click("#btn-notice-rb-bulk-restore", { label: "批量恢复通知" });
      await modalConfirm(admin.page, { reason: "" });
      await admin.page.waitForTimeout(2200);
      const resultText = await admin.page.evaluate(() => document.querySelector("#notice-rb-bulk-result")?.innerText || "");
      const details = [];
      for (const row of bulkIds) details.push({ id: row.id, status: (await apiJson(admin.page, `/api/v1/notices/${row.id}`)).status });
      const pass = bulkIds.length === 2 && barVisible && resultText.includes("成功 2") && details.every(row => row.status === 200);
      return { actual: { barVisible, resultText, details }, pass, evidence: [{ kind: "screenshot", path: await shot(admin.page, "round06-notice-bulk-restore.png") }] };
    }, "P0");

  const purgeDocTitle = `${tag}-purge-all`;
  const purgeDocCs = await createDoc(admin.page, purgeDocTitle);
  if (purgeDocCs.status !== 201) throw new Error(`purge-all fixture create failed: ${purgeDocCs.status}`);
  const purgeDocId = purgeDocCs.body.id;
  await uploadMain(admin.page, purgeDocId, `${UPLOAD}/${mainName}`);
  await uploadAttachment(admin.page, purgeDocId, `${UPLOAD}/${attachmentName}`, attachmentName);
  await deleteAttachment(admin.page, purgeDocId);
  const purgeNoticeTitle = `${tag}-purge-all-notice`;
  const purgeNoticeCs = await createNotice(admin.page, purgeNoticeTitle);
  if (purgeNoticeCs.status !== 201) throw new Error(`purge-all notice fixture create failed: ${purgeNoticeCs.status}`);
  await cancelNotice(admin.page, purgeNoticeCs.body.id);

  await runCase("EGO-RECYCLE-PURGE-ALL", "E", "admin+user", "DESTRUCTIVE",
    "file recycle bin and notice recycle bin both contain current-run entries",
    ["purge-all button is visible", "danger modal mentions permanent deletion", "both bins become empty", "purge_all audit is written"],
    async () => {
      await openRecycle(admin.page);
      const beforeFiles = (await recycleItems(admin.page)).length;
      const beforeNotices = (await noticeItems(admin.page)).length;
      await admin.page.click("#btn-purge-all", { label: "清空回收站" });
      await modalConfirm(admin.page, { reason: "round-06 purge all" });
      await admin.page.waitForTimeout(2600);
      const afterFiles = (await recycleItems(admin.page)).length;
      const afterNotices = (await noticeItems(admin.page)).length;
      const audit = await auditLogs(admin.page, "recycle_purge");
      const labels = (audit.items || []).map(item => String(item.object_label || ""));
      const pass = beforeFiles > 0 && beforeNotices > 0 && afterFiles === 0 && afterNotices === 0 && labels.includes("purge_all");
      return { actual: { beforeFiles, beforeNotices, afterFiles, afterNotices, labels }, pass, evidence: [{ kind: "screenshot", path: await shot(admin.page, "round06-purge-all.png") }] };
    }, "P0");
  await runCase("EGO-PERM-RECYCLE", "D", "user", "READ_SHARED",
    "ordinary user is logged in; recycle-bin and attachment mutation endpoints are admin-only",
    ["recycle list and notices list reject user", "purge-all, restore, purge reject user", "user recycle page is redirected", "user cannot upload or delete attachments"],
    async () => {
      const getBin = await fetchStatus(user.page, "/api/v1/recycle-bin");
      const getNotices = await fetchStatus(user.page, "/api/v1/recycle-bin/notices");
      const purgeAll = await fetchStatus(user.page, "/api/v1/recycle-bin/purge-all", { method: "POST", body: JSON.stringify({ confirm: true }), headers: { "Content-Type": "application/json" } });
      const restore = await fetchStatus(user.page, `/api/v1/recycle-bin/1/restore`, { method: "POST", body: "{}" });
      const purge = await fetchStatus(user.page, `/api/v1/recycle-bin/1`, { method: "DELETE", body: JSON.stringify({ confirm: true }), headers: { "Content-Type": "application/json" } });
      const uploadAttachment = await fetchStatus(user.page, `/api/v1/documents/${doc2Id}/attachment`, { method: "POST" });
      const deleteAttachment = await fetchStatus(user.page, `/api/v1/documents/${doc2Id}/attachment`, { method: "DELETE" });
      await goto(user.page, ORIGINS.user_a, "/admin/recycle-bin");
      await user.page.waitForTimeout(800);
      const guard = await pageState(user.page);
      const actual = { getBin: getBin.status, getNotices: getNotices.status, purgeAll: purgeAll.status, restore: restore.status, purge: purge.status, uploadAttachment: uploadAttachment.status, deleteAttachment: deleteAttachment.status, guard };
      const pass = [401, 403].includes(getBin.status) && [401, 403].includes(getNotices.status)
        && [401, 403].includes(purgeAll.status) && [401, 403].includes(restore.status)
        && [401, 403].includes(purge.status) && [401, 403].includes(uploadAttachment.status)
        && [401, 403].includes(deleteAttachment.status) && guard.path !== "/admin/recycle-bin";
      return { actual, pass, evidence: [{ kind: "screenshot", path: await shot(user.page, "round06-permission-guard.png") }] };
    }, "P0");

  await runCase("EGO-LOCK-REVERSE", "F", "admin+user", "READ_SHARED",
    "visible document and notice are loaded; locked decisions apply",
    ["only one default main download CTA", "attachment link remains secondary", "no confirmation controls or confirmation wording", "effective_to null displays 暂无失效日期", "no forbidden empty-expiry wording"],
    async () => {
      await goto(user.page, ORIGINS.user_a, `/documents/${doc2Id}`);
      await user.page.waitForFunction(() => !!document.querySelector(".default-download"), undefined, { timeout: 25000 });
      const docLock = await user.page.evaluate(() => ({
        defaultCount: document.querySelectorAll(".default-download").length,
        attachmentLinks: document.querySelectorAll('.attachment-card a[href$="/attachment"]').length,
        secondMain: document.querySelectorAll('.default-download[href$="/attachment"]').length,
        confirmControls: document.querySelectorAll('#btn-confirm-read, .confirm-read-btn, [data-confirm-read], #confirm-read').length,
        bodyText: document.body.innerText || "",
      }));
      const detail = await apiJson(user.page, `/api/v1/documents/${doc2Id}`);
      await goto(user.page, ORIGINS.user_a, `/notices/${noticeId}`);
      await user.page.waitForTimeout(800);
      const noticeText = await user.page.evaluate(() => document.body.innerText || "");
      const stats = await apiJson(admin.page, `/api/v1/notices/${noticeId}/statistics`);
      const dashboard = await apiJson(user.page, "/api/v1/dashboard");
      const actual = { docLock, effectiveTo: detail.body?.effective_to, noticeText, stats: stats.body, dashboardHasUnconfirmed: Object.prototype.hasOwnProperty.call(dashboard.body || {}, "my_unconfirmed_notices") };
      const pass = docLock.defaultCount === 1 && docLock.attachmentLinks === 1 && docLock.secondMain === 0
        && docLock.confirmControls === 0 && !/确认阅读|确认率|待确认/.test(docLock.bodyText)
        && detail.body?.effective_to === null && docLock.bodyText.includes("暂无失效日期")
        && !/长期|永久有效|暂无已知失效日/.test(docLock.bodyText)
        && !/确认阅读|待确认/.test(noticeText) && !String(stats.body || "").includes("confirmed_at")
        && actual.dashboardHasUnconfirmed === false;
      return { actual, pass, evidence: [{ kind: "screenshot", path: await shot(user.page, "round06-lock-reverse.png") }] };
    }, "P0");

  await runCase("EGO-ANON-GUARD", "D", "anon", "READ_SHARED",
    "anon page has no session; recycle-bin is not public",
    ["admin recycle route redirects to login", "recycle API returns 401", "anonymous page never renders recycle table"],
    async () => {
      await goto(anon.page, ORIGINS.anon, "/admin/recycle-bin");
      await anon.page.waitForTimeout(800);
      const state = await pageState(anon.page);
      const api = await fetchStatus(anon.page, "/api/v1/recycle-bin");
      const hasTable = await anon.page.evaluate(() => !!document.querySelector("#rb-tbody"));
      const pass = state.path.startsWith("/login") && api.status === 401 && !hasTable;
      return { actual: { state, apiStatus: api.status, hasTable }, pass, evidence: [{ kind: "screenshot", path: await shot(anon.page, "round06-anon-guard.png") }] };
    }, "P0");

  await runCase("EGO-AUDIT-CHAIN", "H", "admin", "READ_SHARED",
    "attachment and recycle actions from this round have completed",
    ["delete attachment, recycle restore, recycle purge, and purge-all audits exist"],
    async () => {
      const deleteAudit = await auditLogs(admin.page, "delete_document_attachment");
      const restoreAudit = await auditLogs(admin.page, "recycle_restore");
      const purgeAudit = await auditLogs(admin.page, "recycle_purge");
      const purgeAll = (purgeAudit.items || []).some(item => String(item.object_label || "") === "purge_all");
      const actual = { deleteTotal: deleteAudit.total || 0, restoreTotal: restoreAudit.total || 0, purgeTotal: purgeAudit.total || 0, purgeAll };
      const pass = actual.deleteTotal > 0 && actual.restoreTotal > 0 && actual.purgeTotal > 0 && purgeAll;
      return { actual, pass, evidence: [] };
    }, "P1");

  await runCase("EGO-CONSOLE-NETWORK", "F", "admin+user+anon", "READ_SHARED",
    "all three personas have completed the recycle/attachment journey",
    ["no console errors", "no page errors", "no HTTP 5xx", "evidence events are stored"],
    async () => {
      const adminEvents = await drainEvents(admin.page);
      const userEvents = await drainEvents(user.page);
      const anonEvents = await drainEvents(anon.page);
      const events = { admin: adminEvents, user: userEvents, anon: anonEvents };
      const all = [adminEvents, userEvents, anonEvents];
      const consoleErrors = all.reduce((n, item) => n + (item.consoleErrors || []).length, 0);
      const pageErrors = all.reduce((n, item) => n + (item.pageErrors || []).length, 0);
      const http5xx = all.reduce((n, item) => n + (item.network || []).filter(row => Number(row.status) >= 500).length, 0);
      await writeJson(`${OUT}/ego/console/round06-events.json`, events);
      await writeJson(`${OUT}/ego/network/round06-network.json`, all.flatMap(item => item.network || []));
      const actual = { consoleErrors, pageErrors, http5xx, summary: { admin: adminEvents.network?.length || 0, user: userEvents.network?.length || 0, anon: anonEvents.network?.length || 0 } };
      return { actual, pass: consoleErrors === 0 && pageErrors === 0 && http5xx === 0, evidence: [{ kind: "json", path: `${OUT}/ego/console/round06-events.json` }, { kind: "screenshot", path: await shot(admin.page, "round06-console.png") }] };
    }, "P1");

  await writeRoundResult();
}

try {
  await main();
} catch (error) {
  recordCase(cases, caseRow({
    id: "EGO-ATT-RUNNER", phase: ROUND, owner: "H", role: "system", mutability: "ENV_EXCLUSIVE",
    precondition: "isolated synthetic sandbox", expected: ["round completes"], actual: { error: safeText(String(error)), stack: safeText(error?.stack || "") },
    status: "ERROR", severity: "P0", repro: { command: "python -m scripts.full_matrix.run ego round-06-attachment-recycle" },
  }));
  await writeRoundResult();
  process.exitCode = 1;
}