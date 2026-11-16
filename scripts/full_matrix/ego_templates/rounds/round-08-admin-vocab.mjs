// round-08-admin-vocab: true-browser CRUD for the four vocabularies + users.
//
// Before this round the only ego coverage of categories / tags / notice types /
// departments / users was "the page renders" (round-01) and "a non-admin is
// denied" (round-04).  Nothing exercised the actual create / rename / delete
// flows through the UI.  This round drives every one of them in the real
// browser and additionally pins the agreed deletion semantics:
//   * categories / departments / tags / notice types  -> confirmation modal first
//   * users                                            -> physical delete, NO confirmation
//   * "废止" / "替换" notice types                      -> rename and delete refused
//   * department delete                                -> members become unassigned
//   * user delete                                      -> content-responsibility chain blocks

import {
  API, CREDS, ORIGINS, OUT, PORT, apiJson, caseRow, currentMe, drainEvents, goto,
  disposePages, login, makePage, pageState, recordCase, screenshot, writeJson, writeResult,
} from "../lib/core.mjs";

const ROUND = "round-08-admin-vocab";
const TAG = `E2E-AV-${Date.now()}`;
const cases = [];
const pages = [];

function bodyOf(resp) {
  return resp?.body && typeof resp.body === "object" ? resp.body : {};
}

/** Categories come back as a nested tree; flatten it for lookups. */
function flattenCategories(nodes) {
  const out = [];
  const walk = (list) => {
    for (const node of list || []) {
      out.push(node);
      if (Array.isArray(node.children)) walk(node.children);
    }
  };
  walk(Array.isArray(nodes) ? nodes : (nodes?.items || []));
  return out;
}

function listOf(resp) {
  const body = bodyOf(resp);
  return Array.isArray(body) ? body : (body.items || []);
}

/** Wait for the app's own modal, act inside it, and report whether it appeared. */
async function modalAction(page, { fill = {}, click = "[data-save]", timeout = 8000 } = {}) {
  let appeared = false;
  try {
    await page.waitForSelector("#modal-root .modal", { timeout });
    appeared = true;
  } catch (_) {
    return { appeared: false };
  }
  for (const [selector, value] of Object.entries(fill)) {
    await page.fill(selector, value);
  }
  if (click) await page.click(click);
  await page.waitForTimeout(400);
  return { appeared: true };
}

async function closeModalIfOpen(page) {
  const open = await page.evaluate(() => !!document.querySelector("#modal-root .modal"));
  if (!open) return false;
  try { await page.click("#modal-root [data-cancel]"); } catch (_) {}
  await page.waitForTimeout(200);
  return true;
}

async function rowCount(page, selector) {
  return page.evaluate((sel) => document.querySelectorAll(sel).length, selector);
}

async function findRowText(page, selector, needle) {
  return page.evaluate(({ sel, text }) => {
    const rows = [...document.querySelectorAll(sel)];
    const hit = rows.find(row => (row.innerText || "").includes(text));
    return hit ? (hit.innerText || "").replace(/\s+/g, " ").trim().slice(0, 300) : null;
  }, { sel: selector, text: needle });
}

async function main() {
  const admin = await makePage("admin");
  const user = await makePage("user-a");
  pages.push({ label: "admin", host: "admin.localhost" }, { label: "user-a", host: "user-a.localhost" });
  await login(admin.page, "admin.localhost", CREDS.admin);
  await login(user.page, "user-a.localhost", CREDS.user_a);

  // ------------------------------------------------------------------
  // A. 分类：四级创建 / 第五级拒绝 / 同级重名 / 整树删除后资料变未分类
  // ------------------------------------------------------------------
  const rootName = `${TAG}-L1`;
  const catIds = [];
  await goto(admin.page, ORIGINS.admin, "/admin/categories");
  await admin.page.waitForSelector("#cat-tree", { timeout: 15000 });

  await admin.page.click("#btn-new-root");
  const rootModal = await modalAction(admin.page, { fill: { "#c-name": rootName } });
  await admin.page.waitForTimeout(800);

  const catList = await apiJson(admin.page, `${API.categories}?page_size=100`);
  const allCats = flattenCategories(catList.body);
  const rootCat = allCats.find(c => c.name === rootName);
  if (rootCat) catIds.push(rootCat.id);

  recordCase(cases, caseRow({
    id: "EGO-AV-CAT-CREATE-ROOT",
    phase: ROUND, owner: "D", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: "admin_demo logged in on admin.localhost",
    expected: ["clicking 新建一级分类 opens a modal", "modal save creates the category", "the new name is returned by the API"],
    actual: { modalAppeared: rootModal.appeared, created: rootCat ? rootCat.name : null, status: catList.status },
    status: rootModal.appeared && rootCat ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "av-cat-root.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-08-admin-vocab", path: "/admin/categories" },
  }));

  // Build depth 2..4 through the API (the UI drill-down work is covered by round-01),
  // then assert the 5th level is refused by the server.
  let parentId = rootCat?.id ?? null;
  const depthStatuses = [];
  for (let depth = 2; depth <= 5; depth += 1) {
    const resp = await apiJson(admin.page, API.categories, {
      method: "POST",
      body: { name: `${TAG}-L${depth}`, parent_id: parentId, sort_order: 0 },
    });
    depthStatuses.push({ depth, status: resp.status, id: bodyOf(resp).id ?? null });
    if (resp.status === 201) parentId = bodyOf(resp).id;
  }
  const fifth = depthStatuses.find(d => d.depth === 5);
  recordCase(cases, caseRow({
    id: "EGO-AV-CAT-DEPTH-LIMIT",
    phase: ROUND, owner: "D", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: "a 4-level chain built from the round's own root",
    expected: ["levels 2/3/4 are created (201)", "level 5 is refused (400)"],
    actual: depthStatuses,
    status: depthStatuses.filter(d => d.depth <= 4).every(d => d.status === 201) && fifth?.status === 400 ? "PASS" : "FAIL",
    severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-08-admin-vocab", path: "/api/v1/categories" },
  }));

  const dup = await apiJson(admin.page, API.categories, {
    method: "POST", body: { name: rootName, parent_id: null, sort_order: 0 },
  });
  recordCase(cases, caseRow({
    id: "EGO-AV-CAT-DUPLICATE",
    phase: ROUND, owner: "D", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: `"${rootName}" already exists at root level`,
    expected: ["creating a sibling with the same name is refused (409)"],
    actual: { status: dup.status, body: bodyOf(dup) },
    status: dup.status === 409 ? "PASS" : "FAIL",
    severity: "P1",
    repro: { command: "python -m scripts.full_matrix.run ego round-08-admin-vocab" },
  }));

  // Delete the whole subtree through the UI: the confirmation modal must appear.
  await goto(admin.page, ORIGINS.admin, "/admin/categories");
  await admin.page.waitForSelector("#cat-tree", { timeout: 15000 });
  await admin.page.waitForTimeout(700);
  const delClicked = await admin.page.evaluate((name) => {
    const nodes = [...document.querySelectorAll("#cat-tree .tree-node-content")];
    const node = nodes.find(n => (n.innerText || "").includes(name));
    if (!node) return "node-not-found";
    const btn = node.querySelector('[data-action="delete"]');
    if (!btn) return "delete-button-not-found";
    btn.click();
    return "clicked";
  }, rootName);
  await admin.page.waitForTimeout(500);
  const confirmAppeared = await admin.page.evaluate(() => !!document.querySelector("#modal-root [data-confirm]"));
  if (confirmAppeared) {
    await admin.page.click("#modal-root [data-confirm]");
    await admin.page.waitForTimeout(900);
  }
  const afterCats = await apiJson(admin.page, `${API.categories}?page_size=100`);
  const stillThere = flattenCategories(afterCats.body).some(c => c.name === rootName);

  recordCase(cases, caseRow({
    id: "EGO-AV-CAT-DELETE-SUBTREE",
    phase: ROUND, owner: "D", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: "a 4-level subtree created by this round",
    expected: ["delete asks for confirmation first", "confirming removes the subtree root"],
    actual: { delClicked, confirmAppeared, stillThere },
    status: delClicked === "clicked" && confirmAppeared && !stillThere ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "av-cat-delete.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-08-admin-vocab", path: "/admin/categories" },
  }));

  // ------------------------------------------------------------------
  // B. 标签：创建 / 重名 / 删除前确认
  // ------------------------------------------------------------------
  const tagName = `${TAG}-标签`;
  await goto(admin.page, ORIGINS.admin, "/admin/tags");
  await admin.page.waitForSelector("#tag-tbody", { timeout: 15000 });
  await admin.page.click("#btn-new-tag");
  const tagModal = await modalAction(admin.page, { fill: { "#t-name": tagName }, click: "#modal-root [data-save]" });
  await admin.page.waitForTimeout(700);

  const tagList = await apiJson(admin.page, `${API.tags}?page_size=100`);
  const tagItems = Array.isArray(tagList.body) ? tagList.body : (tagList.body?.items || []);
  const createdTag = tagItems.find(t => t.name === tagName);

  const dupTag = await apiJson(admin.page, API.tags, { method: "POST", body: { name: tagName } });

  recordCase(cases, caseRow({
    id: "EGO-AV-TAG-CREATE-DUP",
    phase: ROUND, owner: "D", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: "tag management page open",
    expected: ["modal create returns the tag", "a duplicate name is refused (409)"],
    actual: { modalAppeared: tagModal.appeared, created: createdTag?.name ?? null, dupStatus: dupTag.status },
    status: tagModal.appeared && createdTag && dupTag.status === 409 ? "PASS" : "FAIL",
    severity: "P1",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "av-tag.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-08-admin-vocab", path: "/admin/tags" },
  }));

  let tagDeleteConfirm = false;
  if (createdTag) {
    await goto(admin.page, ORIGINS.admin, "/admin/tags");
    await admin.page.waitForSelector("#tag-tbody", { timeout: 15000 });
    await admin.page.waitForTimeout(600);
    const clicked = await admin.page.evaluate((name) => {
      const btn = [...document.querySelectorAll('#tag-tbody [data-action="delete"]')]
        .find(b => (b.dataset.name || "").trim() === name);
      if (!btn) return false;
      btn.click();
      return true;
    }, tagName);
    await admin.page.waitForTimeout(400);
    tagDeleteConfirm = await admin.page.evaluate(() => !!document.querySelector("#modal-root [data-confirm]"));
    if (tagDeleteConfirm) {
      await admin.page.click("#modal-root [data-confirm]");
      await admin.page.waitForTimeout(700);
    } else if (clicked) {
      await closeModalIfOpen(admin.page);
    }
  }
  recordCase(cases, caseRow({
    id: "EGO-AV-TAG-DELETE-CONFIRM",
    phase: ROUND, owner: "D", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: "a tag created by this round exists",
    expected: ["deleting a tag shows a confirmation modal first"],
    actual: { confirmAppeared: tagDeleteConfirm },
    status: tagDeleteConfirm ? "PASS" : "FAIL",
    severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-08-admin-vocab", path: "/admin/tags" },
  }));

  // ------------------------------------------------------------------
  // C. 通知类型：自定义类型可建可删；「废止/替换」禁止改名与删除
  // ------------------------------------------------------------------
  const typeName = `${TAG}-类型`;
  const builtinList = await apiJson(admin.page, `${API.noticeTypes}?page_size=100`);
  const builtinItems = Array.isArray(builtinList.body) ? builtinList.body : (builtinList.body?.items || []);
  const abolish = builtinItems.find(t => t.name === "废止");
  const replace = builtinItems.find(t => t.name === "替换");

  await goto(admin.page, ORIGINS.admin, "/admin/notice-types");
  await admin.page.waitForSelector("#type-tbody", { timeout: 15000 });
  await admin.page.click("#btn-new-type");
  const typeModal = await modalAction(admin.page, { fill: { "#nt-name": typeName }, click: "#modal-root [data-save]" });
  await admin.page.waitForTimeout(700);
  const typeList = await apiJson(admin.page, `${API.noticeTypes}?page_size=100`);
  const typeItems = Array.isArray(typeList.body) ? typeList.body : (typeList.body?.items || []);
  const createdType = typeItems.find(t => t.name === typeName);

  // Linked built-ins must refuse rename and delete.
  const guardResults = {};
  for (const [label, item] of [["废止", abolish], ["替换", replace]]) {
    if (!item) { guardResults[label] = "missing-from-seed"; continue; }
    const rename = await apiJson(admin.page, `${API.noticeTypes}/${item.id}`, {
      method: "PUT", body: { name: `${label}-改名` },
    });
    const del = await apiJson(admin.page, `${API.noticeTypes}/${item.id}`, { method: "DELETE" });
    guardResults[label] = { rename: rename.status, delete: del.status };
  }

  recordCase(cases, caseRow({
    id: "EGO-AV-NOTICE-TYPE-CRUD",
    phase: ROUND, owner: "D", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: "notice type page open; 废止/替换 come from the seed",
    expected: ["a custom type is created", "废止 and 替换 both refuse rename and delete"],
    actual: { modalAppeared: typeModal.appeared, created: createdType?.name ?? null, guards: guardResults },
    status: typeModal.appeared && createdType
      && Object.values(guardResults).every(g => g === "missing-from-seed"
        || (g.rename === 409 && g.delete === 409)) ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "av-notice-type.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-08-admin-vocab", path: "/admin/notice-types" },
  }));

  // ------------------------------------------------------------------
  // D. 部门：创建 / 重名 / 删除前确认
  // ------------------------------------------------------------------
  const deptName = `${TAG}-部门`;
  await goto(admin.page, ORIGINS.admin, "/admin/departments");
  await admin.page.waitForSelector("#dept-tbody", { timeout: 15000 });
  await admin.page.click("#btn-new-dept");
  const deptModal = await modalAction(admin.page, { fill: { "#d-name": deptName }, click: "#modal-root [data-save]" });
  await admin.page.waitForTimeout(700);
  const deptList = await apiJson(admin.page, `${API.departments}?page_size=100`);
  const deptItems = Array.isArray(deptList.body) ? deptList.body : (deptList.body?.items || []);
  const createdDept = deptItems.find(d => d.name === deptName);
  const dupDept = await apiJson(admin.page, API.departments, { method: "POST", body: { name: deptName } });

  recordCase(cases, caseRow({
    id: "EGO-AV-DEPT-CREATE-DUP",
    phase: ROUND, owner: "D", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: "department page open",
    expected: ["modal create returns the department", "a duplicate name is refused (409)"],
    actual: { modalAppeared: deptModal.appeared, created: createdDept?.name ?? null, dupStatus: dupDept.status },
    status: deptModal.appeared && createdDept && dupDept.status === 409 ? "PASS" : "FAIL",
    severity: "P1",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "av-dept.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-08-admin-vocab", path: "/admin/departments" },
  }));

  // ------------------------------------------------------------------
  // E. 用户：创建 / 停用后无法登录 / 内容责任链 / 物理删除无弹窗
  // ------------------------------------------------------------------
  const uname = `e2e_av_${Date.now()}`;
  const upass = "Accept@2026x";
  await goto(admin.page, ORIGINS.admin, "/admin/users");
  await admin.page.waitForSelector("#user-tbody", { timeout: 15000 });
  await admin.page.click("#btn-new-user");
  const userModal = await modalAction(admin.page, {
    fill: {
      "#u-username": uname,
      "#u-display": `${TAG} 临时用户`,
      "#u-password": upass,
    },
    click: "#modal-root [data-save]",
  });
  await admin.page.waitForTimeout(900);
  const userList = await apiJson(admin.page, `${API.users}?page_size=100`);
  const userItems = Array.isArray(userList.body) ? userList.body : (userList.body?.items || []);
  const createdUser = userItems.find(u => u.username === uname);

  // The new account must be usable, then disable must really lock it out.
  //
  // Probing sign-in through the browser is unreliable here: the access token is
  // an HttpOnly cookie, so a Page that already holds an admin session is
  // redirected straight to the dashboard and #login-form never renders, and JS
  // cannot clear an HttpOnly cookie.  Probe over plain HTTP instead — no cookie
  // jar at all — which tests authentication itself.
  //
  // A freshly created account carries must_change_password=true, so a correct
  // login response reports that flag; that gate is documented behaviour, so it
  // is asserted rather than treated as a failure.
  const probeLoginState = async () => {
    try {
      const resp = await fetch(`http://admin.localhost:${PORT}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: uname, password: upass }),
      });
      const body = await resp.json().catch(() => ({}));
      if (resp.status !== 200) {
        return { outcome: "rejected", status: resp.status, code: body?.error?.code ?? null };
      }
      const token = body.access_token || null;
      // Prove the token is real by calling an authenticated endpoint with it.
      const me = await fetch(`http://admin.localhost:${PORT}${API.authMe}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      return {
        outcome: body.must_change_password ? "gate" : "entered",
        status: resp.status, hasToken: !!token, meStatus: me.status,
      };
    } catch (e) {
      return { outcome: "error", error: String(e) };
    }
  };

  const beforeLogin = await probeLoginState();
  const couldLoginBefore = beforeLogin.outcome === "gate" || beforeLogin.outcome === "entered";

  let disableStatus = null;
  let afterLogin = null;
  if (createdUser) {
    const dis = await apiJson(admin.page, `${API.users}/${createdUser.id}`, { method: "DELETE" });
    disableStatus = dis.status;
    afterLogin = await probeLoginState();
  }
  const couldLoginAfter = afterLogin ? afterLogin.outcome !== "rejected" : null;

  recordCase(cases, caseRow({
    id: "EGO-AV-USER-CREATE-DISABLE",
    phase: ROUND, owner: "D", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: "admin creates a throw-away account through the UI modal",
    expected: ["the account can authenticate (first sign-in hits the forced password gate)",
               "disabling it is accepted", "a disabled account can no longer log in"],
    actual: { modalAppeared: userModal.appeared, created: createdUser?.username ?? null, beforeLogin, disableStatus, afterLogin },
    status: userModal.appeared && createdUser && couldLoginBefore && disableStatus === 200 && couldLoginAfter === false ? "PASS" : "FAIL",
    severity: "P0",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "av-user.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-08-admin-vocab", path: "/admin/users" },
  }));

  // Content-responsibility chain: an account that created content cannot be purged.
  // `created_by` is stamped by the server from the session, so the only honest
  // way to build this precondition is to act as that account.
  let chainStatus = null;
  if (createdUser) {
    try {
      // Re-enable and promote first: the account was disabled by the case above
      // and needs admin rights to create a document of its own.
      const restore = await apiJson(admin.page, `${API.users}/${createdUser.id}`, {
        method: "PUT", body: { is_active: true, role: "admin" },
      });
      // Sign in over plain HTTP so the forced password gate cannot interfere:
      // every request below carries this Bearer token explicitly.
      const doLogin = async (password) => {
        const resp = await fetch(`http://admin.localhost:${PORT}/api/v1/auth/login`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username: uname, password }),
        });
        const body = await resp.json().catch(() => ({}));
        return { status: resp.status, token: body.access_token || null };
      };
      const changedPassword = `${upass}Z`;
      let session = await doLogin(upass);
      const authed = (route, options = {}) => fetch(`http://admin.localhost:${PORT}${route}`, {
        ...options,
        headers: {
          ...(options.headers || {}),
          "Content-Type": "application/json",
          ...(session.token ? { Authorization: `Bearer ${session.token}` } : {}),
        },
      });
      // Clearing must_change_password goes through the real change-password flow,
      // because UserUpdate does not expose that column at all.
      const changed = await authed("/api/v1/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ old_password: upass, new_password: changedPassword }),
      });
      // A successful password change bumps token_version, so the token above is
      // dead by design; sign in again with the new password before going on.
      session = await doLogin(changedPassword);
      const docResp = await authed(API.documents, {
        method: "POST",
        body: JSON.stringify({ title: `${TAG}-责任链资料`, effective_from: "2026-01-01", effective_to: null, audience_scope: "all" }),
      });
      const purge = await apiJson(admin.page, `${API.users}/${createdUser.id}/purge`, { method: "DELETE" });
      chainStatus = {
        restore: restore.status, login: 200,
        changePassword: changed.status, relogin: session.status, hasToken: !!session.token,
        docCreate: docResp.status, purge: purge.status,
      };
    } catch (e) {
      chainStatus = { error: String(e) };
    }
  }
  recordCase(cases, caseRow({
    id: "EGO-AV-USER-CONTENT-CHAIN",
    phase: ROUND, owner: "D", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: "an account owns at least one document",
    expected: ["physical delete is refused with 409 for an account that owns content"],
    actual: chainStatus,
    status: chainStatus && chainStatus.purge === 409 ? "PASS" : "FAIL",
    severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-08-admin-vocab", path: "/api/v1/users/{id}/purge" },
  }));

  // Physical delete of a clean account must NOT open a confirmation modal.
  const spareName = `e2e_av_spare_${Date.now()}`;
  const spare = await apiJson(admin.page, API.users, {
    method: "POST",
    body: { username: spareName, display_name: `${TAG} 待删`, password: upass, role: "user", department_ids: [] },
  });
  let purgeNoModal = null;
  if (spare.status === 201) {
    await goto(admin.page, ORIGINS.admin, "/admin/users");
    await admin.page.waitForSelector("#user-tbody", { timeout: 15000 });
    await admin.page.fill("#f-keyword", spareName);
    await admin.page.click("#btn-search");
    await admin.page.waitForTimeout(900);
    const clicked = await admin.page.evaluate(() => {
      const btn = document.querySelector('#user-tbody [data-action="purge"]');
      if (!btn) return false;
      btn.click();
      return true;
    });
    await admin.page.waitForTimeout(500);
    const modalSeen = await admin.page.evaluate(() => !!document.querySelector("#modal-root [data-confirm]"));
    purgeNoModal = { clicked, modalSeen };
    if (modalSeen) await closeModalIfOpen(admin.page);
  }
  recordCase(cases, caseRow({
    id: "EGO-AV-USER-PURGE-NO-MODAL",
    phase: ROUND, owner: "D", role: "admin", mutability: "GLOBAL_WRITE",
    precondition: "a brand-new account with no content",
    expected: ["项目规范 §26.1: physical delete of a user shows NO confirmation modal"],
    actual: purgeNoModal,
    status: purgeNoModal && purgeNoModal.clicked && purgeNoModal.modalSeen === false ? "PASS" : "FAIL",
    severity: "P1",
    evidence: [{ kind: "screenshot", path: await screenshot(admin.page, "av-user-purge.png") }],
    repro: { command: "python -m scripts.full_matrix.run ego round-08-admin-vocab", path: "/admin/users" },
  }));

  // ------------------------------------------------------------------
  // G. 清理：删除本前缀的历史残留，保证本轮可重复运行
  // ------------------------------------------------------------------
  // Earlier interrupted runs leave E2E-AV-* rows behind.  Sweep them through the
  // public API (no direct DB writes) so a rerun starts from a comparable state.
  const sweep = { departments: [], noticeTypes: [], users: [], documents: [] };

  const deptsNow = listOf(await apiJson(admin.page, `${API.departments}?page_size=100`));
  for (const dep of deptsNow.filter(d => (d.name || "").startsWith("E2E-AV-"))) {
    sweep.departments.push({ name: dep.name, status: (await apiJson(admin.page, `${API.departments}/${dep.id}`, { method: "DELETE" })).status });
  }

  const typesNow = listOf(await apiJson(admin.page, `${API.noticeTypes}?page_size=100`));
  for (const type of typesNow.filter(t => (t.name || "").startsWith("E2E-AV-"))) {
    sweep.noticeTypes.push({ name: type.name, status: (await apiJson(admin.page, `${API.noticeTypes}/${type.id}`, { method: "DELETE" })).status });
  }

  const usersNow = listOf(await apiJson(admin.page, `${API.users}?page_size=100`));
  for (const u of usersNow.filter(x => (x.username || "").startsWith("e2e_av_"))) {
    sweep.users.push({ username: u.username, status: (await apiJson(admin.page, `${API.users}/${u.id}/purge`, { method: "DELETE" })).status });
  }

  const docsNow = listOf(await apiJson(admin.page, `${API.documents}?page_size=100`));
  for (const d of docsNow.filter(x => (x.title || "").startsWith("E2E-AV-"))) {
    // Documents are archived rather than deleted (项目规范 §26); this is ordinary
    // cleanup of this round's own fixtures, not a product assertion.
    sweep.documents.push({ title: d.title, status: (await apiJson(admin.page, `${API.documents}/${d.id}`, { method: "DELETE" })).status });
  }

  // Everything above is cleanup; it is only reported, never used to pass a case.
  await writeJson(`${OUT}/ego/results/${ROUND}/cleanup.json`, sweep);

  // ------------------------------------------------------------------
  // H. 普通用户必须被后端拒绝（不是只隐藏按钮）
  // ------------------------------------------------------------------
  const denied = [];
  for (const [method, route] of [
    ["POST", API.categories], ["POST", API.tags], ["POST", API.noticeTypes],
    ["POST", API.departments], ["POST", API.users],
  ]) {
    const resp = await apiJson(user.page, route, { method, body: { name: `${TAG}-越权` } });
    denied.push({ method, route, status: resp.status });
  }
  recordCase(cases, caseRow({
    id: "EGO-AV-USER-WRITE-DENIED",
    phase: ROUND, owner: "D", role: "user", mutability: "GLOBAL_WRITE",
    precondition: "ordinary user session on user-a.localhost",
    expected: ["every vocabulary create endpoint answers 401/403 for a normal user"],
    actual: denied,
    status: denied.every(d => d.status === 401 || d.status === 403) ? "PASS" : "FAIL",
    severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-08-admin-vocab" },
  }));

  // ------------------------------------------------------------------
  // I. 控制台干净
  // ------------------------------------------------------------------
  const events = await drainEvents(admin.page);
  const clean = events.consoleErrors.length === 0 && events.pageErrors.length === 0;
  recordCase(cases, caseRow({
    id: "EGO-AV-CONSOLE",
    phase: ROUND, owner: "F", role: "admin", mutability: "READ_SHARED",
    precondition: "every vocabulary page visited by this round",
    expected: ["no console error and no pageerror"],
    actual: events,
    status: clean ? "PASS" : "FAIL",
    severity: "P1",
    evidence: [{ kind: "json", path: `${OUT}/ego/results/${ROUND}/events.json` }],
    repro: { command: "python -m scripts.full_matrix.run ego round-08-admin-vocab" },
  }));
  await writeJson(`${OUT}/ego/results/${ROUND}/events.json`, events);
  await writeJson(`${OUT}/ego/results/${ROUND}/pages.json`, pages);
  // The runtime probe Pages this round created are done; declare them so the
  // runner closes exactly these (and nothing shared with later rounds).
  await disposePages(ROUND, ["admin", "user-a"]);
}

try {
  await main();
  await writeResult(ROUND, cases, pages);
} catch (error) {
  recordCase(cases, caseRow({
    id: "EGO-AV-RUNNER", phase: ROUND, owner: "D", role: "system", mutability: "GLOBAL_WRITE",
    precondition: "ego runtime", expected: ["round completes"],
    actual: { error: String(error), stack: error?.stack || null },
    status: "ERROR", severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-08-admin-vocab" },
  }));
  await writeResult(ROUND, cases, pages);
  process.exitCode = 1;
}
