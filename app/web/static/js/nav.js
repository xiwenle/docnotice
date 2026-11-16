/**
 * nav.js —— 外壳交互（全局，仅登录后加载）
 *
 * 2026-09-14 换肤：外壳由「顶栏 + 管理下拉」改为「左侧栏平铺」。
 * - 桌面端侧栏可收起为窄栏（localStorage 记忆）
 * - 窄屏侧栏为抽屉：面包屑顶条 ☰ 打开，点遮罩 / Esc / 点链接关闭
 * - 面包屑顶条快捷搜索（回车提交；按「/」聚焦）→ /documents?keyword=
 * - 修改密码弹窗 / 退出登录
 *
 * 原顶栏的用户下拉、管理下拉逻辑已随换肤移除（对应 DOM 已不存在）。
 */
(function () {
  "use strict";

  var currentUser = ui.currentUser();

  var RAIL_KEY = "ui.sideRail";
  var GROUP_KEY = "ui.sideGroup";

  function isDesktop() {
    return window.matchMedia("(min-width: 901px)").matches;
  }

  /* ---- 侧栏：桌面端收起为窄栏（跨会话记忆） ---- */
  var sidebar = document.getElementById("app-side");
  var collapseBtn = document.getElementById("sb-collapse");

  function readRailPref() {
    try {
      return localStorage.getItem(RAIL_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  function applyRail(rail) {
    document.body.classList.toggle("rail", !!rail);
    if (collapseBtn) {
      collapseBtn.setAttribute("aria-expanded", rail ? "false" : "true");
      collapseBtn.textContent = rail ? "›" : "‹";
    }
  }

  /* 首帧防抖动的收尾（2026-11-03）：
     base.html 的内联脚本在 <body> 前加了 html.rail-pending 并禁用过渡，
     这里把最终态同步到 body 后立刻摘掉该类，恢复正常过渡。
     用 rAF 确保浏览器已经用"无过渡"的样式完成首帧布局。 */
  function settleRailPending() {
    requestAnimationFrame(function () {
      document.documentElement.classList.remove("rail-pending");
    });
  }

  if (sidebar && collapseBtn) {
    if (isDesktop()) applyRail(readRailPref());
    settleRailPending();

    collapseBtn.addEventListener("click", function () {
      var rail = !document.body.classList.contains("rail");
      applyRail(rail);
      try {
        localStorage.setItem(RAIL_KEY, rail ? "1" : "0");
      } catch (e) {
        /* 隐私模式下 localStorage 不可写：忽略，仅本次会话生效 */
      }
    });
  }

  /* ---- 管理员低频分组：默认折叠，记住展开态（2026-11-08） ----
     服务端在当前页命中分组时渲染 open，保证高亮项/当前入口不会藏在折叠块里；
     nav.js 在 DOM 就绪后恢复用户此前展开过的分组。纯 UI 偏好，不参与鉴权、不改任何路由。 */
  function readGroupPref() {
    try {
      var pref = JSON.parse(localStorage.getItem(GROUP_KEY) || "{}");
      return pref && typeof pref === "object" ? pref : {};
    } catch (e) {
      return {};
    }
  }

  function writeGroupPref(changedKey) {
    var pref = readGroupPref();
    document.querySelectorAll("[data-sb-group]").forEach(function (group) {
      var key = group.dataset.sbGroup;
      if (!changedKey || key === changedKey) pref[key] = group.open;
    });
    try {
      localStorage.setItem(GROUP_KEY, JSON.stringify(pref));
    } catch (e) {
      /* 隐私模式下 localStorage 不可写：忽略，仅本次会话生效 */
    }
  }

  function applyGroupPref() {
    if (!sidebar) return;
    var pref = readGroupPref();
    sidebar.querySelectorAll("[data-sb-group]").forEach(function (group) {
      var key = group.dataset.sbGroup;
      var serverOpen = group.open;  // 当前页命中时由模板展开，保证当前项可见
      var active = group.querySelector(".sb-item.on, .sb-item[aria-current='page']");
      var wanted = !!active || serverOpen || (Object.prototype.hasOwnProperty.call(pref, key) && !!pref[key]);
      group.open = !!wanted;
      // 只把用户亲手开合写进偏好；服务端为当前页强制展开不应变成全局默认值。
      var summary = group.querySelector("summary");
      if (summary) {
        summary.addEventListener("click", function () {
          // 只把用户亲手开合的这一组写进偏好；服务端为当前页强制展开
          // 不应变成全局默认值，也不会顺手覆盖另一组的记忆。
          setTimeout(function () { writeGroupPref(key); }, 0);
        });
      }
    });
  }

  applyGroupPref();

  /* ---- 侧栏：窄屏抽屉 ---- */
  var menuBtn = document.getElementById("menu-btn");
  var backdrop = document.getElementById("side-backdrop");

  function closeDrawer() {
    document.body.classList.remove("drawer");
    if (menuBtn) menuBtn.setAttribute("aria-expanded", "false");
  }

  function openDrawer() {
    document.body.classList.add("drawer");
    if (menuBtn) menuBtn.setAttribute("aria-expanded", "true");
  }

  if (menuBtn) {
    menuBtn.setAttribute("aria-expanded", "false");
    menuBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      if (document.body.classList.contains("drawer")) closeDrawer();
      else openDrawer();
    });
  }
  if (backdrop) backdrop.addEventListener("click", closeDrawer);
  if (sidebar) {
    sidebar.addEventListener("click", function (e) {
      var link = e.target && e.target.closest ? e.target.closest("a") : null;
      if (link) closeDrawer();
    });
  }
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeDrawer();
  });
  window.addEventListener("resize", function () {
    if (isDesktop()) {
      closeDrawer();
      if (sidebar && collapseBtn) applyRail(readRailPref());
    } else {
      applyRail(false);
    }
  });

  /* ---- 面包屑顶条快捷搜索 ---- */
  var cbSearch = document.getElementById("cb-search");
  var cbInput = document.getElementById("cb-q");
  var localSearch = document.getElementById("f-keyword");
  var dashboardSearch = document.getElementById("gs-input");
  var globalLink = document.getElementById("cb-global-link");
  // 列表页已有页内搜索时隐藏顶条输入框，避免“两个输入框、两种范围”；
  // 保留一个紧凑的「搜索资料」链接作为全局入口（详情/备份/分类等仍用顶条输入）。
  if (cbSearch && localSearch) {
    cbSearch.hidden = true;
    if (globalLink) globalLink.hidden = false;
  }
  if (cbSearch && cbInput) {
    cbSearch.addEventListener("submit", function (e) {
      e.preventDefault();
      var kw = cbInput.value.trim();
      location.href = "/documents" + (kw ? "?keyword=" + encodeURIComponent(kw) : "");
    });
  }
  /* 按「/」：优先页内搜索，否则顶条搜索（在输入框内输入时不劫持） */
  document.addEventListener("keydown", function (e) {
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
    var t = e.target;
    var tag = t && t.tagName ? t.tagName.toLowerCase() : "";
    if (tag === "input" || tag === "textarea" || tag === "select" || (t && t.isContentEditable)) {
      return;
    }
    if (localSearch && !localSearch.closest("[hidden]")) {
      e.preventDefault();
      localSearch.focus();
      return;
    }
    if (dashboardSearch && !dashboardSearch.closest("[hidden]")) {
      e.preventDefault();
      dashboardSearch.focus();
      return;
    }
    if (cbInput && cbSearch && !cbSearch.hidden) {
      e.preventDefault();
      cbInput.focus();
    }
  });

  /* ---- 修改密码 ---- */
  var changePwBtn = document.getElementById("btn-change-password");
  if (changePwBtn) {
    changePwBtn.addEventListener("click", function () {
      openChangePasswordModal();
    });
  }

  function openChangePasswordModal() {
    var overlay = ui.openModal(
      '<div class="form-group">' +
        '<label class="form-label">当前密码</label>' +
        '<div class="password-field"><input class="form-control" type="password" id="cp-old" autocomplete="current-password">' +
        '<button type="button" class="password-toggle" data-password-toggle="cp-old" aria-label="显示密码" aria-pressed="false">显示</button></div>' +
        '<p class="form-error">请输入当前密码</p>' +
      "</div>" +
      '<div class="form-group">' +
        '<label class="form-label">新密码</label>' +
        '<div class="password-field"><input class="form-control" type="password" id="cp-new" autocomplete="new-password">' +
        '<button type="button" class="password-toggle" data-password-toggle="cp-new" aria-label="显示密码" aria-pressed="false">显示</button></div>' +
        '<p class="form-error">至少 8 位，需包含字母和数字</p>' +
      "</div>" +
      '<div class="form-group">' +
        '<label class="form-label">确认新密码</label>' +
        '<div class="password-field"><input class="form-control" type="password" id="cp-confirm" autocomplete="new-password">' +
        '<button type="button" class="password-toggle" data-password-toggle="cp-confirm" aria-label="显示密码" aria-pressed="false">显示</button></div>' +
        '<p class="form-error">两次输入的新密码不一致</p>' +
      "</div>" +
      '<div id="cp-modal-error" class="alert alert-danger hidden"><span></span></div>' +
      '<div class="modal-footer">' +
        '<button type="button" class="btn btn-secondary" data-cancel>取消</button>' +
        '<button type="button" class="btn btn-primary" data-confirm>保存新密码</button>' +
      "</div>",
      { title: "修改密码", closable: true }
    );
    var oldPw = overlay.querySelector("#cp-old");
    var newPw = overlay.querySelector("#cp-new");
    var confirmPw = overlay.querySelector("#cp-confirm");
    var errBox = overlay.querySelector("#cp-modal-error");
    var saveBtn = overlay.querySelector("[data-confirm]");

    function fieldError(input, show) {
      var g = input.closest(".form-group");
      if (g) g.classList.toggle("has-error", !!show);
    }

    overlay.querySelector("[data-cancel]").addEventListener("click", function () {
      ui.closeModal(overlay);
    });

    saveBtn.addEventListener("click", async function () {
      fieldError(oldPw, !oldPw.value);
      fieldError(newPw, !newPw.value);
      fieldError(confirmPw, confirmPw.value !== newPw.value);
      if (!oldPw.value || !newPw.value || confirmPw.value !== newPw.value) return;
      ui.btnLoading(saveBtn, true, "保存中…");
      try {
        await api.post("/api/v1/auth/change-password", {
          old_password: oldPw.value,
          new_password: newPw.value,
        }, null, { noAuthRedirect: true });  // 原密码错误是业务拒绝，就地提示
        ui.closeModal(overlay);
        ui.toast("密码已修改", "success");
        if (currentUser && currentUser.must_change_password) {
          location.reload();  // 解除首登强制改密拦截
        }
      } catch (err) {
        errBox.querySelector("span").textContent =
          err && err.message ? err.message : "修改失败，请重试";
        errBox.classList.remove("hidden");
      } finally {
        ui.btnLoading(saveBtn, false);
      }
    });
  }

  /* ---- 退出登录 ---- */
  var logoutBtn = document.getElementById("btn-logout");
  if (logoutBtn) {
    logoutBtn.addEventListener("click", async function () {
      var btn = logoutBtn;
      ui.btnLoading(btn, true, "退出中…");
      try {
        await api.post("/api/v1/auth/logout", {});
        location.href = "/login";
      } catch (e) {
        ui.btnLoading(btn, false);
        ui.toast(e.message, "error");
      }
    });
  }
})();
