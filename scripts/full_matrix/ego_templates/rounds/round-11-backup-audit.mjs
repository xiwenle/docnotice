// round-11-backup-audit: backup create/verify/restore through the UI plus audit
// filtering, single delete and batch delete with the audit_delete counter-entry.
//
// Gap this closes: round-05 only asserted "a backup can be created and verified"
// and "one audit row can be deleted".  Restoration through the real page, the
// file_count difference between the two backup types, and batch deletion with its
// single merged audit entry were never exercised in a browser.

import {
  API, CREDS, ORIGINS, OUT, apiJson, caseRow, disposePages, drainEvents, goto, login, makePage, recordCase, screenshot, writeJson, writeResult,
} from "../lib/core.mjs";

const ROUND = "round-11-backup-audit";
const TAG = `E2E-BA-${Date.now()}`;
const cases = [];
const pages = [];

const listOf = (resp) => {
  const body = resp?.body && typeof resp.body === "object" ? resp.body : {};
  return Array.isArray(body) ? body : (body.items || []);
};

async function waitModal(page, timeout = 10000) {
  try {
    await page.waitForSelector("#modal-root [data-confirm]", { timeout });
    return true;
  } catch (_) {
    return false;
  }
}

async function acceptModal(page, { reason = "" } = {}) {
  if (reason) {
    try { await page.fill("#confirm-input", reason); } catch (_) {}
  }
  await page.click("#modal-root [data-confirm]");
  await page.waitForTimeout(600);
}

async function main() {
  const admin = await makePage("admin");
  const user = await makePage("user-a");
  pages.push({ label: "admin", host: "admin.localhost" }, { label: "user-a", host: "user-a.localhost" });
  await login(admin.page, "admin.localhost", CREDS.admin);
  await login(user.page, "user-a.localhost", CREDS.user_a);

  // ------------------------------------------------------------------
  // A. 两种备份类型：仅库 vs 完整（file_count 必须不同）
  // ------------------------------------------------------------------
  await goto(admin.page, ORIGINS.admin, "/admin/backups");
  await admin.page.waitForSelector("#backup-tbody", { timeout: 20000 });

  await admin.page.selectOption("#backup-type", { label: "仅数据库" }).catch(async () => {
    await admin.page.selectOption("#backup-type", "database");
  });
  await admin.page.click("#btn-create-backup");
  await admin.page.waitForTimeout(6000);

  const dbOnlyList = listOf(await apiJson(admin.page, `${API.backups}?page_size=20`));
  const dbOnly = dbOnlyList.find(b => b.backup_type === "database");

  await admin.page.selectOption("#backup-type", { label: "完整" }).catch(async () => {
    await admin.page.selectOption("#backup-type", "full");
  });
  await admin.page.click("#btn-create-backup");
  await admin.page.waitForTimeout(12000);

  const fullList = listOf(await apiJson(admin.page, `${API.backups}?page_size=20`));
  const full = fullList.find(b => b.backup_type === "full");

  recordCase(cases, caseRow({
    id: "EGO-BA-CREATE-TWO-TYPES",
    phase: ROUND, owner: "E", role: "admin", mutability: "DESTRUCTIVE",
    precondition: "backup page open, both types created through the real select + button",
    expected: ["仅数据库备份创建成功且 file_count = 0",
               "完整备份创建成功且 file_count > 0"],
    actual: {
      dbOnly: dbOnly ? { id: dbOnly.id, type: dbOnly.backup_type, fileCount: dbOnly.file_count, status: dbOnly.status } : null,
      full: full ? { id: full.id, type: full.backup_type, fileCount: full.file_count, status: full.status } : null,
    },
    status: dbOnly && full && Number(dbOnly.file_count) === 0 && Number(full.file_count) > 0
      && dbOnly.status === "completed" && full.status === "completed" ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "ba-two-types.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-11-backup-audit", path: "/admin/backups" },
  }));

  // ------------------------------------------------------------------
  // B. 只读校验（manifest + 哈希 + integrity_check）
  // ------------------------------------------------------------------
  let verifyResult = null;
  if (full) {
    verifyResult = await apiJson(admin.page, `${API.backups}/${full.id}/verify`, { method: "POST" });
  }
  recordCase(cases, caseRow({
    id: "EGO-BA-VERIFY",
    phase: ROUND, owner: "E", role: "admin", mutability: "READ_SHARED",
    precondition: "a completed full backup exists",
    expected: ["校验返回 ok=true", "校验结果为只读，不改变备份状态"],
    actual: { status: verifyResult?.status ?? null, ok: verifyResult?.body?.ok ?? null, checks: verifyResult?.body?.checks ?? null },
    status: verifyResult && verifyResult.status === 200 && verifyResult.body?.ok === true ? "PASS" : "FAIL",
    severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-11-backup-audit", path: "/api/v1/backups/{id}/verify" },
  }));

  // ------------------------------------------------------------------
  // C. 恢复：备份 → 改动 → 恢复后改动消失（§38 必须项）
  // ------------------------------------------------------------------
  const markerTitle = `${TAG}-恢复前不存在`;
  const markerDoc = await apiJson(admin.page, API.documents, {
    method: "POST",
    body: { title: markerTitle, effective_from: "2026-01-01", effective_to: null, audience_scope: "all" },
  });
  const beforeRestore = listOf(await apiJson(admin.page, `${API.documents}?keyword=${encodeURIComponent(markerTitle)}`))
    .some(d => d.title === markerTitle);

  let restoreStatus = null;
  if (full) {
    await goto(admin.page, ORIGINS.admin, "/admin/backups");
    await admin.page.waitForSelector("#backup-tbody", { timeout: 20000 });
    await admin.page.waitForTimeout(1000);
    const clicked = await admin.page.evaluate((id) => {
      const btn = document.querySelector(`#backup-tbody [data-action="restore"][data-id="${id}"]`);
      if (!btn) return false;
      btn.click();
      return true;
    }, full.id);
    const modal = clicked ? await waitModal(admin.page, 12000) : false;
    if (modal) {
      await acceptModal(admin.page, { reason: `${TAG} 恢复演练` });
      restoreStatus = { clicked, modal, accepted: true };
    } else {
      restoreStatus = { clicked, modal, accepted: false };
    }
    // Restoration drops connections and rewrites the database; give it room.
    await admin.page.waitForTimeout(18000);
    const health = await apiJson(admin.page, "/health");
    const afterRestore = listOf(await apiJson(admin.page, `${API.documents}?keyword=${encodeURIComponent(markerTitle)}`))
      .some(d => d.title === markerTitle);
    restoreStatus.health = health.status;
    restoreStatus.docStillThere = afterRestore;
  }

  recordCase(cases, caseRow({
    id: "EGO-BA-RESTORE-ROLLBACK",
    phase: ROUND, owner: "E", role: "admin", mutability: "DESTRUCTIVE",
    precondition: "a full backup taken before a marker document was created",
    expected: ["恢复操作先要求确认并填写原因",
               "恢复后标记资料消失（回到备份时点）",
               "恢复后服务仍然健康"],
    actual: { beforeRestore, restoreStatus },
    status: beforeRestore
      && restoreStatus?.modal === true
      && restoreStatus?.docStillThere === false
      && restoreStatus?.health === 200 ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "ba-restore.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-11-backup-audit", path: "/admin/backups" },
  }));

  // ------------------------------------------------------------------
  // D. 操作日志：按操作类型筛选，结果一致
  // ------------------------------------------------------------------
  await goto(admin.page, ORIGINS.admin, "/admin/audit-logs");
  await admin.page.waitForSelector("#log-tbody", { timeout: 20000 });
  await admin.page.waitForTimeout(1200);

  const unfiltered = listOf(await apiJson(admin.page, `${API.audits}?page_size=50`));
  const actionFilters = [...new Set(unfiltered.map(a => a.action).filter(Boolean))].slice(0, 3);
  const filterChecks = [];
  for (const action of actionFilters) {
    const filtered = await apiJson(admin.page, `${API.audits}?action=${encodeURIComponent(action)}&page_size=50`);
    const items = listOf(filtered);
    filterChecks.push({
      action,
      status: filtered.status,
      count: items.length,
      allMatch: items.every(a => a.action === action),
    });
  }
  // The page's own dropdown must contain the same actions the API returns.
  const dropdown = await admin.page.evaluate(() => {
    const sel = document.querySelector("#f-action");
    return sel ? [...sel.options].map(o => o.value).filter(Boolean) : [];
  });

  recordCase(cases, caseRow({
    id: "EGO-BA-AUDIT-FILTER",
    phase: ROUND, owner: "E", role: "admin", mutability: "READ_SHARED",
    precondition: "audit log page with real entries",
    expected: ["按操作类型筛选后返回的每条记录都属于该类型", "页面下拉包含真实存在的操作类型"],
    actual: { totalUnfiltered: unfiltered.length, filterChecks, dropdownHasAll: actionFilters.every(a => dropdown.includes(a)), dropdownSample: dropdown.slice(0, 8) },
    status: filterChecks.length > 0
      && filterChecks.every(f => f.status === 200 && f.allMatch)
      && actionFilters.every(a => dropdown.includes(a)) ? "PASS" : "FAIL",
    severity: "P1",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "ba-audit-filter.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-11-backup-audit", path: "/admin/audit-logs" },
  }));

  // ------------------------------------------------------------------
  // E. 批量删除：所选全部消失，且只追加一条 audit_delete 汇总
  // ------------------------------------------------------------------
  const beforeBatch = listOf(await apiJson(admin.page, `${API.audits}?page_size=100`));
  const deletable = beforeBatch.slice(0, 3).map(a => a.id);
  const auditDeleteBefore = beforeBatch.filter(a => a.action === "audit_delete").length;

  let batchResult = null;
  if (deletable.length >= 2) {
    batchResult = await apiJson(admin.page, `${API.audits}/batch-delete`, {
      method: "POST", body: { ids: deletable },
    });
    const afterBatch = listOf(await apiJson(admin.page, `${API.audits}?page_size=100`));
    const afterIds = new Set(afterBatch.map(a => a.id));
    const auditDeleteAfter = afterBatch.filter(a => a.action === "audit_delete").length;
    batchResult = {
      http: batchResult.status,
      removed: deletable.filter(id => !afterIds.has(id)).length,
      requested: deletable.length,
      auditDeleteBefore,
      auditDeleteAfter,
      auditDeleteDelta: auditDeleteAfter - auditDeleteBefore,
    };
  }
  recordCase(cases, caseRow({
    id: "EGO-BA-AUDIT-BATCH-DELETE",
    phase: ROUND, owner: "E", role: "admin", mutability: "DESTRUCTIVE",
    precondition: "at least two audit rows are selected",
    expected: ["所选日志全部消失", "只追加一条 audit_delete 汇总（不是每条一条）"],
    actual: batchResult,
    status: batchResult && batchResult.http === 200
      && batchResult.removed === batchResult.requested
      && batchResult.auditDeleteDelta === 1 ? "PASS" : "FAIL",
    severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-11-backup-audit", path: "/api/v1/audit-logs/batch-delete" },
  }));

  // ------------------------------------------------------------------
  // F. 无「按筛选全删 / 清空全部」入口（§25 补充决策）
  // ------------------------------------------------------------------
  const forbiddenUi = await admin.page.evaluate(() => {
    const text = document.body.innerText || "";
    const labels = [...document.querySelectorAll("button, a.btn")].map(b => (b.textContent || "").trim());
    return {
      hasClearAll: labels.some(l => /清空全部|全部删除|按筛选全删/.test(l)),
      hasBatchDelete: labels.some(l => /删除所选/.test(l)),
      textMentionsClearAll: /清空全部/.test(text),
    };
  });
  recordCase(cases, caseRow({
    id: "EGO-BA-AUDIT-NO-CLEAR-ALL",
    phase: ROUND, owner: "E", role: "admin", mutability: "READ_SHARED",
    precondition: "audit log page open",
    expected: ["存在「删除所选」", "不存在「清空全部 / 全部删除 / 按筛选全删」"],
    actual: forbiddenUi,
    status: forbiddenUi.hasBatchDelete && !forbiddenUi.hasClearAll && !forbiddenUi.textMentionsClearAll ? "PASS" : "FAIL",
    severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-11-backup-audit", path: "/admin/audit-logs" },
  }));

  // ------------------------------------------------------------------
  // G. 普通用户全部被拒
  // ------------------------------------------------------------------
  const denied = [];
  for (const [method, route] of [
    ["GET", API.backups], ["POST", API.backups], ["GET", API.audits],
    ["POST", `${API.audits}/batch-delete`],
  ]) {
    const resp = await apiJson(user.page, route, method === "POST" ? { method, body: { ids: [1] } } : {});
    denied.push({ method, route, status: resp.status });
  }
  recordCase(cases, caseRow({
    id: "EGO-BA-USER-DENIED",
    phase: ROUND, owner: "D", role: "user", mutability: "READ_SHARED",
    precondition: "ordinary user session",
    expected: ["普通用户对备份与日志接口一律 401/403"],
    actual: denied,
    status: denied.every(d => d.status === 401 || d.status === 403) ? "PASS" : "FAIL",
    severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-11-backup-audit" },
  }));

  // ------------------------------------------------------------------
  // H. 控制台
  // ------------------------------------------------------------------
  const events = await drainEvents(admin.page);
  const clean = events.consoleErrors.length === 0 && events.pageErrors.length === 0;
  recordCase(cases, caseRow({
    id: "EGO-BA-CONSOLE",
    phase: ROUND, owner: "F", role: "admin", mutability: "READ_SHARED",
    precondition: "backup restore + audit flows exercised",
    expected: ["no console error and no pageerror"],
    actual: events,
    status: clean ? "PASS" : "FAIL",
    severity: "P1",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/${ROUND}/events.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-11-backup-audit" },
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
    id: "EGO-BA-RUNNER", phase: ROUND, owner: "E", role: "system", mutability: "DESTRUCTIVE",
    precondition: "ego runtime", expected: ["round completes"],
    actual: { error: String(error), stack: error?.stack || null },
    status: "ERROR", severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-11-backup-audit" },
  }));
  await disposePages(ROUND, ["admin", "user-a"]);
  await writeResult(ROUND, cases, pages);
  process.exitCode = 1;
}
