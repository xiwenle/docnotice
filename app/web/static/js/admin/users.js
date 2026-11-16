/**
 * admin/users.js —— 用户管理
 * - 列表/筛选/分页：GET /api/v1/users
 * - 新建/编辑/停用/重置密码：POST/PUT/DELETE /api/v1/users（角色变更仅 admin，后端强制）
 */
(function () {
  "use strict";

  var esc = ui.escapeHtml;
  var state = { page: 1, page_size: 15 };
  var me = ui.currentUser();

  var keywordEl = document.getElementById("f-keyword");
  var roleEl = document.getElementById("f-role");
  var activeEl = document.getElementById("f-active");
  var deptEl = document.getElementById("f-department");
  var tbody = document.getElementById("user-tbody");
  var pager = document.getElementById("user-pagination");
  var departments = [];

  function buildQuery() {
    return {
      page: state.page,
      page_size: state.page_size,
      keyword: keywordEl.value.trim(),
      role: roleEl.value,
      is_active: activeEl.value === "" ? undefined : activeEl.value === "1",
      department_id: deptEl.value || undefined,
    };
  }

  async function loadDepartments() {
    try {
      var d = await api.get("/api/v1/departments", { page_size: 100 });
      departments = d.items || [];
      var html = '<option value="">全部部门</option>';
      departments
        .filter(function (dep) {
          return dep.is_active !== false;
        })
        .forEach(function (dep) {
          html += '<option value="' + dep.id + '">' + esc(dep.name) + (dep.member_count !== undefined ? "（" + dep.member_count + "人）" : "") + "</option>";
        });
      deptEl.innerHTML = html;
    } catch (e) {
      /* 部门加载失败不阻塞 */
    }
  }

  function deptNames(ids) {
    if (!ids || !ids.length) return '<span class="text-muted">未分配部门</span>';
    return ids
      .map(function (id) {
        var d = departments.find(function (x) {
          return x.id === id;
        });
        return d ? esc(d.name) : "";
      })
      .filter(Boolean)
      .map(function (n) {
        return '<span class="tag-chip">' + n + "</span>";
      })
      .join("");
  }

  function render(items) {
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="6"><div class="table-empty">没有符合条件的用户</div></td></tr>';
      return;
    }
    tbody.innerHTML = items
      .map(function (u) {
        var ops = [];
        ops.push(
          '<button class="btn btn-sm btn-ghost" data-action="edit" data-id="' + u.id + '" data-name="' + esc(u.display_name) + '">编辑</button>'
        );
        ops.push(
          '<button class="btn btn-sm btn-ghost" data-action="reset" data-id="' + u.id + '" data-name="' + esc(u.display_name) + '">重置密码</button>'
        );
        if (u.is_active) {
          ops.push(
            '<button class="btn btn-sm btn-danger-outline" data-action="disable" data-id="' + u.id + '" data-name="' + esc(u.display_name) + '"' +
            (u.id === me.id ? " disabled title=\"不能停用自己\"" : "") + '>停用</button>'
          );
        } else {
          ops.push(
            '<button class="btn btn-sm btn-secondary" data-action="enable" data-id="' + u.id + '" data-name="' + esc(u.display_name) + '">启用</button>'
          );
        }
        ops.push(
          '<button class="btn btn-sm btn-danger-outline" data-action="purge" data-id="' + u.id + '" data-name="' + esc(u.display_name) + '"' +
          (u.id === me.id ? " disabled title=\"不能删除自己\"" : "") + '>删除</button>'
        );
        return (
          "<tr>" +
          '<td><div class="row-title">' + esc(u.display_name) + "</div>" +
          '<div class="row-sub">' + esc(u.username) + (u.email ? " · " + esc(u.email) : "") +
          (u.must_change_password ? " · <span class='text-warning'>待改密</span>" : "") + "</div></td>" +
          "<td>" + ui.roleBadge(u.role) + "</td>" +
          "<td>" + deptNames(u.department_ids) + "</td>" +
          "<td>" + (u.is_active ? '<span class="badge badge-active">启用</span>' : '<span class="badge badge-expired">停用</span>') + "</td>" +
          "<td class=\"text-muted\" style=\"font-size:13px;\">" + esc(ui.formatDateTime(u.last_login_at)) + "</td>" +
          '<td class="row-actions">' + ops.join("") + "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  async function load() {
    tbody.innerHTML = '<tr><td colspan="6"><div class="table-empty">加载中…</div></td></tr>';
    try {
      var data = await api.get("/api/v1/users", buildQuery());
      render(data.items);
      ui.renderPagination(pager, data.page, data.pages, function (p) {
        state.page = p;
        load();
      });
    } catch (e) {
      tbody.innerHTML = '<tr><td colspan="6"><div class="table-empty text-danger">' + esc(e.message) + "</div></td></tr>";
    }
  }

  /* ---------------- 表单弹窗 ---------------- */
  /* 部门勾选列表内联新建（创建后自动勾选新部门） */
  function bindNewDept(overlay) {
    var box = overlay.querySelector("#u-depts");
    if (!box) return;
    var wrap = document.createElement("div");
    wrap.innerHTML =
      '<div class="flex gap-2 mt-2">' +
      '<input class="form-control" id="u-new-dept" maxlength="128" placeholder="新部门名，创建后自动勾选">' +
      '<button type="button" class="btn btn-secondary" id="u-btn-new-dept">＋ 新建</button></div>';
    box.parentElement.appendChild(wrap);
    var input = wrap.querySelector("#u-new-dept");
    var btn = wrap.querySelector("#u-btn-new-dept");

    function renderDepts(checkedIds) {
      var html = departments
        .map(function (d) {
          return (
            '<label><input type="checkbox" data-dept="' + d.id + '"' +
            (checkedIds.indexOf(d.id) >= 0 ? " checked" : "") + ">" +
            "<span>" + esc(d.name) + "</span></label>"
          );
        })
        .join("");
      box.innerHTML = html || '<span class="text-muted">暂无部门</span>';
    }

    function doCreate() {
      var name = input.value.trim();
      if (!name) {
        input.focus();
        return;
      }
      ui.btnLoading(btn, true, "创建中…");
      api
        .post("/api/v1/departments", { name: name })
        .then(async function (res) {
          ui.toast("部门「" + res.name + "」已创建", "success");
          input.value = "";
          var checked = Array.prototype.slice
            .call(box.querySelectorAll("input:checked"))
            .map(function (cb) {
              return parseInt(cb.dataset.dept, 10);
            });
          checked.push(res.id);
          // 刷新筛选下拉与部门缓存（修复：弹窗新建部门后筛选仍是空的）
          await loadDepartments();
          renderDepts(checked);
        })
        .catch(function (e) {
          ui.toast(e.message, "error");
        })
        .finally(function () {
          ui.btnLoading(btn, false);
        });
    }

    btn.addEventListener("click", doCreate);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        doCreate();
      }
    });
  }

  function userFormModal(user) {
    var isEdit = !!user;
    var deptOptions = departments
      .map(function (d) {
        return (
          '<label><input type="checkbox" data-dept="' + d.id + '"' +
          (user && (user.department_ids || []).includes(d.id) ? " checked" : "") + ">" +
          "<span>" + esc(d.name) + "</span></label>"
        );
      })
      .join("");
    var roleOptions = "";
    ["user", "admin"].forEach(function (r) {
      roleOptions +=
        '<option value="' + r + '"' + (user && user.role === r ? " selected" : "") + ">" +
        { user: "普通用户", admin: "管理员" }[r] + "</option>";
    });

    var html =
      '<form id="user-form">' +
      '<div class="form-grid">' +
      '<div class="form-group"><label class="form-label"><span class="required">用户名</span></label>' +
      '<input class="form-control" id="u-username" maxlength="64"' +
      ' value="' + esc(user ? user.username : "") + '" required>' +
      '<p class="form-error">用户名不能为空</p></div>' +
      '<div class="form-group"><label class="form-label"><span class="required">姓名（显示名）</span></label>' +
      '<input class="form-control" id="u-display" maxlength="128" value="' + esc(user ? user.display_name : "") + '" required>' +
      '<p class="form-error">姓名不能为空</p></div>' +
      (isEdit
        ? ""
        : '<div class="form-group full"><label class="form-label"><span class="required">初始密码</span></label>' +
          '<input class="form-control" id="u-password" type="text" placeholder="至少 8 位，含字母和数字" required>' +
          '<p class="form-error">密码至少 8 位且同时包含字母和数字</p></div>') +
      '<div class="form-group"><label class="form-label">邮箱</label>' +
      '<input class="form-control" id="u-email" type="email" maxlength="128" value="' + esc(user ? user.email || "" : "") + '"></div>' +
      '<div class="form-group"><label class="form-label">角色</label>' +
      '<select class="form-control" id="u-role">' + roleOptions + "</select></div>" +
      '<div class="form-group full"><label class="form-label">所属部门</label>' +
      '<div class="check-list" id="u-depts" style="max-height:140px;">' +
      (deptOptions || '<span class="text-muted">暂无部门</span>') +
      "</div></div>" +
      "</div></form>";

    var overlay = ui.openModal(html, { title: isEdit ? "编辑用户" : "新建用户" });
    bindNewDept(overlay);
    var bodyEl = overlay.querySelector(".modal-body");
    // 加 footer
    bodyEl.insertAdjacentHTML(
      "beforeend",
      '<div class="modal-footer" style="margin:16px -20px -20px;">' +
      '<button type="button" class="btn btn-secondary" data-cancel>取消</button>' +
      '<button type="button" class="btn btn-primary" data-save>' + (isEdit ? "保存" : "创建") + "</button>" +
      "</div>"
    );
    return { overlay: overlay, bodyEl: bodyEl };
  }

  async function submitUser(overlay, user) {
    var isEdit = !!user;
    var username = overlay.querySelector("#u-username").value.trim();
    var display = overlay.querySelector("#u-display").value.trim();
    var email = overlay.querySelector("#u-email").value.trim() || null;
    var role = overlay.querySelector("#u-role").value;
    var deptIds = Array.prototype.slice
      .call(overlay.querySelectorAll("#u-depts input:checked"))
      .map(function (cb) {
        return parseInt(cb.dataset.dept, 10);
      });

    var ok = true;
    if (!username) {
      overlay.querySelector("#u-username").closest(".form-group").classList.add("has-error");
      ok = false;
    }
    if (!display) {
      overlay.querySelector("#u-display").closest(".form-group").classList.add("has-error");
      ok = false;
    }
    var pwEl = overlay.querySelector("#u-password");
    if (!isEdit && pwEl) {
      var pw = pwEl.value;
      if (pw.length < 8 || !/[a-zA-Z]/.test(pw) || !/[0-9]/.test(pw)) {
        pwEl.closest(".form-group").classList.add("has-error");
        ok = false;
      } else {
        pwEl.closest(".form-group").classList.remove("has-error");
      }
    }
    if (!ok) {
      ui.toast("请完善必填项", "warning");
      return false;
    }

    var saveBtn = overlay.querySelector("[data-save]");
    ui.btnLoading(saveBtn, true, "保存中…");
    try {
      if (isEdit) {
        await api.put("/api/v1/users/" + user.id, {
          username: username,
          display_name: display,
          email: email,
          role: role,
          department_ids: deptIds,
        });
        ui.toast("用户已更新", "success");
      } else {
        await api.post("/api/v1/users", {
          username: username,
          password: pwEl.value,
          display_name: display,
          email: email,
          role: role,
          department_ids: deptIds,
        });
        ui.toast("用户已创建", "success");
      }
      ui.closeModal(overlay);
      load();
      return true;
    } catch (e) {
      ui.btnLoading(saveBtn, false);
      if (e.code === "FORBIDDEN" || e.code === "CONFLICT" || e.code === "DUPLICATE") {
        ui.toast(e.message, "error");
      } else {
        ui.toast(e.message, "error");
      }
      return false;
    }
  }

  /* ---------------- 事件 ---------------- */
  document.getElementById("btn-new-user").addEventListener("click", function () {
    var m = userFormModal(null);
    m.overlay.querySelector("[data-save]").addEventListener("click", function () {
      submitUser(m.overlay, null);
    });
    m.overlay.querySelector("[data-cancel]").addEventListener("click", function () {
      ui.closeModal(m.overlay);
    });
  });

  tbody.addEventListener("click", async function (e) {
    var btn = e.target.closest("[data-action]");
    if (!btn) return;
    var id = parseInt(btn.dataset.id, 10);
    var name = btn.dataset.name || "";

    if (btn.dataset.action === "edit") {
      var detail = await api.get("/api/v1/users/" + id);
      var m = userFormModal(detail);
      m.overlay.querySelector("[data-save]").addEventListener("click", function () {
        submitUser(m.overlay, detail);
      });
      m.overlay.querySelector("[data-cancel]").addEventListener("click", function () {
        ui.closeModal(m.overlay);
      });
      return;
    }

    if (btn.dataset.action === "reset") {
      var overlay = ui.openModal(
        '<div class="alert alert-warning"><span>重置后该用户需修改密码才能使用系统。</span></div>' +
          '<div class="form-group mt-3"><label class="form-label"><span class="required">新密码</span></label>' +
          '<input class="form-control" type="text" id="rp-password" placeholder="至少 8 位，含字母和数字（将下发给用户）">' +
          '<p class="form-error">密码至少 8 位且同时包含字母和数字</p></div>' +
          '<div class="modal-footer" style="margin:16px -20px -20px;">' +
          '<button type="button" class="btn btn-secondary" data-cancel>取消</button>' +
          '<button type="button" class="btn btn-primary" data-save>确认重置</button></div>',
        { title: '重置密码：' + name, size: "sm" }
      );
      overlay.querySelector("[data-cancel]").addEventListener("click", function () {
        ui.closeModal(overlay);
      });
      overlay.querySelector("[data-save]").addEventListener("click", async function () {
        var pw = overlay.querySelector("#rp-password").value;
        if (pw.length < 8 || !/[a-zA-Z]/.test(pw) || !/[0-9]/.test(pw)) {
          overlay.querySelector("#rp-password").closest(".form-group").classList.add("has-error");
          return;
        }
        var save = overlay.querySelector("[data-save]");
        ui.btnLoading(save, true, "重置中…");
        try {
          await api.post("/api/v1/users/" + id + "/reset-password", { new_password: pw });
          ui.toast("密码已重置", "success");
          ui.closeModal(overlay);
        } catch (err) {
          ui.toast(err.message, "error");
          ui.btnLoading(save, false);
        }
      });
      return;
    }

    if (btn.dataset.action === "disable") {
      var ok = await ui.confirmModal({
        title: "停用用户",
        danger: true,
        message: "停用后「" + name + "」将无法登录系统，也不再计入通知阅读统计（可随时启用恢复）。确定停用吗？",
        confirmText: "确认停用",
      });
      if (!ok) return;
      ui.btnLoading(btn, true, "停用中…");
      try {
        await api.del("/api/v1/users/" + id);
        ui.toast("用户已停用", "success");
        load();
      } catch (err) {
        ui.toast(err.message, "error");
        ui.btnLoading(btn, false);
      }
      return;
    }

    if (btn.dataset.action === "enable") {
      ui.btnLoading(btn, true, "启用中…");
      try {
        await api.put("/api/v1/users/" + id, { is_active: true });
        ui.toast("用户已启用", "success");
        load();
      } catch (err) {
        ui.toast(err.message, "error");
        ui.btnLoading(btn, false);
      }
      return;
    }

    if (btn.dataset.action === "purge") {
      // 产品决策（2026-09）：删除不做二次确认，直接执行；
      // 创建过内容的账号由后端 409 拒并说明原因
      ui.btnLoading(btn, true, "删除中…");
      try {
        var res = await api.del("/api/v1/users/" + id + "/purge");
        ui.toast("账号「" + (res.username || name) + "」已删除（阅读记录已清除、操作日志保留）", "success");
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
    roleEl.value = "";
    activeEl.value = "";  // 重置回默认视图：全部（停用不隐藏，列表直显）
    deptEl.value = "";
    state.page = 1;
    load();
  });
  keywordEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      state.page = 1;
      load();
    }
  });

  loadDepartments().then(load);
})();