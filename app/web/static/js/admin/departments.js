/**
 * admin/departments.js —— 部门管理
 * REST：GET/POST/PUT/DELETE /api/v1/departments（删除=物理删除，有启用成员 → 409 IN_USE）
 */
(function () {
  "use strict";

  var esc = ui.escapeHtml;
  var state = { page: 1, page_size: 15 };
  var keywordEl = document.getElementById("f-keyword");
  var tbody = document.getElementById("dept-tbody");
  var pager = document.getElementById("dept-pagination");

  function buildQuery() {
    return {
      page: state.page,
      page_size: state.page_size,
      keyword: keywordEl.value.trim(),
      with_member_count: 1,
    };
  }

  function render(items) {
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="4"><div class="table-empty">没有符合条件的部门</div></td></tr>';
      return;
    }
    tbody.innerHTML = items
      .map(function (d) {
        return (
          "<tr>" +
          "<td><div class=\"row-title\">" + esc(d.name) + "</div></td>" +
          "<td class=\"text-muted\">" + esc(d.description || "—") + "</td>" +
          "<td>" + esc(d.member_count !== undefined ? d.member_count : "—") + "</td>" +
          '<td class="row-actions">' +
          '<button class="btn btn-sm btn-ghost" data-action="edit" data-id="' + d.id + '">编辑</button>' +
          '<button class="btn btn-sm btn-danger-outline" data-action="delete" data-id="' + d.id + '" data-name="' + esc(d.name) + '">删除</button>' +
          "</td></tr>"
        );
      })
      .join("");
  }

  async function load() {
    tbody.innerHTML = '<tr><td colspan="4"><div class="table-empty">加载中…</div></td></tr>';
    try {
      var data = await api.get("/api/v1/departments", buildQuery());
      render(data.items);
      ui.renderPagination(pager, data.page, data.pages, function (p) {
        state.page = p;
        load();
      });
    } catch (e) {
      tbody.innerHTML = '<tr><td colspan="4"><div class="table-empty text-danger">' + esc(e.message) + "</div></td></tr>";
    }
  }

  function deptModal(dep) {
    var isEdit = !!dep;
    var overlay = ui.openModal(
      '<form id="dept-form">' +
      '<div class="form-group"><label class="form-label"><span class="required">部门名称</span></label>' +
      '<input class="form-control" id="d-name" maxlength="128" value="' + esc(dep ? dep.name : "") + '"></div>' +
      '<div class="form-group mt-3"><label class="form-label">说明</label>' +
      '<textarea class="form-control" id="d-desc" rows="2">' + esc(dep ? dep.description || "" : "") + "</textarea></div>" +
      '<div class="modal-footer" style="margin:16px -20px -20px;">' +
      '<button type="button" class="btn btn-secondary" data-cancel>取消</button>' +
      '<button type="button" class="btn btn-primary" data-save>' + (isEdit ? "保存" : "创建") + "</button></div>" +
      "</form>",
      { title: isEdit ? "编辑部门" : "新建部门", size: "sm" }
    );
    return overlay;
  }

  async function submitDept(overlay, dep) {
    var nameEl = overlay.querySelector("#d-name");
    var name = nameEl.value.trim();
    if (!name) {
      nameEl.closest(".form-group").classList.add("has-error");
      return;
    }
    var body = {
      name: name,
      description: overlay.querySelector("#d-desc").value.trim() || null,
    };
    var save = overlay.querySelector("[data-save]");
    ui.btnLoading(save, true, "保存中…");
    try {
      if (dep) {
        await api.put("/api/v1/departments/" + dep.id, body);
        ui.toast("部门已更新", "success");
      } else {
        await api.post("/api/v1/departments", body);
        ui.toast("部门已创建", "success");
      }
      ui.closeModal(overlay);
      load();
    } catch (e) {
      ui.toast(e.message, "error");
      ui.btnLoading(save, false);
    }
  }

  document.getElementById("btn-new-dept").addEventListener("click", function () {
    var overlay = deptModal(null);
    overlay.querySelector("[data-save]").addEventListener("click", function () {
      submitDept(overlay, null);
    });
    overlay.querySelector("[data-cancel]").addEventListener("click", function () {
      ui.closeModal(overlay);
    });
  });

  tbody.addEventListener("click", async function (e) {
    var btn = e.target.closest("[data-action]");
    if (!btn) return;
    var id = parseInt(btn.dataset.id, 10);

    if (btn.dataset.action === "edit") {
      var list = await api.get("/api/v1/departments", { page_size: 100 });
      var dep = (list.items || []).find(function (x) {
        return x.id === id;
      });
      if (!dep) {
        ui.toast("部门不存在", "error");
        return;
      }
      var overlay = deptModal(dep);
      overlay.querySelector("[data-save]").addEventListener("click", function () {
        submitDept(overlay, dep);
      });
      overlay.querySelector("[data-cancel]").addEventListener("click", function () {
        ui.closeModal(overlay);
      });
      return;
    }

    if (btn.dataset.action === "delete") {
      var depName = btn.dataset.name || "";
      var ok = await ui.confirmModal({
        title: "删除部门",
        danger: true,
        message: "确定删除部门「" + depName + "」吗？部门内成员会变为「未分配部门」；通知和资料中指定的该部门目标会被移除；用户、通知、资料和标签都不会删除。",
        confirmText: "确认删除",
      });
      if (!ok) return;
      ui.btnLoading(btn, true, "删除中…");
      try {
        var res = await api.del("/api/v1/departments/" + id);
        var msg = "部门「" + (res.name || depName) + "」已删除";
        if (res.affected_member_count > 0) msg += "，" + res.affected_member_count + " 个用户已移除该部门";
        if (res.notice_targets_removed > 0) msg += "，" + res.notice_targets_removed + " 条通知的部门目标已摘除";
        if (res.document_targets_removed > 0) msg += "，" + res.document_targets_removed + " 条资料的部门可见目标已摘除";
        ui.toast(msg, "success");
        load();
      } catch (err) {
        ui.toast(err.message, "error");
        ui.btnLoading(btn, false);
      }
    }
  });

  document.getElementById("btn-search").addEventListener("click", function () {
    state.page = 1;
    load();
  });
  document.getElementById("btn-reset").addEventListener("click", function () {
    keywordEl.value = "";
    state.page = 1;
    load();
  });
  keywordEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      state.page = 1;
      load();
    }
  });

  load();
})();