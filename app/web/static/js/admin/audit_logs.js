/**
 * admin/audit_logs.js —— 操作日志
 * REST：GET /api/v1/audit-logs（username/action/object_type/date_from/date_to/keyword + 分页）
 *      GET /api/v1/audit-logs/{id}（完整详情）
 */
(function () {
  "use strict";

  var esc = ui.escapeHtml;
  var state = { page: 1, page_size: 20 };

  var userEl = document.getElementById("f-user");
  var actionEl = document.getElementById("f-action");
  var objectEl = document.getElementById("f-object");
  var fromEl = document.getElementById("f-from");
  var toEl = document.getElementById("f-to");
  var keywordEl = document.getElementById("f-keyword");
  var tbody = document.getElementById("log-tbody");
  var pager = document.getElementById("log-pagination");
  var bulkBar = document.getElementById("audit-bulk-bar");
  var bulkNum = document.getElementById("audit-bulk-num");
  var checkAll = document.getElementById("audit-check-all");
  var selected = {};  // id -> true（翻页保留选择）

  var ACTION_LABELS = {
    login: "登录",
    login_failed: "登录失败",
    logout: "登出",
    audit_delete: "删除日志",
    create_document: "新建资料",
    update_document: "修改资料",
    replace_file: "替换文件",
    delete_document: "删除资料",
    archive_document: "归档资料",
    restore_document: "恢复资料",
    modify_validity: "修改时效",
    create_notice: "创建通知",
    update_notice: "修改通知",
    publish_notice: "发布通知",
    cancel_notice: "取消通知",
    read_notice: "阅读通知",
    confirm_notice: "历史通知动作（兼容旧日志）",
    create_category: "新建分类",
    update_category: "修改分类",
    delete_category: "删除分类",
    create_tag: "新建标签",
    update_tag: "修改标签",
    delete_tag: "删除标签",
    create_notice_type: "新建通知类型",
    update_notice_type: "修改通知类型",
    delete_notice_type: "删除通知类型",
    manage_user: "用户管理",
    create_department: "新建部门",
    update_department: "修改部门",
    delete_department: "删除部门",
    backup_create: "创建备份",
    backup_restore: "恢复备份",
    backup_delete: "删除备份",
    backup_reconcile: "认领备份目录",
    backup_verify: "校验备份",
    recycle_restore: "回收站恢复",
    recycle_purge: "回收站清理",
    change_password: "修改密码",
  };

  function buildQuery() {
    return {
      page: state.page,
      page_size: state.page_size,
      username: userEl.value.trim() || undefined,
      action: actionEl.value || undefined,
      object_type: objectEl.value || undefined,
      date_from: fromEl.value || undefined,
      date_to: toEl.value || undefined,
      keyword: keywordEl.value.trim() || undefined,
    };
  }

  function fmtDetail(detail) {
    if (!detail) return "—";
    try {
      var obj = typeof detail === "string" ? JSON.parse(detail) : detail;
      var s = JSON.stringify(obj);
      return s.length > 120 ? s.slice(0, 120) + "…" : s;
    } catch (e) {
      return String(detail).slice(0, 120);
    }
  }

  function initActionOptions() {
    var actions = Object.keys(ACTION_LABELS);
    var html = '<option value="">全部操作</option>';
    actions.forEach(function (a) {
      html += '<option value="' + a + '">' + ACTION_LABELS[a] + "</option>";
    });
    actionEl.innerHTML = html;
  }
  function render(items) {
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="8"><div class="table-empty">暂无日志记录</div></td></tr>';
      updateBulkBar();
      return;
    }
    tbody.innerHTML = items
      .map(function (l) {
        var objLabel =
          l.object_label ||
          (l.object_id ? l.object_type + "#" + l.object_id : (l.object_type || "—"));
        var checked = selected[l.id] ? " checked" : "";
        return (
          "<tr>" +
          '<td><input type="checkbox" class="audit-row-check" data-id="' + l.id + '" aria-label="选择 ' + esc(ui.formatDateTime(l.created_at)) + ' 的日志"' + checked + "></td>" +
          '<td class="text-muted" style="font-size:13px;white-space:nowrap;">' + esc(ui.formatDateTime(l.created_at)) + "</td>" +
          "<td>" + esc(l.username || "—") + "</td>" +
          "<td><span class='tag-chip'>" + esc(ACTION_LABELS[l.action] || l.action) + "</span></td>" +
          "<td>" + esc(objLabel) + "</td>" +
          '<td class="text-muted" style="font-size:12px;max-width:280px;word-break:break-all;">' +
          '<code style="font-size:12px;">' + esc(fmtDetail(l.detail)) + "</code></td>" +
          '<td class="text-muted" style="font-size:12px;white-space:nowrap;">' + esc(l.ip || "—") + "</td>" +
          '<td class="row-actions"><button class="btn btn-sm btn-ghost" data-action="detail" data-id="' + l.id + '">详情</button>' +
          '<button class="btn btn-sm btn-danger-outline" data-action="del" data-id="' + l.id + '">删除</button></td>' +
          "</tr>"
        );
      })
      .join("");
    tbody.querySelectorAll(".audit-row-check").forEach(function (cb) {
      cb.addEventListener("change", function () {
        var id = parseInt(cb.dataset.id, 10);
        if (cb.checked) selected[id] = true;
        else delete selected[id];
        updateBulkBar();
      });
    });
    syncCheckAll();
    updateBulkBar();
  }

  function fullDetailText(detail) {
    if (detail === null || detail === undefined || detail === "") return "—";
    try {
      var obj = typeof detail === "string" ? JSON.parse(detail) : detail;
      return JSON.stringify(obj, null, 2);
    } catch (e) {
      return String(detail);
    }
  }

  async function showLogDetail(id, btn) {
    if (btn) ui.btnLoading(btn, true, "加载中…");
    try {
      var d = await api.get("/api/v1/audit-logs/" + id);
      var objLabel = d.object_label ||
        (d.object_id ? (d.object_type || "对象") + "#" + d.object_id : (d.object_type || "—"));
      ui.openModal(
        '<div class="doc-meta-grid">' +
        '<div class="meta-item"><div class="meta-label">时间</div><div class="meta-value">' + esc(ui.formatDateTime(d.created_at)) + "</div></div>" +
        '<div class="meta-item"><div class="meta-label">操作人</div><div class="meta-value">' + esc(d.username || "—") +
          (d.user_id ? '<span class="text-muted" style="font-size:12px;">（用户 #' + esc(d.user_id) + "）</span>" : "") + "</div></div>" +
        '<div class="meta-item"><div class="meta-label">操作类型</div><div class="meta-value">' + esc(ACTION_LABELS[d.action] || d.action) + "</div></div>" +
        '<div class="meta-item"><div class="meta-label">对象</div><div class="meta-value">' + esc(objLabel) +
          (d.object_type ? '<div class="text-muted" style="font-size:12px;">' + esc(d.object_type) + "</div>" : "") + "</div></div>" +
        '<div class="meta-item"><div class="meta-label">IP</div><div class="meta-value">' + esc(d.ip || "—") + "</div></div>" +
        '<div class="meta-item"><div class="meta-label">User-Agent</div><div class="meta-value" style="font-size:12px;word-break:break-all;">' + esc(d.user_agent || "—") + "</div></div>" +
        "</div>" +
        '<div class="mt-3"><h4 style="font-size:14px;">完整详情</h4><pre style="background:var(--bg-hover);padding:12px;border-radius:8px;font-size:12px;overflow:auto;max-height:360px;white-space:pre-wrap;word-break:break-word;">' +
        esc(fullDetailText(d.detail)) + "</pre></div>",
        { title: "操作日志 #" + id, size: "lg" }
      );
      if (btn) ui.btnLoading(btn, false);
    } catch (err) {
      if (btn) ui.btnLoading(btn, false);
      ui.toast(err.message, "error");
    }
  }

  tbody.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-action]");
    if (!btn) return;
    var id = parseInt(btn.dataset.id, 10);
    if (btn.dataset.action === "detail") {
      showLogDetail(id, btn);
    } else if (btn.dataset.action === "del") {
      deleteLogs([id], btn);
    }
  });

  function selectedIds() {
    return Object.keys(selected).map(function (k) { return parseInt(k, 10); });
  }

  function updateBulkBar() {
    var n = selectedIds().length;
    bulkNum.textContent = n;
    bulkBar.hidden = n === 0;
    // 2026-09-14 缺陷修复：原实现无条件把「全选本页」置为未选中，导致点击全选后
    // 该复选框自己弹回未选（行却已全选），状态与视图不自洽。改为按当页勾选态同步。
    syncCheckAll();
  }

  function syncCheckAll() {
    var boxes = tbody.querySelectorAll(".audit-row-check");
    if (checkAll && boxes.length) {
      checkAll.checked = Array.prototype.every.call(boxes, function (b) { return b.checked; });
    }
  }

  function deleteLogs(ids, btn) {
    ui.confirmModal({
      title: "删除操作日志",
      danger: true,
      message:
        "将<strong>物理删除</strong>选中的 " + ids.length + " 条操作日志，不可恢复。" +
        "删除动作本身将写入一条审计记录（audit_delete）。",
      confirmText: "删除",
    }).then(async function (ok) {
      if (!ok) return;
      if (btn) ui.btnLoading(btn, true, "删除中…");
      try {
        var r = await api.post("/api/v1/audit-logs/batch-delete", { ids: ids });
        selected = {};
        if (btn) ui.btnLoading(btn, false);  // 成功也要复位：批量条不随列表刷新重建
        ui.toast("已删除 " + r.deleted.length + " 条日志", "success");
        load();
      } catch (err) {
        if (btn) ui.btnLoading(btn, false);
        ui.toast(err.message, "error");
      }
    });
  }

  if (checkAll) {
    checkAll.addEventListener("change", function () {
      tbody.querySelectorAll(".audit-row-check").forEach(function (cb) {
        cb.checked = checkAll.checked;
        var id = parseInt(cb.dataset.id, 10);
        if (checkAll.checked) selected[id] = true;
        else delete selected[id];
      });
      updateBulkBar();
    });
  }
  document.getElementById("btn-audit-batch-delete").addEventListener("click", function () {
    deleteLogs(selectedIds(), this);
  });
  document.getElementById("btn-audit-bulk-cancel").addEventListener("click", function () {
    selected = {};
    tbody.querySelectorAll(".audit-row-check").forEach(function (cb) { cb.checked = false; });
    if (checkAll) checkAll.checked = false;
    updateBulkBar();
  });

  async function load() {
    tbody.innerHTML = '<tr><td colspan="8"><div class="table-empty">加载中…</div></td></tr>';
    try {
      var data = await api.get("/api/v1/audit-logs", buildQuery());
      render(data.items);
      ui.renderPagination(pager, data.page, data.pages, function (p) {
        state.page = p;
        load();
      });
    } catch (e) {
      tbody.innerHTML = '<tr><td colspan="8"><div class="table-empty text-danger">' + esc(e.message) + "</div></td></tr>";
    }
  }

  document.getElementById("btn-search").addEventListener("click", function () {
    state.page = 1;
    load();
  });
  document.getElementById("btn-reset").addEventListener("click", function () {
    userEl.value = "";
    actionEl.value = "";
    objectEl.value = "";
    fromEl.value = "";
    toEl.value = "";
    keywordEl.value = "";
    state.page = 1;
    load();
  });
  [userEl, keywordEl].forEach(function (input) {
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        state.page = 1;
        load();
      }
    });
  });

  initActionOptions();
  load();
})();