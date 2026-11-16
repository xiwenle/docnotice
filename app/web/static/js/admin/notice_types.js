/**
 * admin/notice_types.js —— 通知类型管理
 * REST：GET/POST/PUT/DELETE /api/v1/notice-types（重名 409；普通类型删除后通知变未分类）
 * 注：类型只有「删除」（物理删+引用拦截）；停用能力已按产品决策移除
 */
(function () {
  "use strict";

  var esc = ui.escapeHtml;
  var keywordEl = document.getElementById("f-keyword");
  var tbody = document.getElementById("type-tbody");

  async function load() {
    tbody.innerHTML = '<tr><td colspan="3"><div class="table-empty">加载中…</div></td></tr>';
    try {
      var data = await api.get("/api/v1/notice-types", {
        keyword: keywordEl.value.trim() || undefined,
        with_count: 1,
      });
      var items = data.items || [];
      if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="3"><div class="table-empty">暂无通知类型</div></td></tr>';
        return;
      }
      tbody.innerHTML = items
        .map(function (t) {
          return (
            "<tr><td><strong>" + esc(t.name) + "</strong></td>" +
            "<td>" + esc(t.notice_count !== undefined ? t.notice_count : "—") + "</td>" +
            '<td class="row-actions">' +
            '<button class="btn btn-sm btn-ghost" data-action="edit" data-id="' + t.id + '" data-name="' + esc(t.name) + '">编辑</button>' +
            '<button class="btn btn-sm btn-danger-outline" data-action="delete" data-id="' + t.id + '" data-name="' + esc(t.name) + '">删除</button>' +
            "</td></tr>"
          );
        })
        .join("");
    } catch (e) {
      tbody.innerHTML = '<tr><td colspan="3"><div class="table-empty text-danger">' + esc(e.message) + "</div></td></tr>";
    }
  }

  function openTypeModal(title, initial) {
    initial = initial || {};
    var overlay = ui.openModal(
      '<form id="type-form">' +
      '<div class="form-group"><label class="form-label"><span class="required">类型名称</span></label>' +
      '<input class="form-control" id="nt-name" maxlength="32" value="' + esc(initial.name || "") + '" placeholder="如：培训通知"></div>' +
      '<div class="modal-footer" style="margin:16px -20px -20px;">' +
      '<button type="button" class="btn btn-secondary" data-cancel>取消</button>' +
      '<button type="button" class="btn btn-primary" data-save>' + (initial.id ? "保存" : "创建") + "</button></div>" +
      "</form>",
      { title: title, size: "sm" }
    );
    overlay.querySelector("[data-cancel]").addEventListener("click", function () {
      ui.closeModal(overlay);
    });
    overlay.querySelector("[data-save]").addEventListener("click", async function () {
      var nameEl = overlay.querySelector("#nt-name");
      var name = nameEl.value.trim();
      if (!name) {
        nameEl.closest(".form-group").classList.add("has-error");
        return;
      }
      var save = overlay.querySelector("[data-save]");
      ui.btnLoading(save, true, "保存中…");
      try {
        if (initial.id) {
          await api.put("/api/v1/notice-types/" + initial.id, { name: name });
          ui.toast("通知类型已更新", "success");
        } else {
          await api.post("/api/v1/notice-types", { name: name, sort_order: 0 });
          ui.toast("通知类型已创建", "success");
        }
        ui.closeModal(overlay);
        load();
      } catch (e) {
        ui.toast(e.message, "error");
        ui.btnLoading(save, false);
      }
    });
    setTimeout(function () {
      overlay.querySelector("#nt-name").focus();
    }, 50);
  }

  document.getElementById("btn-new-type").addEventListener("click", function () {
    openTypeModal("新建通知类型");
  });

  tbody.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-action]");
    if (!btn) return;
    var id = parseInt(btn.dataset.id, 10);
    var name = btn.dataset.name || "";

    if (btn.dataset.action === "edit") {
      openTypeModal("编辑通知类型", { id: id, name: name });
      return;
    }

    if (btn.dataset.action === "delete") {
      ui.confirmModal({
        title: "删除通知类型",
        danger: true,
        message: "确定删除通知类型「" + name + "」吗？删除后已有通知的类型会变为「未分类」；通知、资料和标签都不会删除。「废止」「替换」是联动类型，不能删除。",
        confirmText: "确认删除",
      }).then(async function (ok) {
        if (!ok) return;
        ui.btnLoading(btn, true, "删除中…");
        try {
          var res = await api.del("/api/v1/notice-types/" + id);
          var msg = "通知类型「" + (res.name || name) + "」已删除";
          if (res.affected_notice_count > 0) msg += "，" + res.affected_notice_count + " 条通知已转为未分类";
          ui.toast(msg, "success");
          load();
        } catch (err) {
          ui.toast(err.message, "error");
          ui.btnLoading(btn, false);
        }
      });
    }
  });

  document.getElementById("btn-search").addEventListener("click", function () {
    load();
  });
  document.getElementById("btn-reset").addEventListener("click", function () {
    keywordEl.value = "";
    load();
  });
  keywordEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter") load();
  });

  load();
})();