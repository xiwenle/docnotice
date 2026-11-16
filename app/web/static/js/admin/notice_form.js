/**
 * admin/notice_form.js —— 通知新建/编辑页（admin）
 * - 创建：JSON（无文件）或 multipart（含文件），字段与 API.md §7 一致
 *   2026-11-04 起 POST /notices 即创建通知（单事务发布），无需再调 /publish
 * - 编辑：published 显示警告 + 必填修改原因 + confirm_modify
 * - 后端 409 NOTICE_MODIFY_REQUIRES_REASON / VALIDITY_CONFLICT 回显
 * REST：POST/GET/PUT /api/v1/notices；辅助：/documents、/departments、/users
 */
(function () {
  "use strict";

  var editId = window.EDIT_ID;
  var esc = ui.escapeHtml;
  var notice = null;

  var titleEl = document.getElementById("f-title");
  var typeEl = document.getElementById("f-type");
  var contentEl = document.getElementById("f-content");
  var effAtEl = document.getElementById("f-effective-at");
  var deadlineEl = document.getElementById("f-deadline-at");
  var form = document.getElementById("notice-form");
  var saveBtn = document.getElementById("btn-save");

  var allDocs = [];
  var linkedDocs = []; // 编辑态已关联资料（含 archived 标记，恒定并入候选池）
  var selectedDocObjs = {}; // 搜索结果里勾选过的资料：清空/换关键词时不丢已选
  var docSearchTimer = null;
  var allDepts = [];
  var allUsers = [];
  var userTotal = 0;
  var deptTotal = 0;
  var deptSearchTimer = null;
  var userSearchTimer = null;
  var allNoticeTypes = [];
  var selectedDocIds = new Set();
  var selectedDeptIds = new Set();
  var selectedUserIds = new Set();
  // 已选对象缓存：服务端搜索结果不含已选者时，渲染仍需把已选项置顶保真
  // （编辑回显按 id 拉详情；勾选变化时同步维护），防静默丢已选
  var selectedDeptObjs = {};
  var selectedUserObjs = {};
  var lastDeptPool = [];
  var lastUserPool = [];
  var activeScope = "all";

  var publishedWarning = document.getElementById("published-warning");
  var reasonBox = document.getElementById("reason-box");
  var confirmCb = document.getElementById("f-confirm-modify");
  var modifyReason = document.getElementById("f-modify-reason");

  /* ---------------- 加载选项 ---------------- */
  /* 资料候选：搜索走服务端（候选池仅 100 条，本地过滤会漏掉第 101 条之后的资料），
     并把编辑态已关联且未失效的资料恒定并入——否则老关联不在池里会被当成失效关联丢掉 */
  function mergeLinkedDocs() {
    var byId = {};
    var merged = [];
    function put(x) {
      if (!x || byId[x.id] || x.archived) return;
      byId[x.id] = true;
      merged.push(x);
    }
    // 已勾选者优先入池：服务端按关键词换页/清空时，勾选态仍可见且不会静默丢失。
    Object.keys(selectedDocObjs).forEach(function (id) {
      if (selectedDocIds.has(parseInt(id, 10))) put(selectedDocObjs[id]);
    });
    linkedDocs.forEach(put);  // 编辑态老关联也恒定并入（列表置顶可见）
    allDocs.forEach(put);
    allDocs = merged;
  }

  async function loadDocCandidates(kw) {
    var params = { page: 1, page_size: 100 };
    if (kw) params.keyword = kw;
    var fetched = [];
    try {
      var d = await api.get("/api/v1/documents", params);
      fetched = d.items || [];
    } catch (e) {
      fetched = [];
    }
    allDocs = fetched;
    mergeLinkedDocs();
    renderDocList(kw || "");
  }

  async function loadAll(force) {
    var jobs = [];
    if (!allDocs.length || force) {
      jobs.push(loadDocCandidates(""));
    }
    if (!allDepts.length || force) {
      jobs.push(
        api.get("/api/v1/departments", { page_size: 100 }).then(function (d) {
          allDepts = d.items || [];
          deptTotal = typeof d.total === "number" ? d.total : allDepts.length;
        }).catch(function () {})
      );
    }
    if (!allUsers.length || force) {
      jobs.push(
        api.get("/api/v1/users", { page: 1, page_size: 100 }).then(function (d) {
          // 管理员不进阅读对象（2026-11-03），候选里不出现
          allUsers = (d.items || []).filter(function (u) { return u.role !== "admin"; });
          userTotal = allUsers.length;
        }).catch(function () {})
      );
    }
    await Promise.all(jobs);
    renderDocList("");
    renderDeptList("");
    renderUserList("");
  }

  /* 部门/用户候选：搜索走服务端 keyword（对齐资料候选），已选者恒定并入池 */
  function mergeSelectedDepts(fetched) {
    var byId = {};
    var merged = [];
    function put(x) {
      if (!x || byId[x.id]) return;
      byId[x.id] = true;
      merged.push(x);
    }
    selectedDeptIds.forEach(function (id) {
      put(selectedDeptObjs[id]);
    });
    (fetched || allDepts).forEach(put);
    return merged;
  }

  function mergeSelectedUsers(fetched) {
    var byId = {};
    var merged = [];
    function put(x, keepInactive) {
      if (!x || byId[x.id]) return;
      if (x.is_active === false && !keepInactive) return;
      byId[x.id] = true;
      merged.push(x);
    }
    selectedUserIds.forEach(function (id) {
      put(selectedUserObjs[id], true); // 已选者即使被停用也保真显示（否则编辑回显丢勾选）
    });
    (fetched || allUsers).forEach(function (u) {
      put(u, false);
    });
    return merged;
  }

  async function loadDeptCandidates(kw) {
    var params = { page_size: 100 };
    if (kw) params.keyword = kw;
    var fetched = [];
    try {
      var d = await api.get("/api/v1/departments", params);
      fetched = d.items || [];
      deptTotal = typeof d.total === "number" ? d.total : fetched.length;
    } catch (e) {
      fetched = [];
    }
    allDepts = fetched;
    renderDeptList(kw || "");
  }

  async function loadUserCandidates(kw) {
    var params = { page: 1, page_size: 100 };
    if (kw) params.keyword = kw;
    var fetched = [];
    try {
      var d = await api.get("/api/v1/users", params);
      fetched = (d.items || []).filter(function (u) { return u.role !== "admin"; });
      userTotal = fetched.length;
    } catch (e) {
      fetched = [];
    }
    allUsers = fetched;
    renderUserList(kw || "");
  }

  /* ---------------- 通知类型（自定义词表 + 内联新建） ---------------- */
  async function loadNoticeTypes() {
    try {
      var data = await api.get("/api/v1/notice-types");
      allNoticeTypes = (data.items || []).filter(function (t) {
        return t.is_active !== false;
      });
      var html = '<option value="">（不选择类型）</option>';
      html += allNoticeTypes
        .map(function (t) {
          return '<option value="' + esc(t.name) + '">' + esc(t.name) + "</option>";
        })
        .join("");
      html += '<option value="__new__">＋ 新建类型…</option>';
      typeEl.innerHTML = html;
    } catch (e) {
      typeEl.innerHTML = '<option value="">（不选择类型）</option>';
    }
  }

  function openNewTypeModal() {
    var overlay = ui.openModal(
      '<div class="form-group"><label class="form-label"><span class="required">类型名称</span></label>' +
      '<input class="form-control" id="nt-name" maxlength="32" placeholder="如：培训通知">' +
      '<p class="form-error">类型名称不能为空</p></div>' +
      '<div class="modal-footer">' +
      '<button type="button" class="btn btn-secondary" data-close>取消</button>' +
      '<button type="button" class="btn btn-primary" id="nt-submit">创建并选中</button></div>',
      { title: "新建通知类型", size: "sm" }
    );
    overlay.querySelector("[data-close]").addEventListener("click", function () {
      ui.closeModal(overlay);
    });
    overlay.querySelector("#nt-name").addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        overlay.querySelector("#nt-submit").click();
      }
    });
    overlay.querySelector("#nt-submit").addEventListener("click", async function () {
      var nameEl = overlay.querySelector("#nt-name");
      var name = nameEl.value.trim();
      if (!name) {
        nameEl.closest(".form-group").classList.add("has-error");
        nameEl.focus();
        return;
      }
      var btn = overlay.querySelector("#nt-submit");
      ui.btnLoading(btn, true, "创建中…");
      try {
        var res = await api.post("/api/v1/notice-types", { name: name, sort_order: 0 });
        ui.closeModal(overlay);
        ui.toast("通知类型「" + res.name + "」已创建", "success");
        await loadNoticeTypes();
        typeEl.value = res.name;
      } catch (e) {
        ui.toast(e.message, "error");
        ui.btnLoading(btn, false);
      }
    });
    setTimeout(function () {
      overlay.querySelector("#nt-name").focus();
    }, 50);
  }

  typeEl.addEventListener("change", function () {
    if (typeEl.value === "__new__") {
      typeEl.value = "";
      openNewTypeModal();
    }
  });

  /* ---------------- 列表渲染（带本地过滤） ---------------- */
  function renderDocList(kw) {
    var list = document.getElementById("doc-list");
    // 搜索条件已由 /api/v1/documents?keyword= 在服务端按 title/summary 过滤；
    // 这里只负责渲染与已选置顶，不能再按 title 做本地二次过滤，
    // 否则资料说明命中但标题不含关键词的候选会被静默藏掉。
    var items = allDocs.slice();
    // 选中项优先置顶
    items.sort(function (a, b) {
      return (selectedDocIds.has(b.id) ? 1 : 0) - (selectedDocIds.has(a.id) ? 1 : 0);
    });
    if (!items.length) {
      list.innerHTML = '<div class="empty-state" style="padding:12px;"><p>没有匹配的资料</p></div>';
      return;
    }
    list.innerHTML = items
      .map(function (d) {
        var on = selectedDocIds.has(d.id);
        return (
          '<label><input type="checkbox" data-id="' + d.id + '"' + (on ? " checked" : "") + ">" +
          '<span>' + esc(d.title) + "</span>" +
          '<span class="list-meta">' +
          (d.status ? " · " + ui.statusBadge(d.status) : "") + "</span></label>"
        );
      })
      .join("");
  }

  function renderDeptList(kw) {
    var list = document.getElementById("dept-list");
    kw = (kw || "").toLowerCase();
    var pool = (lastDeptPool = mergeSelectedDepts());
    var items = pool.filter(function (d) {
      return d.is_active !== false && (!kw || (d.name || "").toLowerCase().includes(kw));
    });
    if (!items.length) {
      list.innerHTML = '<div class="empty-state" style="padding:12px;"><p>暂无匹配部门' +
        (deptTotal > 100 ? "（共 " + deptTotal + " 个，可输入关键词搜索）" : "（请先到部门管理创建）") + "</p></div>";
      return;
    }
    list.innerHTML = items
      .map(function (d) {
        var on = selectedDeptIds.has(d.id);
        return (
          '<label><input type="checkbox" data-id="' + d.id + '"' + (on ? " checked" : "") + ">" +
          '<span>' + esc(d.name) + "</span>" +
          '<span class="list-meta">' + esc(d.code || "") + "</span></label>"
        );
      })
      .join("");
  }

  function renderUserList(kw) {
    var list = document.getElementById("user-list");
    kw = (kw || "").toLowerCase();
    var pool = (lastUserPool = mergeSelectedUsers());
    var items = pool.filter(function (u) {
      return (!kw || (u.display_name || "").toLowerCase().includes(kw) || (u.username || "").toLowerCase().includes(kw));
    });
    if (!items.length) {
      list.innerHTML = '<div class="empty-state" style="padding:12px;"><p>暂无匹配用户' +
        (userTotal > 100 ? "（共 " + userTotal + " 人，可输入姓名/账号搜索）" : "") + "</p></div>";
      return;
    }
    var hint = userTotal > items.length
      ? '<div class="text-muted" style="padding:6px 12px;font-size:12px;">共 ' + userTotal + " 人，当前显示 " + items.length + " 人（可输入姓名/账号搜索）</div>"
      : "";
    list.innerHTML = hint + items
      .map(function (u) {
        var on = selectedUserIds.has(u.id);
        return (
          '<label><input type="checkbox" data-id="' + u.id + '"' + (on ? " checked" : "") + ">" +
          '<span>' + esc(u.display_name) + "（" + esc(u.username) + "）</span>" +
          '<span class="list-meta">' + esc(u.department_ids && u.department_ids.length ? "部门×" + u.department_ids.length : "") + "</span></label>"
        );
      })
      .join("");
  }

  /* ---------------- 编辑模式 ---------------- */
  async function loadNotice() {
    if (!editId) return;
    try {
      var d = await api.get("/api/v1/notices/" + editId);
      notice = d;
      titleEl.value = d.title || "";
      typeEl.value = d.notice_type || "";
      contentEl.value = d.content || "";
      if (d.effective_at) {
        effAtEl.value = (d.effective_at || "").slice(0, 16);
      }
      if (d.deadline_at) {
        deadlineEl.value = (d.deadline_at || "").slice(0, 16);
      }
      linkedDocs = (d.documents || []).map(function (x) {
        return { id: x.id, title: x.title, archived: !!x.archived };
      });
      linkedDocs.forEach(function (x) {
        if (!x.archived) {
          selectedDocIds.add(x.id);
          selectedDocObjs[x.id] = x;
        }
      });

      // 回填阅读对象
      if (d.audience_meta) {
        var m = d.audience_meta;
        activeScope = m.scope || "all";
        var radio = form.querySelector('input[name="target-scope"][value="' + activeScope + '"]');
        if (radio) radio.checked = true;
        (m.department_ids || []).forEach(function (id) {
          selectedDeptIds.add(id);
        });
        (m.user_ids || []).forEach(function (id) {
          selectedUserIds.add(id);
        });
        // 已选对象缓存：候选池是分页/搜索结果，未必包含已选者；
        // 按 id 拉详情保证回显置顶保真（部门无单查端点，走列表过滤）
        if (m.user_ids && m.user_ids.length) {
          m.user_ids.forEach(function (id) {
            if (selectedUserObjs[id]) return;
            api.get("/api/v1/users/" + id).then(function (u) {
              selectedUserObjs[u.id] = u;
              renderUserList(document.getElementById("user-filter").value);
            }).catch(function () {});
          });
        }
        if (m.department_ids && m.department_ids.length) {
          api.get("/api/v1/departments", { page_size: 100 }).then(function (d) {
            var byId = {};
            (d.items || []).forEach(function (dep) { byId[dep.id] = dep; });
            m.department_ids.forEach(function (id) {
              if (byId[id]) selectedDeptObjs[id] = byId[id];
            });
            renderDeptList(document.getElementById("dept-filter").value);
          }).catch(function () {});
        }
      }

      // 编辑页显示修改留痕警告与原因区（草稿下线后，只能编辑正常通知或已删除通知）
      if (d.status === "published") {
        publishedWarning.classList.remove("hidden");
        reasonBox.style.display = "block";
      } else if (d.status === "cancelled") {
        var inputs = form.querySelectorAll("input,textarea,select");
        inputs.forEach(function (el) {
          el.disabled = true;
        });
        if (saveBtn) saveBtn.disabled = true;
        document.getElementById("notice-state-banner").innerHTML =
          '<div class="alert alert-danger"><span>该通知已删除（在回收站），不可编辑。</span></div>';
      }
    } catch (e) {
      ui.toast(e.message, "error");
      history.back();
    }
  }

  async function loadOptionsAndFill() {
    await loadAll();
    // 只剪"确认已失效"的关联（后端对回收站中的资料会 400）；
    // 未加载进候选池的老关联必须保留，否则保存即静默丢关联
    mergeLinkedDocs();
    var dropped = linkedDocs.filter(function (x) {
      return x.archived;
    });
    if (dropped.length) {
      ui.toast(
        "已移除 " + dropped.length + " 条失效关联（资料在回收站）：" +
          dropped.map(function (x) { return x.title; }).join("、"),
        "warn"
      );
    }
    renderDocList("");
    var docList = document.getElementById("doc-list");
    docList.querySelectorAll("input[type=checkbox]").forEach(function (cb) {
      if (selectedDocIds.has(parseInt(cb.dataset.id, 10))) cb.checked = true;
    });
    renderDeptList("");
    renderUserList("");
  }

  /* ---------------- target 组装 ---------------- */
  function buildTarget() {
    var scope = activeScope;
    var t = { scope: scope };
    if (scope === "department") {
      t.department_ids = Array.from(selectedDeptIds);
    } else if (scope === "user") {
      t.user_ids = Array.from(selectedUserIds);
    }
    return t;
  }

  function toISO(value) {
    return value || null;
  }

  /* ---------------- 提交 ---------------- */
  function buildBody(extra) {
    var body = {
      title: titleEl.value.trim(),
      content: contentEl.value.trim() || null,
      notice_type: typeEl.value || null,
      effective_at: toISO(effAtEl.value),
      deadline_at: deadlineEl.value ? toISO(deadlineEl.value) : null,
      document_ids: Array.from(selectedDocIds),
      ...extra,
    };
    // 通知禁止修改阅读对象（后端规则 §22）：不传 target 保持原对象
    if (!(editId && notice && notice.status === "published")) {
      body.target = buildTarget();
    } else {
      delete body.target;
    }
    return body;
  }

  function validate() {
    var ok = true;
    if (!titleEl.value.trim()) {
      titleEl.closest(".form-group").classList.add("has-error");
      ok = false;
    } else {
      titleEl.closest(".form-group").classList.remove("has-error");
    }
    if (!contentEl.value.trim() && !document.getElementById("upload-input").files.length && notice && notice.has_file) {
      // 已有文件可保留
    } else if (!contentEl.value.trim() && !document.getElementById("upload-input").files.length && !(notice && notice.has_file)) {
      ui.toast("请填写通知正文或选择通知文件，至少填一项", "warning");
      ok = false;
    }
    // 编辑通知：必须确认 + 原因（字段级红框，不再只弹 toast）
    if (notice && notice.status === "published") {
      var reasonGroup = modifyReason.closest(".form-group");
      if (!modifyReason.value.trim()) {
        reasonGroup.classList.add("has-error");
        ui.toast("通知修改必须填写修改原因", "warning");
        ok = false;
      } else {
        reasonGroup.classList.remove("has-error");
      }
      var cbWrap = confirmCb.closest(".checkbox-line") || confirmCb.closest(".form-group");
      if (!confirmCb.checked) {
        cbWrap.classList.add("has-error");
        ui.toast("请勾选「我已了解修改将被记录」", "warning");
        ok = false;
      } else {
        cbWrap.classList.remove("has-error");
      }
    }
    return ok;
  }

  function submitNotice(andPublish) {
    return new Promise(function (resolve) {
      var fileInput = document.getElementById("upload-input");
      var hasNewFile = !!fileInput.files.length;
      var numSelectedDept = selectedDeptIds.size;
      var numSelectedUser = selectedUserIds.size;

      // 对象非空校验
      if (activeScope === "department" && !numSelectedDept) {
        ui.toast("请选择至少一个部门", "warning");
        resolve(false);
        return;
      }
      if (activeScope === "user" && !numSelectedUser) {
        ui.toast("请选择至少一个用户", "warning");
        resolve(false);
        return;
      }
      if (!validate()) {
        resolve(false);
        return;
      }

      var extra = {};
      if (notice && notice.status === "published") {
        extra.confirm_modify = confirmCb.checked;
        extra.modified_reason = modifyReason.value.trim();
      } else {
        extra.confirm_modify = false;
        extra.modified_reason = null;
      }

      ui.btnLoading(saveBtn, true, andPublish ? "创建中…" : "保存中…");

      var doSave = function () {
        if (hasNewFile) {
          // multipart：复杂字段传 JSON 字符串（与后端 multipart 解析一致）
          var fd = new FormData();
          fd.append("title", titleEl.value.trim());
          fd.append("content", contentEl.value.trim() || "");
          if (typeEl.value) fd.append("notice_type", typeEl.value);
          fd.append("effective_at", effAtEl.value || "");
          fd.append("deadline_at", deadlineEl.value || "");
          fd.append("document_ids", JSON.stringify(Array.from(selectedDocIds)));
          // 通知禁止修改阅读对象：multipart 同样省略 target
          if (!(editId && notice && notice.status === "published")) {
            fd.append("target", JSON.stringify(buildTarget()));
          }
          if (notice && notice.status === "published") {
            fd.append("confirm_modify", "true");
            fd.append("modified_reason", modifyReason.value.trim() || "");
          }
          fd.append("file", fileInput.files[0]);
          if (editId) {
            return api.uploadPut("/api/v1/notices/" + editId, fd).then(function (r) {
              return { data: r, created: false };
            });
          }
          return api.upload("/api/v1/notices", fd).then(function (r) {
            return { data: r, created: true };
          });
        }
        var body = buildBody(extra);
        if (editId) {
          return api.put("/api/v1/notices/" + editId, body).then(function (r) {
            return { data: r, created: false };
          });
        }
        return api.post("/api/v1/notices", body).then(function (r) {
          return { data: r, created: true };
        });
      };

      doSave()
        .then(function (res) {
          var nid = editId || res.data.id;
          // 2026-11-04：创建即发布（后端单事务完成），不再有第二步 /publish
          if (andPublish && !editId) {
            var count = res.data.target_user_count;
            ui.toast(
              "创建成功" + (count !== undefined ? "，应阅读人数：" + count : ""),
              "success"
            );
            location.href = "/admin/notices/" + nid + "/stats";
            return null;
          }
          ui.toast(editId ? "通知已更新" : "通知已创建", "success");
          location.href = "/notices/" + nid;
          return null;
        })
        .catch(function (err) {
          ui.btnLoading(saveBtn, false);
          if (err.code === "NOTICE_MODIFY_REQUIRES_REASON") {
            reasonBox.style.display = "block";
            document.getElementById("error-banner").innerHTML =
              '<div class="alert alert-danger"><span>' +
              esc(err.message) + "</span></div>";
          } else if (err.code === "VALIDITY_CONFLICT") {
            document.getElementById("error-banner").innerHTML =
              '<div class="alert alert-danger"><span>' +
              esc(err.message) + "</span></div>";
          } else {
            ui.toast(err.message, "error");
          }
          resolve(false);
          return null;
        })
        .then(function () {
          resolve(true);
        });
    });
  }

  /* ---------------- 发布前确认（还原后端 confirm_targets 的安全检查） ---------------- */
  function buildPublishConfirmMessage() {
    var targetDesc;
    if (activeScope === "all") {
      targetDesc = "全体非管理员用户（当前共 " + userTotal + " 人）";
    } else if (activeScope === "department") {
      var names = allDepts
        .filter(function (d) { return selectedDeptIds.has(d.id); })
        .map(function (d) { return d.name; });
      targetDesc = selectedDeptIds.size + " 个部门：" + names.join("、");
    } else {
      targetDesc = selectedUserIds.size + " 名指定用户";
    }
    var lines = ["阅读对象：" + targetDesc];
    lines.push("创建后立即对目标人员可见并物化阅读快照，操作不可撤回。");
    var t = typeEl.value;
    if (t === "废止" || t === "替换") {
      lines.push("该通知类型为「" + t + "」：创建将自动截断关联资料的有效期。");
    }
    return lines.join("；");
  }

  /* ---------------- 事件绑定 ---------------- */
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (editId) {
      // 编辑：普通保存
      submitNotice(false);
      return;
    }
    // 新建：先校验对象与表单，再弹创建确认（确认后才创建通知）
    if (activeScope === "department" && !selectedDeptIds.size) {
      ui.toast("请选择至少一个部门", "warning");
      return;
    }
    if (activeScope === "user" && !selectedUserIds.size) {
      ui.toast("请选择至少一个用户", "warning");
      return;
    }
    if (!validate()) return;
    ui.confirmModal({
      title: "确认创建通知",
      message: buildPublishConfirmMessage(),
      confirmText: "确认创建",
    }).then(function (confirmed) {
      if (confirmed) submitNotice(true);
    });
  });

  // 对象范围切换
  form.querySelectorAll('input[name="target-scope"]').forEach(function (radio) {
    radio.addEventListener("change", function () {
      activeScope = radio.value;
      document.getElementById("dept-picker").classList.toggle("hidden", activeScope !== "department");
      document.getElementById("user-picker").classList.toggle("hidden", activeScope !== "user");
    });
  });

  // 候选勾选走容器事件委托：列表 innerHTML 重渲染（搜索/回填）不会丢事件，
  // 也不会出现「点击瞬间重渲染导致 change 随旧 DOM 丢失」的时序竞争
  document.getElementById("dept-list").addEventListener("change", function (e) {
    var cb = e.target;
    if (!cb || cb.type !== "checkbox") return;
    var id = parseInt(cb.dataset.id, 10);
    if (cb.checked) {
      selectedDeptIds.add(id);
      var obj = lastDeptPool.find(function (d) { return d.id === id; });
      if (obj) selectedDeptObjs[id] = obj;
    } else {
      selectedDeptIds.delete(id);
    }
  });
  document.getElementById("user-list").addEventListener("change", function (e) {
    var cb = e.target;
    if (!cb || cb.type !== "checkbox") return;
    var id = parseInt(cb.dataset.id, 10);
    if (cb.checked) {
      selectedUserIds.add(id);
      var obj = lastUserPool.find(function (u) { return u.id === id; });
      if (obj) selectedUserObjs[id] = obj;
    } else {
      selectedUserIds.delete(id);
    }
  });
  document.getElementById("doc-list").addEventListener("change", function (e) {
    var cb = e.target;
    if (!cb || cb.type !== "checkbox") return;
    var id = parseInt(cb.dataset.id, 10);
    if (cb.checked) {
      selectedDocIds.add(id);
      var obj = allDocs.find(function (d) { return d.id === id; });
      if (obj) selectedDocObjs[id] = obj;
    } else {
      selectedDocIds.delete(id);
      delete selectedDocObjs[id];
    }
  });

  // 筛选输入（资料搜索走服务端，候选池不再受 100 条限制）
  document.getElementById("doc-search").addEventListener("input", function (e) {
    var kw = e.target.value.trim();
    if (docSearchTimer) clearTimeout(docSearchTimer);
    docSearchTimer = setTimeout(function () {
      loadDocCandidates(kw);
    }, 300);
  });
  document.getElementById("dept-filter").addEventListener("input", function (e) {
    var kw = e.target.value.trim();
    if (deptSearchTimer) clearTimeout(deptSearchTimer);
    deptSearchTimer = setTimeout(function () {
      loadDeptCandidates(kw);
    }, 300);
  });
  document.getElementById("user-filter").addEventListener("input", function (e) {
    var kw = e.target.value.trim();
    if (userSearchTimer) clearTimeout(userSearchTimer);
    userSearchTimer = setTimeout(function () {
      loadUserCandidates(kw);
    }, 300);
  });

  // 文件选择
  var input = document.getElementById("upload-input");
  var drop = document.getElementById("upload-drop");
  var nameEl = document.getElementById("upload-name");
  input.addEventListener("change", function () {
    var f = input.files[0];
    if (!f) return;
    nameEl.textContent = f.name + "（" + ui.formatBytes(f.size) + "）";
    drop.classList.add("has-file");
  });

  /* ---------------- 初始化 ---------------- */
  (async function init() {
    await Promise.all([loadNoticeTypes(), loadAll()]);
    if (editId) {
      await loadNotice();
      await loadOptionsAndFill();
    }
  })();
})();