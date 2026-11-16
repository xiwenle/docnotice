/**
 * admin/document_form.js —— 资料新建/编辑页（admin）
 * - 分类树 / 标签多选；支持在表单内直接新建分类（弹窗）与标签（输入回车即建）
 * - 时效冲突处理：R1（起止倒挂）前端预检；R2（覆盖既有区间）前端警告 + confirm_overwrite + 必填原因；
 *   后端 409 VALIDITY_CONFLICT 时回显警告并展开覆盖确认区
 * - 新建页：文件为可选（创建成功后如选择了文件则自动上传）
 * - 编辑页：文件上传/替换 POST /api/v1/documents/{id}/file（multipart）
 * REST：POST /api/v1/documents、PUT /api/v1/documents/{id}、GET /api/v1/documents/{id}
 */
(function () {
  "use strict";

  var editId = window.EDIT_ID;
  var esc = ui.escapeHtml;
  var doc = null; // 编辑模式下的详情快照
  var selectedTags = new Set();
  var catsCache = []; // 分类树缓存（内联新建弹窗需要）
  var MAX_DEPTH = 4; // 与后端 category_service.MAX_DEPTH 一致
  var INDENTS = ["", "　", "　　", "　　　"]; // 一级 ~ 四级缩进

  var titleEl = document.getElementById("f-title");
  var categoryEl = document.getElementById("f-category");
  var tagsEl = document.getElementById("f-tags");
  var summaryEl = document.getElementById("f-summary");
  var fromEl = document.getElementById("f-effective-from");
  var toEl = document.getElementById("f-effective-to");
  var warnBox = document.getElementById("conflict-warning");
  var warnText = document.getElementById("conflict-warning-text");
  var overwriteBox = document.getElementById("overwrite-box");
  var confirmCb = document.getElementById("f-confirm-overwrite");
  var modifyReason = document.getElementById("f-modify-reason");
  var oldRangeEl = document.getElementById("old-range");
  var audienceGroup = document.getElementById("audience-scope-group");
  var docDeptPicker = document.getElementById("doc-dept-picker");
  var docUserPicker = document.getElementById("doc-user-picker");
  var docDeptList = document.getElementById("doc-dept-list");
  var docUserList = document.getElementById("doc-user-list");
  var docDeptFilter = document.getElementById("doc-dept-filter");
  var docUserFilter = document.getElementById("doc-user-filter");
  var selectedDeptIds = new Set();
  var selectedUserIds = new Set();
  var newTagInput = document.getElementById("f-new-tag");
  var newTagBtn = document.getElementById("btn-new-tag");
  var form = document.getElementById("doc-form");
  var submitBtn = document.getElementById("btn-submit");

  /* ---------------- 分类加载（含内联新建入口） ---------------- */
  async function loadCategories() {
    try {
      var data = await api.get("/api/v1/categories");
      var cats = (catsCache = data.items || []);
      var html = '<option value="">未分类</option>';
      var active = function (c) {
        return c.is_active !== false;
      };
      var addOptions = function (nodes, depth) {
        nodes.filter(active).forEach(function (n) {
          html +=
            '<option value="' + n.id + '">' +
            (INDENTS[depth] || "") + (depth ? "└ " : "") + esc(n.name) + "</option>";
          addOptions(n.children || [], depth + 1);
        });
      };
      cats.filter(active).forEach(function (root) {
        if ((root.children || []).some(active)) {
          html += '<optgroup label="' + esc(root.name) + '">';
          addOptions([root], 0);
          html += "</optgroup>";
        } else {
          addOptions([root], 0);
        }
      });
      html += '<option value="__new__">＋ 新建分类…</option>';
      categoryEl.innerHTML = html;
    } catch (e) {
      ui.toast(e.message, "error");
    }
  }

  /* 内联新建分类（弹窗：名称 + 上级分类） */
  function openNewCategoryModal() {
    var candidates = [];
    var collect = function (nodes, depth) {
      (nodes || []).forEach(function (n) {
        if (n.is_active === false) return;
        if (depth + 1 < MAX_DEPTH) {
          candidates.push(
            '<option value="' + n.id + '">' +
              (INDENTS[depth] || "") + esc(n.name) + "</option>"
          );
        }
        collect(n.children, depth + 1);
      });
    };
    collect(catsCache || [], 0);
    var html =
      '<div class="form-group">' +
      '<label class="form-label"><span class="required">分类名称</span></label>' +
      '<input class="form-control" type="text" id="nc-name" maxlength="50" placeholder="如：人事制度">' +
      '<p class="form-error">分类名称不能为空</p></div>' +
      '<div class="form-group mt-3">' +
      '<label class="form-label">上级分类（留空 = 一级分类）</label>' +
      '<select class="form-control" id="nc-parent">' +
      '<option value="">无（作为一级分类）</option>' +
      candidates.join("") +
      "</select></div>" +
      '<div class="modal-footer">' +
      '<button type="button" class="btn btn-secondary" data-close>取消</button>' +
      '<button type="button" class="btn btn-primary" id="nc-submit">创建</button>' +
      "</div>";
    var overlay = ui.openModal(html, { title: "新建分类", size: "sm" });
    overlay.querySelector("[data-close]").addEventListener("click", function () {
      ui.closeModal(overlay);
    });
    overlay.querySelector("#nc-name").addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        overlay.querySelector("#nc-submit").click();
      }
    });
    overlay.querySelector("#nc-submit").addEventListener("click", async function () {
      var nameEl = overlay.querySelector("#nc-name");
      var name = nameEl.value.trim();
      if (!name) {
        nameEl.closest(".form-group").classList.add("has-error");
        nameEl.focus();
        return;
      }
      var btn = overlay.querySelector("#nc-submit");
      ui.btnLoading(btn, true, "创建中…");
      try {
        var parentVal = overlay.querySelector("#nc-parent").value;
        var res = await api.post("/api/v1/categories", {
          name: name,
          parent_id: parentVal ? parseInt(parentVal, 10) : null,
        });
        ui.closeModal(overlay);
        ui.toast("分类「" + res.name + "」已创建", "success");
        await loadCategories();
        categoryEl.value = String(res.id);
      } catch (e) {
        ui.toast(e.message, "error");
        ui.btnLoading(btn, false);
      }
    });
    setTimeout(function () {
      overlay.querySelector("#nc-name").focus();
    }, 50);
  }

  categoryEl.addEventListener("change", function () {
    if (categoryEl.value === "__new__") {
      categoryEl.value = "";
      openNewCategoryModal();
    }
  });

  /* ---------------- 标签加载（多选 + 输入即建） ---------------- */
  function renderTagButtons(tags) {
    tagsEl.innerHTML = tags
      .map(function (t) {
        return (
          '<button type="button" class="tag-option" data-id="' + t.id + '">' +
          esc(t.name) + "</button>"
        );
      })
      .join("");
    tagsEl.querySelectorAll(".tag-option").forEach(function (btn) {
      var id = parseInt(btn.dataset.id, 10);
      if (selectedTags.has(id)) btn.classList.add("checked");
      btn.addEventListener("click", function () {
        if (selectedTags.has(id)) {
          selectedTags.delete(id);
          btn.classList.remove("checked");
        } else {
          selectedTags.add(id);
          btn.classList.add("checked");
        }
      });
    });
  }

  async function loadTags() {
    try {
      var data = await api.get("/api/v1/tags");
      renderTagButtons(data.items || []);
    } catch (e) {
      ui.toast(e.message, "error");
    }
  }

  function bindNewTag() {
    async function doCreate() {
      var name = newTagInput.value.trim();
      if (!name) return;
      try {
        var res = await api.post("/api/v1/tags", { name: name });
        ui.toast("标签「" + res.name + "」已创建", "success");
        newTagInput.value = "";
        var data = await api.get("/api/v1/tags");
        renderTagButtons(data.items || []);
        selectedTags.add(res.id);
        renderTagButtons(data.items || []);
      } catch (e) {
        ui.toast(e.message, "error");
      }
    }
    newTagBtn.addEventListener("click", doCreate);
    newTagInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        doCreate();
      }
    });
  }

  /* ---------------- 编辑模式加载 ---------------- */
  async function loadDoc() {
    if (!editId) return;
    try {
      var d = await api.get("/api/v1/documents/" + editId);
      doc = d;
      titleEl.value = d.title || "";
      if (d.category_id !== undefined && d.category_id !== null) categoryEl.value = d.category_id;
      summaryEl.value = d.summary || "";
      fromEl.value = d.effective_from || "";
      toEl.value = d.effective_to || "";
      // 可见范围回填（2026-11-03）
      var scope = d.audience_scope || "all";
      var radio = audienceGroup && audienceGroup.querySelector(
        'input[name="audience-scope"][value="' + scope + '"]'
      );
      if (radio) radio.checked = true;
      (d.audience_department_ids || []).forEach(function (id) {
        selectedDeptIds.add(id);
      });
      (d.audience_user_ids || []).forEach(function (id) {
        selectedUserIds.add(id);
      });
      syncPickers();
      (d.tag_ids || []).forEach(function (id) {
        selectedTags.add(id);
      });
      // 回填选中态（loadTags 渲染按钮之后执行）
      tagsEl.querySelectorAll(".tag-option").forEach(function (btn) {
        if (selectedTags.has(parseInt(btn.dataset.id, 10))) {
          btn.classList.add("checked");
        }
      });
      oldRangeEl.textContent =
        (d.effective_from || "?") + " ~ " + (d.effective_to || "暂无失效日期");
    } catch (e) {
      ui.toast(e.message, "error");
      history.back();
    }
  }

  /* ---------------- 文件区 ---------------- */
  function loadFileInfo() {
    if (!editId || !doc) return;
    var info = document.getElementById("file-info");
    var link = document.getElementById("file-download-link");
    if (doc.current_file) {
      var f = doc.current_file;
      info.innerHTML =
        '<div class="flex gap-2 flex-wrap" style="align-items:center;">' +
        ui.fileTypeBadge(f.file_type) +
        "<strong>" + esc(f.original_filename) + "</strong>" +
        '<span class="text-muted">' + esc(ui.formatBytes(f.size_bytes)) +
        " · 上传于 " + esc(ui.formatDateTime(f.uploaded_at)) + "</span></div>" +
        '<div class="row-sub mt-1">SHA-256：' + esc(f.sha256) + " · 版本次数：" + esc(doc.rev_count || 0) + "</div>";
      link.href = "/api/v1/documents/" + editId + "/file";
      link.style.display = "";
    } else {
      info.innerHTML = '<span class="text-muted">该资料当前没有文件，可立即上传。</span>';
      link.style.display = "none";
    }
  }

  function setupUpload() {
    var input = document.getElementById("upload-input");
    var drop = document.getElementById("upload-drop");
    var nameEl = document.getElementById("upload-name");
    setupAttachInput();
    if (!input) return;
    // 多选：多张图片由服务端合成一份多页 PDF（2026-11-04）
    input.addEventListener("change", function () {
      var files = Array.prototype.slice.call(input.files || []);
      if (!files.length) return;
      nameEl.textContent = describeFiles(files);
      drop.classList.add("has-file");
    });
    // 编辑页：独立上传/替换按钮
    if (editId) {
      var submit = document.getElementById("upload-submit");
      if (submit) {
        submit.disabled = true;
        input.addEventListener("change", function () {
          submit.disabled = !(input.files && input.files.length);
        });
        submit.addEventListener("click", async function () {
          var files = Array.prototype.slice.call(input.files || []);
          if (!files.length) return;
          var fd = new FormData();
          files.forEach(function (f) { fd.append("file", f); });
          ui.btnLoading(submit, true, files.length > 1 ? "合成中…" : "上传中…");
          try {
            var res = await api.upload("/api/v1/documents/" + editId + "/file", fd);
            ui.toast(
              res.merged
                ? res.merge_note + "，旧文件已进入回收站"
                : "文件上传成功" + (res.recycled_file_id ? "，旧文件已进入回收站" : ""),
              "success"
            );
            doc = await api.get("/api/v1/documents/" + editId);
            loadFileInfo();
            submit.disabled = true;
            input.value = "";
            drop.classList.remove("has-file");
            nameEl.textContent = "点击选择文件（PDF / DOCX / XLSX / XLS / 图片；多张图片会合成一份多页 PDF）";
          } catch (e) {
            ui.toast(e.message, "error");
            ui.btnLoading(submit, false);
          }
        });
      }
    }
  }

  /** 附件选择框（新建页）：单文件，仅显示文件名与大小 */
  function setupAttachInput() {
    var input = document.getElementById("attach-input");
    var drop = document.getElementById("attach-drop");
    var nameEl = document.getElementById("attach-name");
    if (!input || !drop || !nameEl) return;
    input.addEventListener("change", function () {
      var f = (input.files || [])[0];
      if (!f) {
        drop.classList.remove("has-file");
        nameEl.textContent = "添加附件（可选：Word / PPT / Excel / PDF / 图片，仅供下载）";
        return;
      }
      nameEl.textContent = f.name + "（" + ui.formatBytes(f.size) + "）";
      drop.classList.add("has-file");
    });
  }

  /** 已选文件摘要：单文件显示名称+大小，多选显示「N 张图片，将合成 PDF」 */
  function describeFiles(files) {
    if (files.length === 1) {
      return files[0].name + "（" + ui.formatBytes(files[0].size) + "）";
    }
    var total = files.reduce(function (sum, f) { return sum + f.size; }, 0);
    var allImages = files.every(function (f) {
      return /\.(jpe?g|png|gif|webp)$/i.test(f.name);
    });
    return files.length + " 个文件（共 " + ui.formatBytes(total) + "）" +
      (allImages
        ? " · 将按选择顺序合成一份多页 PDF"
        : " · 一次只能上传多个图片，请逐个上传其它类型");
  }

  /* ---------------- 时效冲突前端预检 ---------------- */
  function datesOverlap(aFrom, aTo, bFrom, bTo) {
    // a 为库里原区间（可为空=首次设置不算覆盖），b 为新区间
    if (!aFrom) return false;
    var daFrom = new Date(aFrom);
    var dbFrom = new Date(bFrom);
    var daTo = aTo ? new Date(aTo) : null;
    var dbTo = bTo ? new Date(bTo) : null;
    var aEnd = daTo || new Date("9999-12-31");
    var bEnd = dbTo || new Date("9999-12-31");
    return daFrom <= bEnd && dbFrom <= aEnd;
  }

  function validateDates() {
    // R1：起止倒挂
    if (fromEl.value && toEl.value && fromEl.value > toEl.value) {
      warnBox.classList.remove("hidden");
      warnText.innerHTML =
        "<strong>日期冲突：</strong>失效日期不能早于生效日期。请调整日期后再保存。";
      overwriteBox.style.display = "none";
      return false;
    }
    warnBox.classList.add("hidden");
    return true;
  }

  function setupOverwriteWarning() {
    function check() {
      if (validateDates() && doc && editId) {
        var current = { from: doc.effective_from, to: doc.effective_to };
        var changed =
          fromEl.value !== (current.from || "") || toEl.value !== (current.to || "");
        if (changed && datesOverlap(current.from, current.to, fromEl.value, toEl.value)) {
          warnBox.classList.remove("hidden");
          warnText.innerHTML =
            "<strong>时效覆盖警告：</strong>这个资料已有生效区间 <code>" +
            esc(current.from || "?") + " ~ " + esc(current.to || "暂无失效日期") +
            "</code>，新日期与它重叠。系统默认不保存；如确需覆盖，请勾选下方确认项并填写修改原因。修改会写入审计日志。";
          overwriteBox.style.display = "block";
        } else {
          warnBox.classList.add("hidden");
          overwriteBox.style.display = "none";
        }
      }
    }
    [fromEl, toEl].forEach(function (el) {
      el.addEventListener("change", check);
    });
  }


  /* ---------------- 可见范围（2026-11-03） ---------------- */
  function currentScope() {
    var el = audienceGroup && audienceGroup.querySelector("input:checked");
    return el ? el.value : "all";
  }

  function syncPickers() {
    var scope = currentScope();
    if (docDeptPicker) docDeptPicker.classList.toggle("hidden", scope !== "department");
    if (docUserPicker) docUserPicker.classList.toggle("hidden", scope !== "user");
  }

  function renderCheckList(el, items, selected, idKey, labelFn, selectedMap) {
    if (!el) return;
    var map = selectedMap || {};
    var merged = [];
    var seen = {};
    (items || []).forEach(function (it) {
      var id = it[idKey];
      if (!seen[id]) { seen[id] = true; merged.push(it); }
    });
    Object.keys(map).forEach(function (key) {
      var id = parseInt(key, 10);
      if (selected.has(id) && !seen[id]) {
        seen[id] = true;
        merged.unshift(map[key]);
      }
    });
    var count = merged.filter(function (it) { return selected.has(it[idKey]); }).length;
    if (!merged.length) {
      el.innerHTML = '<div class="table-empty" style="padding:12px;">暂无可选项</div>';
      return;
    }
    el.innerHTML = '<div class="form-help" data-selected-count aria-live="polite">已选 ' + count + ' 项</div>' + merged.map(function (it) {
      var id = it[idKey];
      if (selectedMap && !selectedMap[id]) selectedMap[id] = it;
      return '<label class="check-item">' +
        '<input type="checkbox" value="' + id + '"' +
        (selected.has(id) ? " checked" : "") + ">" +
        "<span>" + esc(labelFn(it)) + "</span></label>";
    }).join("");
    el.querySelectorAll("input[type=checkbox]").forEach(function (cb) {
      cb.addEventListener("change", function () {
        var id = parseInt(cb.value, 10);
        if (cb.checked) selected.add(id); else selected.delete(id);
        renderCheckList(el, items, selected, idKey, labelFn, selectedMap);
      });
    });
  }

  var deptsCache = [];
  var usersCache = [];
  var deptSelectedItems = {};
  var userSelectedItems = {};

  function mergeSelectedItems(items, selected, selectedMap) {
    var out = (items || []).slice();
    var seen = {};
    out.forEach(function (it) { seen[it.id] = true; });
    Object.keys(selectedMap || {}).forEach(function (key) {
      var id = parseInt(key, 10);
      if (selected.has(id) && !seen[id]) out.unshift(selectedMap[key]);
    });
    return out;
  }

  async function searchDepartments(keyword) {
    var d = await api.get("/api/v1/departments", {
      page_size: 50, keyword: keyword || undefined, is_active: true
    });
    deptsCache = (d.items || []).filter(function (x) { return x.is_active; });
    deptsCache.forEach(function (x) { deptSelectedItems[x.id] = x; });
    return mergeSelectedItems(deptsCache, selectedDeptIds, deptSelectedItems);
  }

  async function searchUsers(keyword) {
    var u = await api.get("/api/v1/users", {
      page_size: 50, keyword: keyword || undefined, is_active: true
    });
    usersCache = (u.items || []).filter(function (x) {
      return x.is_active && x.role !== "admin";
    });
    usersCache.forEach(function (x) { userSelectedItems[x.id] = x; });
    return mergeSelectedItems(usersCache, selectedUserIds, userSelectedItems);
  }

  function debounce(fn, wait) {
    var timer = null;
    return function () {
      var args = arguments;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(null, args); }, wait);
    };
  }

  async function loadAudienceOptions() {
    try {
      var deps = await searchDepartments("");
      renderCheckList(docDeptList, deps, selectedDeptIds, "id",
        function (x) { return x.name; }, deptSelectedItems);
    } catch (e) { /* 部门候选失败不影响其他字段 */ }
    try {
      var users = await searchUsers("");
      renderCheckList(docUserList, users, selectedUserIds, "id",
        function (x) { return (x.display_name || x.username) + "（" + x.username + "）"; },
        userSelectedItems);
    } catch (e) { /* 用户候选失败不影响其他字段 */ }

    if (docDeptFilter) {
      docDeptFilter.addEventListener("input", debounce(async function () {
        try {
          var items = await searchDepartments(docDeptFilter.value.trim());
          renderCheckList(docDeptList, items, selectedDeptIds, "id",
            function (x) { return x.name; }, deptSelectedItems);
        } catch (e) { /* 保留当前列表 */ }
      }, 250));
    }
    if (docUserFilter) {
      docUserFilter.addEventListener("input", debounce(async function () {
        try {
          var items = await searchUsers(docUserFilter.value.trim());
          renderCheckList(docUserList, items, selectedUserIds, "id",
            function (x) { return (x.display_name || x.username) + "（" + x.username + "）"; },
            userSelectedItems);
        } catch (e) { /* 保留当前列表 */ }
      }, 250));
    }
  }

  async function hydrateSelectedAudience() {
    var depIds = Array.from(selectedDeptIds);
    var userIds = Array.from(selectedUserIds);
    try {
      if (depIds.length) {
        var d = await api.get("/api/v1/departments", { ids: depIds, page_size: 100 });
        (d.items || []).forEach(function (x) { deptSelectedItems[x.id] = x; });
      }
    } catch (e) { /* 保留已有名称或空 */ }
    try {
      if (userIds.length) {
        var u = await api.get("/api/v1/users", { ids: userIds, page_size: 100 });
        (u.items || []).forEach(function (x) { userSelectedItems[x.id] = x; });
      }
    } catch (e) { /* 同上 */ }
  }
  if (audienceGroup) {
    audienceGroup.addEventListener("change", syncPickers);
    syncPickers();
  }

  /* ---------------- 提交 ---------------- */
  function collectPayload() {
    return {
      title: titleEl.value.trim(),
      category_id: categoryEl.value ? parseInt(categoryEl.value, 10) : null,
      summary: summaryEl.value.trim() || null,
      effective_from: fromEl.value || null,
      effective_to: toEl.value || null,
      tag_ids: Array.from(selectedTags),
      audience_scope: currentScope(),
      audience_department_ids: Array.from(selectedDeptIds),
      audience_user_ids: Array.from(selectedUserIds),
      confirm_overwrite: confirmCb.checked,
      modify_reason: modifyReason.value.trim() || null,
    };
  }

  form.addEventListener("submit", async function (e) {
    e.preventDefault();

    // 基础校验
    var ok = true;
    if (!titleEl.value.trim()) {
      titleEl.closest(".form-group").classList.add("has-error");
      ok = false;
    } else {
      titleEl.closest(".form-group").classList.remove("has-error");
    }
    if (!fromEl.value) {
      fromEl.closest(".form-group").classList.add("has-error");
      ok = false;
    } else {
      fromEl.closest(".form-group").classList.remove("has-error");
    }
    if (!ok) {
      ui.toast("请完善必填项", "warning");
      return;
    }
    var scope = currentScope();
    if (scope === "department" && !selectedDeptIds.size) {
      ui.toast("可见范围为「指定部门」时，请至少选择一个部门", "warning");
      return;
    }
    if (scope === "user" && !selectedUserIds.size) {
      ui.toast("可见范围为「指定人员」时，请至少选择一个人", "warning");
      return;
    }
    if (!validateDates()) {
      ui.toast("日期冲突未解决，无法保存", "error");
      return;
    }
    // 覆盖确认前置校验
    if (overwriteBox.style.display !== "none" && !confirmCb.checked) {
      ui.toast("时效区间冲突：请勾选「确认覆盖」并填写修改原因，或调整日期范围", "warning");
      return;
    }
    if (confirmCb.checked && !modifyReason.value.trim()) {
      requireReason();
      ui.toast("修改原因必填（将写入审计日志）", "warning");
      return;
    }

    var payload = collectPayload();
    ui.btnLoading(submitBtn, true, "保存中…");
    try {
      if (editId) {
        await api.put("/api/v1/documents/" + editId, payload);
        ui.toast("资料已更新", "success");
        setTimeout(function () {
          location.href = "/documents/" + editId;
        }, 600);
      } else {
        var created = await api.post("/api/v1/documents", payload);
        // 记住本次分类选择：连续上传同批资料时少一次操作（仅前端偏好）
        try {
          localStorage.setItem("doc_last_category", payload.category_id || "");
        } catch (e) { /* 隐私模式等场景静默 */ }
        // 新建页可选文件：创建成功后自动上传
        var fileInput = document.getElementById("upload-input");
        var files = Array.prototype.slice.call((fileInput && fileInput.files) || []);
        if (files.length) {
          try {
            var fd = new FormData();
            files.forEach(function (f) { fd.append("file", f); });
            var upRes = await api.upload("/api/v1/documents/" + created.id + "/file", fd);
            ui.toast(
              upRes && upRes.merged ? "资料已创建，" + upRes.merge_note : "资料已创建，文件上传成功",
              "success"
            );
          } catch (uploadErr) {
            // 跳转会刷新页面把 toast 吞掉：带 upload_failed 参数让详情页显示持久警告
            ui.toast("资料已创建，但文件上传失败：" + uploadErr.message, "error");
            setTimeout(function () {
              location.href = "/documents/" + created.id + "?upload_failed=1";
            }, 900);
            return;
          }
        } else {
          ui.toast("资料已创建（未上传文件，可在详情页补传）", "success");
        }

        // 附件（2026-11-06）：与主文件互相独立，主文件失败时不阻塞附件上传
        var attachInput = document.getElementById("attach-input");
        var attachFile = ((attachInput && attachInput.files) || [])[0];
        if (attachFile) {
          try {
            var afd = new FormData();
            afd.append("file", attachFile);
            await api.upload(
              "/api/v1/documents/" + created.id + "/attachment", afd
            );
            ui.toast("资料已创建，文件与附件均上传成功", "success");
          } catch (attErr) {
            ui.toast("资料已创建，但附件上传失败：" + attErr.message, "error");
          }
        }

        setTimeout(function () {
          location.href = "/documents/" + created.id;
        }, 800);
      }
    } catch (err) {
      ui.btnLoading(submitBtn, false);
      if (err.code === "VALIDITY_CONFLICT") {
        // 后端 R1/R2 冲突：回显警告并展开覆盖确认区
        warnBox.classList.remove("hidden");
        warnText.innerHTML = "<strong>系统阻止保存：</strong>" + esc(err.message);
        if (editId && doc) {
          overwriteBox.style.display = "block";
          confirmOutlined();
        }
      } else {
        ui.toast(err.message, "error");
      }
    }
  });

  function requireReason() {
    modifyReason.closest("div").style.outline = "2px solid var(--danger)";
    setTimeout(function () {
      modifyReason.closest("div").style.outline = "";
    }, 1500);
  }

  function confirmOutlined() {
    confirmCb.closest("label").style.outline = "2px solid var(--warning)";
    setTimeout(function () {
      confirmCb.closest("label").style.outline = "";
    }, 1500);
  }

  // 修改原因必填校验
  confirmCb.addEventListener("change", function () {
    if (confirmCb.checked && !modifyReason.value.trim()) {
      modifyReason.placeholder = "修改原因必填（写入审计日志）";
    }
  });

  /* ---------------- 初始化 ---------------- */
  (async function init() {
    // 可见范围的部门/人员选项必须与分类/标签一起加载完，
    // 否则编辑模式的回填会因为复选框还没渲染而丢掉选中态。
    await Promise.all([loadCategories(), loadTags(), loadAudienceOptions()]);
    if (editId) {
      await loadDoc();
      await hydrateSelectedAudience();
      // 已选对象可能不在搜索首页，使用 ids 回读后重绘。
      renderCheckList(docDeptList, deptsCache, selectedDeptIds, "id",
        function (x) { return x.name; }, deptSelectedItems);
      renderCheckList(docUserList, usersCache, selectedUserIds, "id",
        function (x) { return (x.display_name || x.username) + "（" + x.username + "）"; }, userSelectedItems);
      if (categoryEl.value && ![...categoryEl.options].some(function (o) {
        return o.value === String(categoryEl.value);
      })) {
        categoryEl.value = "";
      }
      loadFileInfo();
      setupOverwriteWarning();
    } else {
      // 新建模式：恢复上次使用的分类（若仍存在）
      try {
        var last = localStorage.getItem("doc_last_category");
        if (last && [...categoryEl.options].some(function (o) { return o.value === last; })) {
          categoryEl.value = last;
        }
      } catch (e) { /* 静默 */ }
    }
    setupUpload();
    bindNewTag();
    // 新建页也启用日期预检（R1）
    if (!editId) {
      [fromEl, toEl].forEach(function (el) {
        el.addEventListener("change", validateDates);
      });
    }
    document.title =
      (editId ? "编辑资料" : "新建资料") + " - " + document.title.split(" - ").pop();
  })();
})();