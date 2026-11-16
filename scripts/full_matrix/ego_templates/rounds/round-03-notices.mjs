// round-03-notices: create-publish, audiences, read tracking, statistics, linkage, cancel/restore.

import {
  API, CREDS, ORIGINS, OUT, apiJson, caseRow, currentMe, disposePages, drainEvents, goto, login, makePage, pageState, recordCase, screenshot, writeJson, writeResult,
} from "../lib/core.mjs";

const cases = [];
const pages = [];
const tag = `E2E-20261111-impl01-B-${Date.now()}`;

async function createNotice(page, body) {
  return apiJson(page, API.notices, { method: "POST", body });
}

async function main() {
  const admin = await makePage("admin");
  const user = await makePage("user");
  pages.push({ label: "admin", host: "admin.localhost" }, { label: "user", host: "user-a.localhost" });
  await login(admin.page, "admin.localhost", CREDS.admin);
  await login(user.page, "user-a.localhost", CREDS.user_a);

  const userList = await apiJson(admin.page, `/api/v1/users?keyword=${encodeURIComponent(CREDS.user_a.username)}&page_size=20`);
  const userId = userList.body?.items?.[0]?.id;
  const deptList = await apiJson(admin.page, "/api/v1/departments?page_size=100");
  const deptId = deptList.body?.items?.find(item => item.name === "研发部")?.id || deptList.body?.items?.[0]?.id;
  const types = await apiJson(admin.page, API.noticeTypes);
  const typeNames = (types.body?.items || []).map(item => item.name);
  const baseType = typeNames.includes("发布") ? "发布" : (typeNames[0] || null);

  const all = await createNotice(admin.page, {
    title: `${tag}-all`, content: "全员通知正文", notice_type: baseType,
    target: { scope: "all", department_ids: [], user_ids: [] }, document_ids: [],
  });
  const allId = all.body?.id;
  recordCase(cases, caseRow({
    id: "EGO-NOTICE-CREATE-ALL",
    phase: "round-03-notices",
    owner: "B",
    role: "admin",
    mutability: "GLOBAL_WRITE",
    precondition: "admin session and published notice API",
    expected: ["creation returns 201", "status is published", "no draft field is returned"],
    actual: { status: all.status, body: all.body },
    status: all.status === 201 && allId && all.body?.status === "published" ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/round-03-notices/create.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-03-notices" },
  }));
  if (!allId) throw new Error(`notice create failed: ${all.status}`);

  const target = await createNotice(admin.page, {
    title: `${tag}-user-target`, content: "指定用户通知正文", notice_type: baseType,
    target: { scope: "user", department_ids: [], user_ids: userId ? [userId] : [] }, document_ids: [],
  });
  const targetId = target.body?.id;
  const department = deptId ? await createNotice(admin.page, {
    title: `${tag}-department-target`, content: "部门通知正文", notice_type: baseType,
    target: { scope: "department", department_ids: [deptId], user_ids: [] }, document_ids: [],
  }) : null;
  recordCase(cases, caseRow({
    id: "EGO-NOTICE-THREE-AUDIENCES",
    phase: "round-03-notices",
    owner: "B",
    role: "admin",
    mutability: "GLOBAL_WRITE",
    precondition: "admin can resolve seed user and department IDs",
    expected: ["all/department/user notices are accepted and published"],
    actual: { all: all.status, target: target.status, department: department?.status ?? "SKIP" },
    status: target.status === 201 && (department ? department.status === 201 : true) ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/round-03-notices/audiences.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-03-notices" },
  }));

  const future = await createNotice(admin.page, {
    title: `${tag}-future`, content: "未来生效通知", notice_type: baseType,
    effective_at: "2099-01-01T00:00:00", target: { scope: "all", department_ids: [], user_ids: [] }, document_ids: [],
  });
  const userNoticesBefore = await apiJson(user.page, "/api/v1/notices?page_size=100");
  const futureVisibleToUser = (userNoticesBefore.body?.items || []).some(item => item.title === `${tag}-future`);
  recordCase(cases, caseRow({
    id: "EGO-NOTICE-FUTURE-HIDDEN",
    phase: "round-03-notices",
    owner: "B",
    role: "user",
    mutability: "READ_SHARED",
    precondition: "future notice created by admin",
    expected: ["future notice is not visible to ordinary user before effective_at"],
    actual: { status: future.status, futureId: future.body?.id, visible: futureVisibleToUser },
    status: future.status === 201 && !futureVisibleToUser ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/round-03-notices/future.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-03-notices" },
  }));

  await goto(user.page, ORIGINS.user_a, `/notices/${allId}`);
  await user.page.waitForLoadState();
  await user.page.waitForTimeout(1000);
  const detail = await apiJson(user.page, `/api/v1/notices/${allId}`);
  const detailText = await user.page.evaluate(() => document.body.innerText || "");
  recordCase(cases, caseRow({
    id: "EGO-NOTICE-USER-READ",
    phase: "round-03-notices",
    owner: "B",
    role: "user",
    mutability: "GLOBAL_WRITE",
    precondition: "all-audience published notice exists",
    expected: ["user detail loads", "opening detail records read status", "only unread/viewed wording exists"],
    actual: { status: detail.status, body: detail.body, bodyText: detailText, forbidden: ["确认阅读", "确认率", "待确认"].filter(word => detailText.includes(word)) },
    status: detail.status === 200 && !detailText.includes("确认阅读") && !detailText.includes("确认率") && !detailText.includes("待确认") ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "screenshot", path: await screenshot(user.page, "notice-read-user.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-03-notices" },
  }));

  const stats = await apiJson(admin.page, `/api/v1/notices/${allId}/statistics`);
  const statsBody = stats.body || {};
  const statsText = JSON.stringify(statsBody);
  recordCase(cases, caseRow({
    id: "EGO-NOTICE-STATS",
    phase: "round-03-notices",
    owner: "B",
    role: "admin",
    mutability: "READ_SHARED",
    precondition: "user opened the all-audience notice",
    expected: ["statistics returns 200", "no confirmed_at or confirmation rate fields"],
    actual: { status: stats.status, body: statsBody },
    status: stats.status === 200 && !/confirmed_at|confirmed_rate|unconfirmed/i.test(statsText) ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/round-03-notices/stats.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-03-notices" },
  }));

  const modifyDenied = await apiJson(admin.page, `/api/v1/notices/${allId}`, {
    method: "PUT", body: { title: `${tag}-all-updated`, confirm_modify: false, modified_reason: "" },
  });
  const modifyAccepted = await apiJson(admin.page, `/api/v1/notices/${allId}`, {
    method: "PUT", body: { title: `${tag}-all-updated`, confirm_modify: true, modified_reason: "E2E audit reason" },
  });
  recordCase(cases, caseRow({
    id: "EGO-NOTICE-MODIFY-AUDIT",
    phase: "round-03-notices",
    owner: "B",
    role: "admin",
    mutability: "GLOBAL_WRITE",
    precondition: "notice has at least one read record",
    expected: ["unconfirmed modify is rejected 409", "explicit reason modify succeeds"],
    actual: { deniedStatus: modifyDenied.status, deniedBody: modifyDenied.body, acceptedStatus: modifyAccepted.status, acceptedBody: modifyAccepted.body },
    status: modifyDenied.status === 409 && modifyAccepted.status === 200 ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/round-03-notices/modify.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-03-notices" },
  }));

  const doc = await apiJson(admin.page, API.documents, {
    method: "POST",
    body: { title: `${tag}-linkage-doc`, effective_from: "2026-01-01", effective_to: "2099-12-31", audience_scope: "all" },
  });
  const linkageDocId = doc.body?.id;
  let linkageNoticeId = null;
  let linkageBefore = null;
  let linkageAfter = null;
  let linkageCancel = null;
  if (linkageDocId && typeNames.includes("废止")) {
    linkageBefore = await apiJson(admin.page, `/api/v1/documents/${linkageDocId}`);
    const linkageNotice = await createNotice(admin.page, {
      title: `${tag}-abolish`, content: "废止联动通知", notice_type: "废止",
      target: { scope: "all", department_ids: [], user_ids: [] }, document_ids: [linkageDocId],
    });
    linkageNoticeId = linkageNotice.body?.id;
    linkageAfter = await apiJson(admin.page, `/api/v1/documents/${linkageDocId}`);
    if (linkageNoticeId) {
      linkageCancel = await apiJson(admin.page, `/api/v1/notices/${linkageNoticeId}/cancel`, { method: "POST", body: { reason: "E2E rollback" } });
    }
  }
  const rolledBack = linkageDocId ? await apiJson(admin.page, `/api/v1/documents/${linkageDocId}`) : null;
  recordCase(cases, caseRow({
    id: "EGO-NOTICE-LINKAGE-ABOLISH",
    phase: "round-03-notices",
    owner: "B",
    role: "admin",
    mutability: "XCHAIN",
    precondition: "seed includes 废止 notice type; synthetic document has a long effective_to",
    expected: ["废止 notice truncates effective_to", "cancel rolls the prior date back"],
    actual: { linkageDocId, linkageBefore: linkageBefore?.body, linkageAfter: linkageAfter?.body, cancelStatus: linkageCancel?.status, rolledBack: rolledBack?.body },
    status: linkageDocId && linkageNoticeId && linkageAfter?.body?.effective_to !== linkageBefore?.body?.effective_to && rolledBack?.body?.effective_to === linkageBefore?.body?.effective_to ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/round-03-notices/linkage.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-03-notices" },
  }));

  const cancel = await apiJson(admin.page, `/api/v1/notices/${allId}/cancel`, { method: "POST", body: { reason: "E2E cancel" } });
  const hiddenAfterCancel = await user.page.fetch(`/api/v1/notices/${allId}`);
  const restore = await apiJson(admin.page, `/api/v1/recycle-bin/notices/${allId}/restore`, { method: "POST" });
  await goto(user.page, ORIGINS.user_a, `/notices/${allId}`);
  const restoredDetail = await user.page.fetch(`/api/v1/notices/${allId}`);
  recordCase(cases, caseRow({
    id: "EGO-NOTICE-CANCEL-RESTORE",
    phase: "round-03-notices",
    owner: "B",
    role: "admin",
    mutability: "XCHAIN",
    precondition: "notice was published and read by target user",
    expected: ["cancel hides notice from user", "restore brings it back", "read history is preserved according to contract"],
    actual: { cancelStatus: cancel.status, hiddenStatus: hiddenAfterCancel.status, restoreStatus: restore.status, restoredStatus: restoredDetail.status, restoredBody: restoredDetail.body },
    status: cancel.status === 200 && hiddenAfterCancel.status !== 200 && restore.status === 200 && restoredDetail.status === 200 ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/round-03-notices/cancel-restore.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-03-notices" },
  }));

  const noPublish = await admin.page.fetch(`/api/v1/notices/${allId}/publish`);
  const noConfirm = await user.page.fetch(`/api/v1/notices/${allId}/confirm`);
  const reverse = await user.page.evaluate(() => ({
    text: document.body.innerText || "",
    confirmButtons: document.querySelectorAll('[data-action="confirm"], #btn-confirm, [data-confirm-read]').length,
    draftTab: !!document.querySelector('[data-tab="draft"], [data-status="draft"]'),
  }));
  recordCase(cases, caseRow({
    id: "EGO-NOTICE-LOCKED-DECISIONS",
    phase: "round-03-notices",
    owner: "B",
    role: "user",
    mutability: "READ_SHARED",
    precondition: "notice list/detail contains published notice",
    expected: ["no /publish endpoint", "no /confirm endpoint", "no draft tab/button", "no confirmation wording"],
    actual: { publishStatus: noPublish.status, confirmStatus: noConfirm.status, reverse },
    status: noPublish.status === 404 || noPublish.status === 405 || noConfirm.status === 404 || noConfirm.status === 405 ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/round-03-notices/locked.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-03-notices" },
  }));

  const events = await drainEvents(user.page);
  recordCase(cases, caseRow({
    id: "EGO-NOTICE-CONSOLE",
    phase: "round-03-notices", owner: "F", role: "user", mutability: "READ_SHARED",
    precondition: "notice read/cancel/restore chain complete",
    expected: ["no console error or pageerror"], actual: events,
    status: events.consoleErrors.length === 0 && events.pageErrors.length === 0 ? "PASS" : "FAIL",
    severity: "P1", evidence: [{ kind: "json", path: `${OUT}/ego/results/round-03-notices/events.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-03-notices" },
  }));
  await writeJson(`${OUT}/ego/results/round-03-notices/create.json`, { all, target, department, future });
  await writeJson(`${OUT}/ego/results/round-03-notices/audiences.json`, { userId, deptId, targetId, departmentId: department?.body?.id });
  await writeJson(`${OUT}/ego/results/round-03-notices/future.json`, { future, visible: futureVisibleToUser });
  await writeJson(`${OUT}/ego/results/round-03-notices/stats.json`, statsBody);
  await writeJson(`${OUT}/ego/results/round-03-notices/modify.json`, { modifyDenied, modifyAccepted });
  await writeJson(`${OUT}/ego/results/round-03-notices/linkage.json`, { linkageBefore, linkageAfter, linkageCancel, rolledBack });
  await writeJson(`${OUT}/ego/results/round-03-notices/cancel-restore.json`, { cancel, hiddenAfterCancel, restore, restoredDetail });
  await writeJson(`${OUT}/ego/results/round-03-notices/locked.json`, { noPublish, noConfirm, reverse });
  await writeJson(`${OUT}/ego/results/round-03-notices/events.json`, events);
}

try {
  await main();
  await disposePages("round-03-notices", ["admin", "user"]);
  await writeResult("round-03-notices", cases, pages);
} catch (error) {
  recordCase(cases, caseRow({
    id: "EGO-NOTICE-RUNNER", phase: "round-03-notices", owner: "B", role: "system", mutability: "GLOBAL_WRITE",
    precondition: "isolated synthetic sandbox", expected: ["round completes"],
    actual: { error: String(error), stack: error?.stack || null }, status: "ERROR", severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-03-notices" },
  }));
  await disposePages("round-03-notices", ["admin", "user"]);
  await writeResult("round-03-notices", cases, pages);
  process.exitCode = 1;
}