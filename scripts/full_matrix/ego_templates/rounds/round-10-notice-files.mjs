// round-10-notice-files: notice file upload / preview / download, the .pptx
// rule, notice<->document linking, and the 废止 linkage that truncates validity.
//
// Gap this closes: no ego round exercised notice file preview or download, and
// .pptx handling existed only in the pytest suite.

import {
  API, CREDS, ORIGINS, OUT, UPLOAD, apiJson, caseRow, currentMe, disposePages, drainEvents, goto, login, makePage, modalConfirm, recordCase, screenshot, writeJson, writeResult,
} from "../lib/core.mjs";

const ROUND = "round-10-notice-files";
const TAG = `E2E-NF-${Date.now()}`;
const cases = [];
const pages = [];

const listOf = (resp) => {
  const body = resp?.body && typeof resp.body === "object" ? resp.body : {};
  return Array.isArray(body) ? body : (body.items || []);
};

async function createNoticeJson(page, body) {
  return apiJson(page, API.notices, { method: "POST", body });
}

async function findNotice(page, title) {
  const resp = await apiJson(page, `${API.notices}?keyword=${encodeURIComponent(title)}&page_size=50`);
  return listOf(resp).find(n => (n.title || "") === title) || null;
}

async function main() {
  const admin = await makePage("admin");
  const user = await makePage("user-a");
  pages.push({ label: "admin", host: "admin.localhost" }, { label: "user-a", host: "user-a.localhost" });
  await login(admin.page, "admin.localhost", CREDS.admin);
  await login(user.page, "user-a.localhost", CREDS.user_a);

  // ------------------------------------------------------------------
  // A. 通知文件上传（通过真实表单）→ 预览 → 下载
  // ------------------------------------------------------------------
  const fileNoticeTitle = `${TAG}-PDF通知`;
  await goto(admin.page, ORIGINS.admin, "/admin/notices/new");
  await admin.page.waitForSelector("#notice-form", { timeout: 20000 });
  await admin.page.waitForSelector("#upload-input", { state: "attached", timeout: 20000 });
  await admin.page.fill("#f-title", fileNoticeTitle);
  await admin.page.setInputFiles("#upload-input", [`${UPLOAD}/员工手册2026.pdf`]);
  await admin.page.waitForTimeout(1200);
  await admin.page.click("#btn-save");
  // Creating a notice opens a confirmation dialog; nothing is created until it
  // is accepted (notice_form.js:657).
  let fileNoticeConfirm = false;
  try {
    await admin.page.waitForSelector("#modal-root [data-confirm]", { timeout: 8000 });
    fileNoticeConfirm = true;
    await admin.page.click("#modal-root [data-confirm]");
  } catch (_) {}
  await admin.page.waitForTimeout(4000);

  const fileNotice = await findNotice(admin.page, fileNoticeTitle);
  let previewStatus = null;
  let downloadInfo = null;
  if (fileNotice) {
    const preview = await apiJson(admin.page, `${API.notices}/${fileNotice.id}/preview/pdf`);
    previewStatus = { status: preview.status, headers: preview.headers?.["content-type"] ?? null };

    // The download must be served as an attachment, not inline.
    const probe = await admin.page.fetch(`/api/v1/notices/${fileNotice.id}/file`);
    downloadInfo = {
      status: probe.status,
      contentType: probe.headers?.["content-type"] ?? null,
      disposition: probe.headers?.["content-disposition"] ?? null,
      cacheControl: probe.headers?.["cache-control"] ?? null,
    };
  }

  recordCase(cases, caseRow({
    id: "EGO-NF-NOTICE-FILE-UPLOAD",
    phase: ROUND, owner: "B", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: "a notice created through the form with a PDF attached",
    expected: ["通知创建成功并带文件", "PDF 预览端点返回 200", "下载端点返回附件响应且禁止缓存"],
    actual: { confirmShown: fileNoticeConfirm, created: fileNotice ? fileNotice.id : null, previewStatus, downloadInfo },
    status: fileNoticeConfirm && !!fileNotice && previewStatus?.status === 200
      && downloadInfo?.status === 200
      && /attachment/i.test(downloadInfo?.disposition || "")
      && /no-store/i.test(downloadInfo?.cacheControl || "") ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "nf-notice-file.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-10-notice-files", path: "/admin/notices/new" },
  }));

  // ------------------------------------------------------------------
  // B. 通知详情页真实渲染预览按钮（UI 层，不只是接口）
  // ------------------------------------------------------------------
  let uiPreview = null;
  if (fileNotice) {
    await goto(admin.page, ORIGINS.admin, `/notices/${fileNotice.id}`);
    await admin.page.waitForTimeout(1500);
    uiPreview = await admin.page.evaluate(() => ({
      hasPdfButton: !!document.querySelector("#btn-preview-pdf"),
      previewBody: !!document.querySelector("#preview-body"),
      buttons: [...document.querySelectorAll("[id^=btn-preview]")].map(b => b.id),
    }));
  }
  recordCase(cases, caseRow({
    id: "EGO-NF-NOTICE-PREVIEW-UI",
    phase: ROUND, owner: "B", role: "admin", mutability: "READ_SHARED",
    precondition: "a notice with a PDF file, detail page open",
    expected: ["详情页渲染 PDF 在线预览入口"],
    actual: uiPreview,
    status: uiPreview && uiPreview.hasPdfButton ? "PASS" : "FAIL",
    severity: "P1",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "nf-preview-ui.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-10-notice-files", path: "/notices/{id}" },
  }));

  // ------------------------------------------------------------------
  // C. .pptx：只能作附件，不能作通知文件
  // ------------------------------------------------------------------
  // Driven through the real form so the browser builds the multipart body and
  // the server's magic-number + whitelist check runs exactly as it does for a
  // human.  Expect the save to be refused and no notice to be created.
  const pptxTitle = `${TAG}-PPTX通知`;
  await goto(admin.page, ORIGINS.admin, "/admin/notices/new");
  await admin.page.waitForSelector("#notice-form", { timeout: 20000 });
  await admin.page.waitForSelector("#upload-input", { state: "attached", timeout: 20000 });
  await admin.page.fill("#f-title", pptxTitle);
  await admin.page.setInputFiles("#upload-input", [`${UPLOAD}/季度汇报.pptx`]);
  await admin.page.waitForTimeout(1200);
  await admin.page.click("#btn-save");
  await admin.page.waitForTimeout(2000);
  // Accept the confirmation if one appears; the server-side rejection then
  // surfaces as an error toast instead of a created notice.
  try {
    await admin.page.waitForSelector("#modal-root [data-confirm]", { timeout: 5000 });
    await admin.page.click("#modal-root [data-confirm]");
  } catch (_) {}
  await admin.page.waitForTimeout(3500);
  const pptxNotice = await findNotice(admin.page, pptxTitle);

  recordCase(cases, caseRow({
    id: "EGO-NF-PPTX-NOTICE-REFUSED",
    phase: ROUND, owner: "B", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: "a .pptx chosen as the notice file in the real form",
    expected: ["通知没有被创建（.pptx 无法预览，只能作资料附件）"],
    actual: { noticeStillExists: !!pptxNotice },
    status: pptxNotice ? "FAIL" : "PASS",
    severity: "P0",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "nf-pptx.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-10-notice-files", path: "/admin/notices/new" },
  }));

  // ------------------------------------------------------------------
  // D. 通知 ↔ 资料 双向关联（§19）
  // ------------------------------------------------------------------
  const linkDocTitle = `${TAG}-关联资料`;
  const docResp = await apiJson(admin.page, API.documents, {
    method: "POST",
    body: { title: linkDocTitle, effective_from: "2026-01-01", effective_to: null, audience_scope: "all" },
  });
  const linkDoc = listOf(await apiJson(admin.page, `${API.documents}?keyword=${encodeURIComponent(linkDocTitle)}`))
    .find(d => d.title === linkDocTitle);

  const linkNoticeTitle = `${TAG}-关联通知`;
  const linkNoticeResp = await createNoticeJson(admin.page, {
    title: linkNoticeTitle, content: `${TAG} 正文`, document_ids: linkDoc ? [linkDoc.id] : [],
    target: { scope: "all", department_ids: [], user_ids: [] },
  });
  const linkNotice = await findNotice(admin.page, linkNoticeTitle);

  const docSide = linkDoc ? await apiJson(admin.page, `${API.documents}/${linkDoc.id}/notices`) : { body: null };
  const noticeSide = linkNotice ? await apiJson(admin.page, `${API.notices}/${linkNotice.id}/documents`) : { body: null };
  const docNotices = listOf(docSide);
  const noticeDocs = listOf(noticeSide);

  recordCase(cases, caseRow({
    id: "EGO-NF-LINK-BIDIRECTIONAL",
    phase: ROUND, owner: "B", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: "a notice created with document_ids pointing at a document",
    expected: ["通知创建成功", "资料侧能查到该通知", "通知侧能查到该资料"],
    actual: {
      noticeCreate: linkNoticeResp.status,
      docId: linkDoc?.id ?? null,
      noticeId: linkNotice?.id ?? null,
      docSideTitles: docNotices.map(n => n.title),
      noticeSideTitles: noticeDocs.map(d => d.title),
    },
    status: linkNoticeResp.status === 201
      && docNotices.some(n => n.title === linkNoticeTitle)
      && noticeDocs.some(d => d.title === linkDocTitle) ? "PASS" : "FAIL",
    severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-10-notice-files", path: "/api/v1/documents/{id}/notices" },
  }));

  // ------------------------------------------------------------------
  // E. 废止联动：截断 effective_to → 取消回滚 → 恢复重放
  // ------------------------------------------------------------------
  const abolishDocTitle = `${TAG}-待废止`;
  await apiJson(admin.page, API.documents, {
    method: "POST",
    body: { title: abolishDocTitle, effective_from: "2026-01-01", effective_to: "2027-12-31", audience_scope: "all" },
  });
  const abolishDoc = listOf(await apiJson(admin.page, `${API.documents}?keyword=${encodeURIComponent(abolishDocTitle)}`))
    .find(d => d.title === abolishDocTitle);

  const abolishNoticeTitle = `${TAG}-废止通知`;
  const abolishResp = await createNoticeJson(admin.page, {
    title: abolishNoticeTitle,
    content: `${TAG} 废止正文`,
    notice_type: "废止",
    effective_at: "2026-06-15T00:00:00",
    document_ids: abolishDoc ? [abolishDoc.id] : [],
    target: { scope: "all", department_ids: [], user_ids: [] },
  });
  const abolishNotice = await findNotice(admin.page, abolishNoticeTitle);
  const afterPublish = abolishDoc ? await apiJson(admin.page, `${API.documents}/${abolishDoc.id}`) : { body: null };

  let afterCancel = null;
  let afterRestore = null;
  if (abolishNotice) {
    await apiJson(admin.page, `${API.notices}/${abolishNotice.id}/cancel`, {
      method: "POST", body: { reason: `${TAG} 取消` },
    });
    afterCancel = abolishDoc ? await apiJson(admin.page, `${API.documents}/${abolishDoc.id}`) : { body: null };
    await apiJson(admin.page, `${API.recycle}/notices/${abolishNotice.id}/restore`, { method: "POST" });
    afterRestore = abolishDoc ? await apiJson(admin.page, `${API.documents}/${abolishDoc.id}`) : { body: null };
  }

  const truncated = afterPublish.body?.effective_to;
  const rolledBack = afterCancel?.body?.effective_to;
  const reapplied = afterRestore?.body?.effective_to;

  recordCase(cases, caseRow({
    id: "EGO-NF-ABOLISH-LINKAGE",
    phase: ROUND, owner: "B", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: "a document expiring 2027-12-31 and a 废止 notice effective 2026-06-15",
    expected: ["发布废止通知后 effective_to 被截断为通知生效日",
               "取消通知后 effective_to 回滚到原值",
               "恢复通知后再次截断"],
    actual: { publish: abolishResp.status, truncated, rolledBack, reapplied },
    status: abolishResp.status === 201
      && truncated === "2026-06-15" && rolledBack === "2027-12-31" && reapplied === "2026-06-15" ? "PASS" : "FAIL",
    severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-10-notice-files", path: "/api/v1/notices" },
  }));

  // ------------------------------------------------------------------
  // F. 普通用户 403：越权写通知
  // ------------------------------------------------------------------
  const denied = await apiJson(user.page, API.notices, {
    method: "POST",
    body: { title: `${TAG}-越权`, content: "x", target: { scope: "all", department_ids: [], user_ids: [] } },
  });
  recordCase(cases, caseRow({
    id: "EGO-NF-USER-CANNOT-CREATE",
    phase: ROUND, owner: "D", role: "user", mutability: "GLOBAL_WRITE",
    precondition: "ordinary user session",
    expected: ["普通用户不能创建通知（401/403）"],
    actual: { status: denied.status },
    status: denied.status === 401 || denied.status === 403 ? "PASS" : "FAIL",
    severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-10-notice-files" },
  }));

  // ------------------------------------------------------------------
  // G. 清理 + 控制台
  // ------------------------------------------------------------------
  const swept = { notices: [], documents: [] };
  const noticesNow = listOf(await apiJson(admin.page, `${API.notices}?page_size=100`));
  for (const n of noticesNow.filter(x => (x.title || "").startsWith("E2E-NF-"))) {
    const asNotice = await apiJson(admin.page, `${API.notices}/${n.id}/cancel`, { method: "POST", body: { reason: "cleanup" } });
    swept.notices.push({ title: n.title, cancel: asNotice.status });
  }
  const cancelled = listOf(await apiJson(admin.page, `${API.recycle}/notices?page_size=100`));
  for (const n of cancelled.filter(x => (x.title || "").startsWith("E2E-NF-"))) {
    const purged = await apiJson(admin.page, `${API.recycle}/notices/${n.id}`, { method: "DELETE" });
    swept.notices.push({ title: n.title, purge: purged.status });
  }
  const docsNow = listOf(await apiJson(admin.page, `${API.documents}?page_size=100`));
  for (const d of docsNow.filter(x => (x.title || "").startsWith("E2E-NF-"))) {
    const archived = await apiJson(admin.page, `${API.documents}/${d.id}`, { method: "DELETE" });
    swept.documents.push({ title: d.title, archive: archived.status });
  }
  await writeJson(`${OUT}/ego/results/${ROUND}/cleanup.json`, swept);

  const events = await drainEvents(admin.page);
  const clean = events.consoleErrors.length === 0 && events.pageErrors.length === 0;
  recordCase(cases, caseRow({
    id: "EGO-NF-CONSOLE",
    phase: ROUND, owner: "F", role: "admin", mutability: "READ_SHARED",
    precondition: "notice file and linkage flows exercised",
    expected: ["no console error and no pageerror"],
    actual: events,
    status: clean ? "PASS" : "FAIL",
    severity: "P1",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/${ROUND}/events.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-10-notice-files" },
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
    id: "EGO-NF-RUNNER", phase: ROUND, owner: "B", role: "system", mutability: "GLOBAL_WRITE",
    precondition: "ego runtime", expected: ["round completes"],
    actual: { error: String(error), stack: error?.stack || null },
    status: "ERROR", severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-10-notice-files" },
  }));
  await disposePages(ROUND, ["admin", "user-a"]);
  await writeResult(ROUND, cases, pages);
  process.exitCode = 1;
}
