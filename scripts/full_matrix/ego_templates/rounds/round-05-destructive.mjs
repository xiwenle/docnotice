// round-05-destructive: recycle-bin, backup/verify/restore, maintenance health, audit deletion.

import {
  API, CREDS, ORIGINS, OUT, apiJson, caseRow, disposePages, drainEvents, goto, login, makePage, pageState, recordCase, screenshot, writeJson, writeResult,
} from "../lib/core.mjs";

const cases = [];
const pages = [];
const tag = `E2E-20261111-impl01-E-${Date.now()}`;

async function createDoc(page, title) {
  return apiJson(page, API.documents, { method: "POST", body: { title, effective_from: "2026-01-01", effective_to: "2026-12-31", audience_scope: "all" } });
}

async function main() {
  const admin = await makePage("admin");
  pages.push({ label: "admin", host: "admin.localhost" });
  await login(admin.page, "admin.localhost", CREDS.admin);

  const doc = await createDoc(admin.page, `${tag}-recycle-doc`);
  const docId = doc.body?.id;
  const archived = docId ? await apiJson(admin.page, `/api/v1/documents/${docId}`, { method: "DELETE", body: { reason: "E2E recycle" } }) : null;
  const bin = await apiJson(admin.page, "/api/v1/recycle-bin?page_size=100");
  const entry = (bin.body?.items || []).find(item => item.document_id === docId);
  recordCase(cases, caseRow({
    id: "EGO-DESTRUCT-RECYCLE-LIST",
    phase: "round-05-destructive", owner: "E", role: "admin", mutability: "DESTRUCTIVE",
    precondition: "synthetic document archived by admin",
    expected: ["document archive returns 200", "recycle-bin contains an entry for the document"],
    actual: { createStatus: doc.status, archiveStatus: archived?.status, entry },
    status: doc.status === 201 && archived?.status === 200 && !!entry ? "PASS" : "FAIL",
    severity: "P0", evidence: [{ kind: "json", path: `${OUT}/ego/results/round-05-destructive/recycle.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-05-destructive" },
  }));

  let restored = null;
  if (entry?.id) {
    restored = await apiJson(admin.page, `/api/v1/recycle-bin/${entry.id}/restore`, { method: "POST" });
  }
  const restoredDoc = docId ? await apiJson(admin.page, `/api/v1/documents/${docId}`) : null;
  recordCase(cases, caseRow({
    id: "EGO-DESTRUCT-RECYCLE-RESTORE",
    phase: "round-05-destructive", owner: "E", role: "admin", mutability: "DESTRUCTIVE",
    precondition: "archive entry exists in recycle bin",
    expected: ["restore returns 200", "document becomes visible again"],
    actual: { restoreStatus: restored?.status, restoredDocStatus: restoredDoc?.status, restoredDoc: restoredDoc?.body },
    status: restored?.status === 200 && restoredDoc?.status === 200 && restoredDoc?.body?.archived !== true ? "PASS" : "FAIL",
    severity: "P0", evidence: [{ kind: "json", path: `${OUT}/ego/results/round-05-destructive/recycle-restore.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-05-destructive" },
  }));

  const backup = await apiJson(admin.page, API.backups, { method: "POST", body: { type: "full" } });
  const backupId = backup.body?.id;
  const verify = backupId ? await apiJson(admin.page, `/api/v1/backups/${backupId}/verify`, { method: "POST", body: {} }) : null;
  recordCase(cases, caseRow({
    id: "EGO-DESTRUCT-BACKUP-VERIFY",
    phase: "round-05-destructive", owner: "E", role: "admin", mutability: "DESTRUCTIVE",
    precondition: "sandbox has a synthetic database and file tree",
    expected: ["full backup returns 201", "manifest/hash/integrity verification succeeds"],
    actual: { createStatus: backup.status, backup: backup.body, verifyStatus: verify?.status, verify: verify?.body },
    status: backup.status === 201 && backupId && verify?.status === 200 ? "PASS" : "FAIL",
    severity: "P0", evidence: [{ kind: "json", path: `${OUT}/ego/results/round-05-destructive/backup.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-05-destructive" },
  }));

  const auditBefore = await apiJson(admin.page, "/api/v1/audit-logs?page_size=100");
  const auditItems = auditBefore.body?.items || [];
  const victim = auditItems[0]?.id;
  const auditDelete = victim ? await apiJson(admin.page, `/api/v1/audit-logs/${victim}`, { method: "DELETE" }) : null;
  const auditAfter = await apiJson(admin.page, "/api/v1/audit-logs?page_size=100");
  const auditDeleteMarker = (auditAfter.body?.items || []).find(item => item.action === "audit_delete");
  recordCase(cases, caseRow({
    id: "EGO-DESTRUCT-AUDIT-DELETE",
    phase: "round-05-destructive", owner: "E", role: "admin", mutability: "DESTRUCTIVE",
    precondition: "at least one audit row exists",
    expected: ["single audit deletion succeeds", "a following audit_delete row preserves the deleted id list and operator"],
    actual: { deleteStatus: auditDelete?.status, marker: auditDeleteMarker, beforeCount: auditBefore.body?.total, afterCount: auditAfter.body?.total },
    status: auditDelete?.status === 200 && !!auditDeleteMarker ? "PASS" : "FAIL",
    severity: "P0", evidence: [{ kind: "json", path: `${OUT}/ego/results/round-05-destructive/audit-delete.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-05-destructive" },
  }));

  let restoreStatus = null;
  let restoreBody = null;
  if (backupId) {
    const restore = await apiJson(admin.page, `/api/v1/backups/${backupId}/restore`, { method: "POST", body: { confirm: true, reason: "E2E restore rehearsal" } });
    restoreStatus = restore.status;
    restoreBody = restore.body;
  }
  const health = await admin.page.fetch("/health");
  const maintenance = await admin.page.fetch("/api/v1/dashboard");
  recordCase(cases, caseRow({
    id: "EGO-DESTRUCT-RESTORE-MAINTENANCE",
    phase: "round-05-destructive", owner: "E", role: "admin", mutability: "DESTRUCTIVE",
    precondition: "verified full backup exists; restore is the final write of this round",
    expected: ["restore returns 200", "/health remains 200", "business API either returns 200 after restore or 503 MAINTENANCE_MODE", "no other outcome"],
    actual: { restoreStatus, restoreBody, healthStatus: health.status, dashboardStatus: maintenance.status },
    status: restoreStatus === 200 && health.status === 200 && [200, 503].includes(maintenance.status) ? "PASS" : "FAIL",
    severity: "P0", evidence: [{ kind: "json", path: `${OUT}/ego/results/round-05-destructive/restore.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-05-destructive" },
  }));

  await goto(admin.page, ORIGINS.admin, "/admin/recycle-bin");
  const recycleState = await pageState(admin.page);
  recordCase(cases, caseRow({
    id: "EGO-DESTRUCT-RECYCLE-PAGE", phase: "round-05-destructive", owner: "F", role: "admin", mutability: "READ_SHARED",
    precondition: "restore rehearsal complete", expected: ["admin recycle-bin page renders without overflow"], actual: recycleState,
    status: recycleState.path === "/admin/recycle-bin" && !recycleState.overflow ? "PASS" : "FAIL",
    severity: "P1", evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "destructive-recycle-page-390.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-05-destructive" },
  }));
  const events = await drainEvents(admin.page);
  await writeJson(`${OUT}/ego/results/round-05-destructive/recycle.json`, { doc, archived, bin, entry, restored, restoredDoc });
  await writeJson(`${OUT}/ego/results/round-05-destructive/backup.json`, { backup, verify });
  await writeJson(`${OUT}/ego/results/round-05-destructive/audit-delete.json`, { auditBefore, auditDelete, auditAfter, auditDeleteMarker });
  await writeJson(`${OUT}/ego/results/round-05-destructive/restore.json`, { restoreStatus, restoreBody, health: health.status, maintenance: maintenance.status });
  await writeJson(`${OUT}/ego/results/round-05-destructive/events.json`, events);
}

try {
  await main();
  await disposePages("round-05-destructive", ["admin"]);
  await writeResult("round-05-destructive", cases, pages);
} catch (error) {
  recordCase(cases, caseRow({
    id: "EGO-DESTRUCT-RUNNER", phase: "round-05-destructive", owner: "E", role: "system", mutability: "DESTRUCTIVE",
    precondition: "isolated synthetic sandbox", expected: ["round completes"], actual: { error: String(error), stack: error?.stack || null },
    status: "ERROR", severity: "P0", repro: { command: "python -m scripts.full_matrix.run ego round-05-destructive" },
  }));
  await disposePages("round-05-destructive", ["admin"]);
  await writeResult("round-05-destructive", cases, pages);
  process.exitCode = 1;
}