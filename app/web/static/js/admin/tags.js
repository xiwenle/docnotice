/**
 * admin/tags.js —— 标签管理
 * REST：GET/POST/PUT/DELETE /api/v1/tags（with_count；重名 409；删除标签只解除资料关联）
 */
(function () {
  "use strict";

  var esc = ui.escapeHtml;
  var keywordEl = document.getElementById("f-keyword");
  var tbody = document.getElementById("tag-tbody");

  async function load() {
    tbody.innerHTML = '<tr><td colspan="3"><div class="table-empty">加载中…</div></td></tr>';
    try {
      var data = await api.get("/api/v1/tags", {
        keyword: keywordEl.value.trim() || undefined,
        with_count: 1,
      });
      var items = data.items || [];
      if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="3"><div class="table-empty">暂无标签</div></td></tr>';
        return;
      }
      tbody.innerHTML = items
        .map(function (t) {
          return (
            "<tr><td><span class=\"tag-chip\"># " + esc(t.name) + "</span></td>" +
            "<td>" + esc(t.document_count !== undefined ? t.document_count : "—") + "</td>" +
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

  document.getElementById("btn-new-tag").addEventListener("click", function () {
    var overlay = ui.openModal(
      '<form id="tag-form">' +
      '<div class="form-group"><label class="form-label"><span class="required">标签名称</span></label>' +
      '<input class="form-control" id="t-name" maxlength="64" placeholder="如：薪酬"></div>' +
      '<div class="modal-footer" style="margin:16px -20px -20px;">' +
      '<button type="button" class="btn btn-secondary" data-cancel>取消</button>' +
      '<button type="button" class="btn btn-primary" data-save>创建</button></div>' +
      "</form>",
      { title: "新建标签", size: "sm" }
    );
    overlay.querySelector("[data-cancel]").addEventListener("click", function () {
      ui.closeModal(overlay);
    });
    overlay.querySelector("[data-save]").addEventListener("click", async function () {
      var nameEl = overlay.querySelector("#t-name");
      var name = nameEl.value.trim();
      if (!name) {
        nameEl.closest(".form-group").classList.add("has-error");
        return;
      }
      var save = overlay.querySelector("[data-save]");
      ui.btnLoading(save, true, "创建中…");
      try {
        await api.post("/api/v1/tags", { name: name });
        ui.toast("标签已创建", "success");
        ui.closeModal(overlay);
        load();
      } catch (e) {
        ui.toast(e.message, "error");
        ui.btnLoading(save, false);
      }
    });
  });

  tbody.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-action]");
    if (!btn) return;
    var id = parseInt(btn.dataset.id, 10);
    var name = btn.dataset.name || "";

    if (btn.dataset.action === "edit") {
      var overlay = ui.openModal(
        '<form id="tag-form">' +
        '<div class="form-group"><label class="form-label"><span class="required">标签名称</span></label>' +
        '<input class="form-control" id="t-name" maxlength="64" value="' + esc(name) + '"></div>' +
        '<div class="modal-footer" style="margin:16px -20px -20px;">' +
        '<button type="button" class="btn btn-secondary" data-cancel>取消</button>' +
        '<button type="button" class="btn btn-primary" data-save>保存</button></div>' +
        "</form>",
        { title: "编辑标签", size: "sm" }
      );
      overlay.querySelector("[data-cancel]").addEventListener("click", function () {
        ui.closeModal(overlay);
      });
      overlay.querySelector("[data-save]").addEventListener("click", async function () {
        var nameEl = overlay.querySelector("#t-name");
        var val = nameEl.value.trim();
        if (!val) {
          nameEl.closest(".form-group").classList.add("has-error");
          return;
        }
        var save = overlay.querySelector("[data-save]");
        ui.btnLoading(save, true, "保存中…");
        try {
          await api.put("/api/v1/tags/" + id, { name: val });
          ui.toast("标签已更新", "success");
          ui.closeModal(overlay);
          load();
        } catch (err) {
          ui.toast(err.message, "error");
          ui.btnLoading(save, false);
        }
      });
      return;
    }

    if (btn.dataset.action === "delete") {
      ui.confirmModal({
        title: "删除标签",
        danger: true,
        message: "确定删除标签「" + name + "」吗？删除后资料会移除该标签，进入「未打标签」状态；资料、文件、附件和分类都不会删除。",
        confirmText: "确认删除",
      }).then(async function (ok) {
        if (!ok) return;
        ui.btnLoading(btn, true, "删除中…");
        try {
          var res = await api.del("/api/v1/tags/" + id);
          var msg = "标签「" + (res.name || name) + "」已删除";
          if (res.affected_document_count > 0) msg += "，" + res.affected_document_count + " 份资料已移除该标签";
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