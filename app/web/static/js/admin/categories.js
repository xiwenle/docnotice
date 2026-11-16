/**
 * admin/categories.js —— 分类树管理（一级 ~ 四级）
 * REST：GET/POST/PUT/DELETE /api/v1/categories（深度≤4、同级重名；删除=整棵子树 + 资料转未分类）
 */
(function () {
  "use strict";

  var esc = ui.escapeHtml;
  var tree = document.getElementById("cat-tree");
  var MAX_DEPTH = 4;  // 与后端 category_service.MAX_DEPTH 一致
  var LEVEL_NAMES = ["一级", "二级", "三级", "四级"];

  function findNode(nodes, id) {
    for (var i = 0; i < nodes.length; i++) {
      if (nodes[i].id === id) return nodes[i];
      var hit = findNode(nodes[i].children || [], id);
      if (hit) return hit;
    }
    return null;
  }

  function countDescendants(node) {
    var n = 0;
    (node.children || []).forEach(function (ch) {
      n += 1 + countDescendants(ch);
    });
    return n;
  }

  function render(items) {
    if (!items.length) {
      tree.innerHTML =
        '<div class="empty-state"><p>暂无分类，点击右上角「新建一级分类」开始</p></div>';
      return;
    }
    var nodeActions = function (n, depth) {
      return (
        (depth + 1 < MAX_DEPTH
          ? '<button class="btn btn-sm btn-ghost" data-action="add-child" data-id="' + n.id +
            '" data-name="' + esc(n.name) + '" data-depth="' + depth + '">＋ 子分类</button>'
          : "") +
        '<button class="btn btn-sm btn-ghost" data-action="edit" data-id="' + n.id + '" data-name="' + esc(n.name) + '" data-parent="' + (depth === 0 ? "" : n.parent_id || "") + '">编辑</button>' +
        '<button class="btn btn-sm btn-danger-outline" data-action="delete" data-id="' + n.id + '" data-name="' + esc(n.name) + '">删除</button>'
      );
    };
    var countHtml = function (n) {
      var parts = [];
      if (n.document_count !== undefined && n.document_count > 0) {
        parts.push("<strong>" + n.document_count + "</strong> 篇资料");
      }
      if (n.children && n.children.length) {
        parts.push(n.children.length + " 个子分类");
      }
      return parts.length
        ? '<span class="text-muted" style="margin-left:8px;font-size:12px;">' + parts.join(" · ") + "</span>"
        : "";
    };
    // 资源管理器式折叠树：文件夹（分类）可展开/收起，资料只以计数呈现（叶子）
    var renderNode = function (n, depth) {
      var hasChildren = !!(n.children && n.children.length);
      var html =
        '<div class="tree-node">' +
        '<div class="tree-node-content" data-id="' + n.id + '" style="padding:10px 16px 10px ' + (12 + depth * 22) + 'px;">' +
        '<span class="tree-toggle' + (hasChildren ? "" : " leaf") + '">▸</span>' +
        "<strong>" + esc(n.name) + "</strong>" +
        countHtml(n) +
        '<span style="flex:1;"></span>' +
        '<span class="row-actions">' + nodeActions(n, depth) + "</span></div>";
      if (hasChildren) {
        html += '<div class="tree-children collapsed">' +
          n.children.map(function (ch) { return renderNode(ch, depth + 1); }).join("") +
          "</div>";
      }
      html += "</div>";
      return html;
    };
    tree.innerHTML = items.map(function (root) { return renderNode(root, 0); }).join("");
    ui.treeState("zc_tree_admin_categories", tree).restore();  // 记忆用户展开状态
  }

  async function load() {
    tree.innerHTML =
      '<div class="empty-state"><div class="spinner spinner-sm" style="margin:0 auto 8px;"></div><p>加载中…</p></div>';
    try {
      var data = await api.get("/api/v1/categories", { with_document_count: 1 });
      render(data.items || []);
    } catch (e) {
      tree.innerHTML = '<div class="empty-state text-danger">' + esc(e.message) + "</div>";
    }
  }

  function catModal(opts) {
    opts = opts || {};
    var isEdit = !!opts.cat;
    var overlay = ui.openModal(
      '<form id="cat-form">' +
      '<div class="form-group"><label class="form-label"><span class="required">分类名称</span></label>' +
      '<input class="form-control" id="c-name" maxlength="128" value="' + esc(opts.cat ? opts.cat.name : "") + '"></div>' +
      '<div class="form-group mt-3"><label class="form-label">排序号（小的在前）</label>' +
      '<input class="form-control" type="number" id="c-sort" value="' + esc(opts.cat ? opts.cat.sort_order : 0) + '"></div>' +
      '<div class="modal-footer" style="margin:16px -20px -20px;">' +
      '<button type="button" class="btn btn-secondary" data-cancel>取消</button>' +
      '<button type="button" class="btn btn-primary" data-save>' + (isEdit ? "保存" : "创建") + "</button></div>" +
      "</form>",
      { title: opts.title || "分类", size: "sm" }
    );
    return overlay;
  }

  function bindCatModal(overlay, opts) {
    overlay.querySelector("[data-cancel]").addEventListener("click", function () {
      ui.closeModal(overlay);
    });
    overlay.querySelector("[data-save]").addEventListener("click", async function () {
      var nameEl = overlay.querySelector("#c-name");
      var name = nameEl.value.trim();
      if (!name) {
        nameEl.closest(".form-group").classList.add("has-error");
        return;
      }
      var sort = parseInt(overlay.querySelector("#c-sort").value, 10) || 0;
      var save = overlay.querySelector("[data-save]");
      ui.btnLoading(save, true, "保存中…");
      try {
        if (opts.cat) {
          await api.put("/api/v1/categories/" + opts.cat.id, {
            name: name,
            sort_order: sort,
          });
          ui.toast("分类已更新", "success");
        } else {
          await api.post("/api/v1/categories", {
            name: name,
            parent_id: opts.parentId || null,
            sort_order: sort,
          });
          ui.toast("分类已创建", "success");
        }
        ui.closeModal(overlay);
        load();
      } catch (e) {
        ui.toast(e.message, "error");
        ui.btnLoading(save, false);
      }
    });
  }

  document.getElementById("btn-new-root").addEventListener("click", function () {
    bindCatModal(catModal({ title: "新建一级分类" }), {});
  });

  tree.addEventListener("click", async function (e) {
    var btn = e.target.closest("[data-action]");
    if (!btn) return;
    var id = parseInt(btn.dataset.id, 10);
    var name = btn.dataset.name || "";

    if (btn.dataset.action === "add-child") {
      var childLevel = LEVEL_NAMES[parseInt(btn.dataset.depth, 10) + 1];
      bindCatModal(catModal({ title: "在「" + name + "」下新建" + childLevel + "分类" }), {
        parentId: id,
      });
      return;
    }

    if (btn.dataset.action === "edit") {
      var data = await api.get("/api/v1/categories", { with_document_count: 1 });
      var cat = findNode(data.items || [], id);
      if (!cat) {
        ui.toast("分类不存在", "error");
        return;
      }
      bindCatModal(
        catModal({ title: "编辑分类", cat: Object.assign({}, cat, { name: btn.dataset.name }) }),
        { cat: cat }
      );
      return;
    }

    if (btn.dataset.action === "delete") {
      // 2026-11-09 用户确认：删除整棵子树；先弹窗，资料含已归档转未分类。
      var list = await api.get("/api/v1/categories", { with_document_count: 1 });
      var cat = findNode(list.items || [], id);
      if (!cat) {
        ui.toast("分类不存在", "error");
        return;
      }
      var descendants = countDescendants(cat);
      var docCount = cat.document_count || 0;
      var message = "确定删除分类「" + (cat.name || name) + "」" +
        (descendants > 0 ? "及其 " + descendants + " 个子分类" : "") +
        "吗？" +
        (docCount > 0 ? "当前树内统计 " + docCount + " 份资料，" : "") +
        "资料（含已归档）将变为「未分类」；资料、文件、附件、标签、通知和通知类型都不会删除。";
      var ok = await ui.confirmModal({
        title: "删除分类",
        danger: true,
        message: message,
        confirmText: "确认删除",
      });
      if (!ok) return;
      ui.btnLoading(btn, true, "删除中…");
      try {
        var res = await api.del("/api/v1/categories/" + id);
        var catCount = res.deleted_category_count || 1;
        var docCountDeleted = res.unclassified_document_count || 0;
        var msg = "分类「" + (res.name || name) + "」及 " + catCount + " 个分类已删除";
        if (docCountDeleted > 0) msg += "，" + docCountDeleted + " 份资料转为未分类";
        ui.toast(msg, "success");
        load();
      } catch (err) {
        ui.toast(err.message, "error");
        ui.btnLoading(btn, false);
      }
    }
  });

  // 资源管理器式折叠：点击节点行（非按钮区域）展开/收起子文件夹
  tree.addEventListener("click", function (e) {
    var btn = e.target.closest("button");
    if (btn) return;  // 按钮交给上方事件委托
    var content = e.target.closest(".tree-node-content");
    if (!content) return;
    var kids = content.nextElementSibling;
    if (kids && kids.classList.contains("tree-children")) {
      var toggle = content.querySelector(".tree-toggle");
      var expanded = toggle.classList.toggle("expanded");
      kids.classList.toggle("collapsed", !expanded);
    }
  });

  // 单键双态：展开全部/折叠全部
  var toggleTreeBtn = document.getElementById("btn-toggle-tree");
  if (toggleTreeBtn) {
    toggleTreeBtn.addEventListener("click", function () {
      var expanding = toggleTreeBtn.textContent.indexOf("展开") >= 0;
      if (expanding) {
        tree.querySelectorAll(".tree-children").forEach(function (k) { k.classList.remove("collapsed"); });
        tree.querySelectorAll(".tree-toggle:not(.leaf)").forEach(function (t) { t.classList.add("expanded"); });
        toggleTreeBtn.textContent = "折叠全部 ▴";
      } else {
        tree.querySelectorAll(".tree-children").forEach(function (k) { k.classList.add("collapsed"); });
        tree.querySelectorAll(".tree-toggle").forEach(function (t) { t.classList.remove("expanded"); });
        toggleTreeBtn.textContent = "展开全部 ▾";
      }
    });
  }

  load();
})();