/**
 * notices.js —— 通知列表页（UI 改版阶段3e）
 * GET /api/v1/notices（tab: unread + type/keyword/date + 分页）
 *
 * 交互约定：
 * - tabs：全部 / 未读 N；tab 数字来自 dashboard 聚合
 * - 未读且我是对象的行：黄脊线 + 行尾「查看」（打开详情即置读）
 * - 类型/日期收进高级折叠；tab 与筛选同步 URL
 *
 * 2026-11-03：「确认」功能整体下线，只保留「已查看 / 未查看」。
 */
(function () {
  "use strict";

  var esc = ui.escapeHtml;
  var state = { page: 1, page_size: 15, tab: "" };
  // 身份只从 #user-data 读（勿用 DOM 反推，见 ui.js 注释）
  var isAdmin = ui.isAdmin();

  var keywordEl = document.getElementById("f-keyword");
  var typeEl = document.getElementById("f-type");
  var dateFromEl = document.getElementById("f-date-from");
  var dateToEl = document.getElementById("f-date-to");
  var listEl = document.getElementById("notice-list");
  var pager = document.getElementById("notice-pagination");
  var totalEl = document.getElementById("notice-total");
  var tabsEl = document.getElementById("notice-tabs");
  var tabBtns = tabsEl ? Array.prototype.slice.call(tabsEl.querySelectorAll(".filter-tab")) : [];
  var emptyEl = document.getElementById("notices-empty");
  var liveEl = document.getElementById("notice-result-live");
  var searchRow = document.querySelector(".search-row");
  var advFilter = document.getElementById("adv-filter");
  var totalRow = totalEl ? totalEl.parentElement : null;
  var filterSummaryEl = document.getElementById("notice-filter-summary");
  var selectedIds = new Set();
  var selectedTitles = {};
  var bulkBar = document.getElementById("notice-bulk-bar");
  var bulkCountEl = document.getElementById("notice-bulk-count");
  var bulkCheckAll = document.getElementById("notice-check-all");
  var bulkCancelBtn = document.getElementById("btn-notice-bulk-cancel");
  var bulkClearBtn = document.getElementById("btn-notice-bulk-clear");
  var bulkResultEl = document.getElementById("notice-bulk-result");

  function hasActiveFilters() {
    return !!(
      keywordEl.value.trim() ||
      state.tab ||
      typeEl.value ||
      state.urlType ||
      dateFromEl.value ||
      dateToEl.value
    );
  }

  function renderEmptyGuide() {
    if (!emptyEl) return;
    var art =
      '<div class="eg-art"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h6"/></svg></div>';
    emptyEl.className = "empty-guide" + (isAdmin ? "" : " is-simple");
    if (isAdmin) {
      emptyEl.innerHTML =
        art +
        '<div class="eg-copy"><p class="eg-kicker">通知中心 · 0 条通知</p>' +
        "<h2>还没有通知</h2>" +
        "<p>创建第一条通知，员工会在通知页看到它。系统会记录谁查看过；也可以关联资料一起创建。</p>" +
        '<div class="eg-actions"><a class="btn btn-primary" href="/admin/notices/new">＋ 新建通知</a>' +
        '<a class="text-muted" href="/documents">查看资料库 →</a></div></div>' +
        '<ol class="eg-steps"><li><span class="n">01</span><div><b>创建通知</b><small>纯文字或上传原始文件</small></div></li>' +
        '<li><span class="n">02</span><div><b>指定阅读对象</b><small>全员 / 部门 / 指定用户</small></div></li>' +
        '<li><span class="n">03</span><div><b>查看阅读统计</b><small>谁看过、谁还没看</small></div></li></ol>';
    } else {
      emptyEl.innerHTML =
        art +
        '<div class="eg-copy"><p class="eg-kicker">通知中心 · 0 条通知</p>' +
        "<h2>暂无通知</h2>" +
        "<p>通知会显示在这里；未查看的通知会在工作台提醒你。</p>" +
        '<div class="eg-actions"><a class="btn btn-secondary" href="/">返回工作台</a>' +
        '<a class="text-muted" href="/documents">查看资料 →</a></div></div>';
    }
  }

  function setLibraryEmpty(empty) {
    if (empty) renderEmptyGuide();
    if (emptyEl) emptyEl.hidden = !empty;
    [tabsEl, searchRow, advFilter, filterSummaryEl, listEl, totalRow, pager].forEach(function (el) {
      if (el) el.hidden = !!empty;
    });
  }

  /* ---------------- 管理员批量选择 ---------------- */
  function clearSelection() {
    selectedIds.clear();
    selectedTitles = {};
    syncBulkBar();
  }

  function syncBulkBar() {
    if (!isAdmin || !bulkBar) return;
    var boxes = listEl.querySelectorAll(".notice-check");
    var count = selectedIds.size;
    if (bulkCountEl) bulkCountEl.textContent = count;
    bulkBar.hidden = count === 0;
    if (bulkCheckAll) {
      bulkCheckAll.checked = boxes.length > 0 && count >= boxes.length;
      bulkCheckAll.indeterminate = count > 0 && count < boxes.length;
    }
  }

  function showBulkResult(result, actionLabel) {
    if (!bulkResultEl) return;
    var succeeded = result.succeeded || [];
    var skipped = result.skipped || [];
    var html = "<strong>批量" + esc(actionLabel) + "：成功 " + succeeded.length +
      " 条，跳过 " + skipped.length + " 条。</strong>";
    var rows = [];
    succeeded.forEach(function (row) {
      rows.push('<li>成功：' + esc(row.title || ("通知 #" + row.id)) +
        '（ID ' + esc(row.id) + "）</li>");
    });
    skipped.forEach(function (row) {
      var title = selectedTitles[row.id] || ("通知 #" + row.id);
      rows.push('<li>跳过：' + esc(title) + "（ID " + esc(row.id) + "）— " +
        esc(row.reason || "未说明原因") + "</li>");
    });
    if (rows.length) {
      html += '<ul style="margin:8px 0 0;padding-left:20px;">' + rows.join("") + "</ul>";
    }
    bulkResultEl.innerHTML = html;
    bulkResultEl.hidden = false;
  }

  /* ---------------- URL 同步 ---------------- */
  function syncUrl() {
    var q = new URLSearchParams();
    if (keywordEl.value.trim()) q.set("keyword", keywordEl.value.trim());
    if (state.tab) q.set("tab", state.tab);
    if (typeEl.value) q.set("type", typeEl.value);
    if (dateFromEl.value) q.set("date_from", dateFromEl.value);
    if (dateToEl.value) q.set("date_to", dateToEl.value);
    if (state.page > 1) q.set("page", state.page);
    var s = q.toString();
    history.replaceState(null, "", location.pathname + (s ? "?" + s : ""));
  }

  function initFromUrl() {
    var q = new URLSearchParams(location.search);
    if (q.get("keyword")) keywordEl.value = q.get("keyword");
    if (q.get("unread_only") === "1") state.tab = "unread";   // 兼容工作台旧链接
    if (q.get("tab")) state.tab = q.get("tab");
    if (q.get("type")) state.urlType = q.get("type");
    if (q.get("date_from")) dateFromEl.value = q.get("date_from");
    if (q.get("date_to")) dateToEl.value = q.get("date_to");
    if (q.get("page")) state.page = parseInt(q.get("page"), 10) || 1;
  }

  function paintTabs() {
    tabBtns.forEach(function (b) {
      b.classList.toggle("active", (b.dataset.tab || "") === state.tab);
    });
  }

  tabBtns.forEach(function (b) {
    b.addEventListener("click", function () {
      state.tab = this.dataset.tab || "";
      state.page = 1;
      paintTabs();
      load();
    });
  });

  /* ---------------- tab 数字（dashboard 聚合） ---------------- */
  async function loadTabCounts() {
    try {
      var dash = await api.get("/api/v1/dashboard");
      var set = function (id, v) {
        var el = document.getElementById(id);
        if (el) el.textContent = v !== undefined && v !== null ? " " + v : "";
      };
      set("tab-unread", dash.my_unread_notices);
    } catch (e) { /* 数字缺省不显示 */ }
  }

  /* ---------------- 通知类型 ---------------- */
  async function loadTypes() {
    try {
      var data = await api.get("/api/v1/notice-types");
      var html = '<option value="">全部类型</option>';
      (data.items || [])
        .filter(function (t) { return t.is_active !== false; })
        .forEach(function (t) {
          html += '<option value="' + esc(t.name) + '">' + esc(t.name) + "</option>";
        });
      html += '<option value="__unclassified__">未分类</option>';
      typeEl.innerHTML = html;
      if (state.urlType) typeEl.value = state.urlType;
    } catch (e) { /* 保持默认 */ }
  }

  function buildQuery() {
    var q = {
      page: state.page,
      page_size: state.page_size,
      keyword: keywordEl.value.trim(),
      type: typeEl.value,
    };
    if (state.tab === "unread") q.unread_only = true;
    if (dateFromEl.value) q.date_from = dateFromEl.value;
    if (dateToEl.value) q.date_to = dateToEl.value;
    return q;
  }

  function syncAdvCount() {
    var badge = document.getElementById("adv-count");
    if (!badge) return;
    var n = 0;
    if (typeEl.value) n++;
    if (dateFromEl.value) n++;
    if (dateToEl.value) n++;
    badge.textContent = n;
    badge.hidden = n === 0;
  }

  /* ---------------- 已选筛选摘要 ---------------- */
  function filterChip(key, label) {
    return (
      '<button type="button" class="filter-chip" data-filter="' + key + '">' +
      esc(label) + ' <span aria-hidden="true">×</span>' +
      '<span class="sr-only">，移除该筛选</span></button>'
    );
  }

  function renderFilterSummary() {
    if (!filterSummaryEl) return;
    var chips = [];
    var kw = keywordEl.value.trim();
    if (kw) chips.push(filterChip("keyword", "关键词：" + kw));
    var tabLabel = { unread: "未读" }[state.tab];
    if (tabLabel) chips.push(filterChip("tab", "视角：" + tabLabel));
    var typeVal = typeEl.value || state.urlType;
    if (typeVal) {
      var opt = typeEl.options[typeEl.selectedIndex];
      chips.push(filterChip("type", "类型：" + (opt && opt.value ? opt.text : typeVal)));
    }
    if (dateFromEl.value) chips.push(filterChip("date_from", "创建从：" + dateFromEl.value));
    if (dateToEl.value) chips.push(filterChip("date_to", "创建至：" + dateToEl.value));
    if (!chips.length) {
      filterSummaryEl.hidden = true;
      filterSummaryEl.innerHTML = "";
      return;
    }
    filterSummaryEl.hidden = false;
    filterSummaryEl.innerHTML = chips.join("") +
      '<button type="button" class="filter-clear" data-filter="all">清除全部</button>';
  }

  function removeFilter(key) {
    if (key === "keyword") keywordEl.value = "";
    else if (key === "tab") state.tab = "";
    else if (key === "type") {
      typeEl.value = "";
      state.urlType = "";
    } else if (key === "date_from") dateFromEl.value = "";
    else if (key === "date_to") dateToEl.value = "";
    state.page = 1;
    paintTabs();
    syncAdvCount();
    load();
  }

  if (filterSummaryEl) {
    filterSummaryEl.addEventListener("click", function (e) {
      var chip = e.target.closest("[data-filter]");
      if (!chip) return;
      if (chip.dataset.filter === "all") resetFilters();
      else removeFilter(chip.dataset.filter);
    });
  }

  /* ---------------- 管理操作 ---------------- */
  async function cancelNotice(id, title) {
    var ok = await ui.confirmModal({
      title: "删除通知",
      danger: true,
      message: "删除后「" + title + "」将移至回收站，可在此页面恢复。确定删除吗？",
      confirmText: "确认删除",
    });
    if (!ok) return false;
    try {
      await api.post("/api/v1/notices/" + id + "/cancel", {});
      ui.toast("通知已移至回收站", "success");
      return true;
    } catch (e) {
      ui.toast(e.message, "error");
      return false;
    }
  }

  function moreMenu(items) {
    if (!items.length) return "";
    return (
      '<details class="more-menu">' +
      '<summary aria-label="更多操作">⋯</summary>' +
      '<div class="more-menu-body">' + items.join("") + "</div></details>"
    );
  }

  function opsFor(n, pending, expiredPending) {
    var title = 'data-title="' + esc(n.title) + '"';
    var idAttr = 'data-id="' + n.id + '"';
    var view = '<a class="btn btn-sm btn-secondary" href="/notices/' + n.id + '">' +
      (pending ? "详情" : "查看") + "</a>";

    if (!isAdmin) {
      var userBtns = [];
      // 2026-11-03：确认功能下线，未读通知也只提供「查看」（打开即置读）
      userBtns.push('<a class="btn btn-sm btn-primary" href="/notices/' + n.id + '">查看</a>');
      return '<div class="notice-side">' + badgesFor(n, pending, expiredPending) + userBtns.join("") + "</div>";
    }

    // 管理员：主操作留在行内，低频动作收进「⋯」菜单
    // 2026-11-11：草稿状态下线后，列表只显示创建即发布的通知；
    // 已删除通知在回收站处理，不再显示发布状态徽章。
    var primary;
    var menuItems = [];
    if (pending) {
      primary = '<a class="btn btn-sm btn-primary" href="/notices/' + n.id + '">查看</a>';
      menuItems = [
        '<a class="more-item" href="/admin/notices/' + n.id + '/edit">编辑</a>',
        '<a class="more-item" href="/admin/notices/' + n.id + '/stats">阅读统计</a>',
        '<button type="button" class="more-item danger" data-action="cancel" ' + idAttr + " " + title + ">删除</button>",
      ];
    } else if (n.status === "published") {
      primary = '<a class="btn btn-sm btn-primary" href="/notices/' + n.id + '">查看</a>';
      menuItems = [
        '<a class="more-item" href="/admin/notices/' + n.id + '/edit">编辑</a>',
        '<a class="more-item" href="/admin/notices/' + n.id + '/stats">阅读统计</a>',
        '<button type="button" class="more-item danger" data-action="cancel" ' + idAttr + " " + title + ">删除</button>",
      ];
    } else {
      primary = '<a class="btn btn-sm btn-primary" href="/admin/notices/' + n.id + '/stats">阅读统计</a>';
    }
    return '<div class="notice-side">' + badgesFor(n, pending, expiredPending) + primary + moreMenu(menuItems) + "</div>";
  }
  function badgesFor(n, pending, expiredPending) {
    // 管理员不是阅读对象：列表只显示对象阅读进度，不显示个人「未查看」。
    if (isAdmin) return "";
    // 普通用户只显示一个「未查看/已查看」徽章；pending 已由行脊线和按钮表达。
    return ui.readStatusBadge(n.my_read_status || "unread");
  }

  /* 管理视角行内阅读概要：统一按「已查看/应阅读」口径（确认功能已下线） */
  function rateBar(n) {
    if (!isAdmin || n.status !== "published" || !n.read_stats || !n.read_stats.target) return "";
    var s = n.read_stats;
    var done = s.viewed || 0;
    var pct = Math.round((done * 100) / s.target);
    var cls = pct < 60 ? " rate-low" : pct >= 80 ? " rate-ok" : "";
    return (
      '<span class="rate-wrap" title="应阅读 ' + s.target + " 人，已查看 " + s.viewed + '">' +
      '<span class="rate-track"><span class="rate-fill' + cls + '" style="width:' + pct + '%"></span></span>' +
      '<span class="rate-text">查看 ' + done + "/" + s.target + "</span></span>"
    );
  }

  /* ---------------- 渲染 ---------------- */
  function render(items) {
    if (!items || !items.length) {
      listEl.innerHTML =
        '<div class="empty-state"><p>暂无符合条件的通知</p>' +
        '<button class="btn btn-secondary btn-sm" id="btn-clear-filters" type="button">清除筛选条件</button></div>';
      var clearBtn = document.getElementById("btn-clear-filters");
      if (clearBtn) clearBtn.addEventListener("click", resetFilters);
      return;
    }
    listEl.innerHTML = items
      .map(function (n) {
        // 2026-11-03：确认功能下线，「待处理」= published + 我是对象 + 我还没查看
        var expiredPending = false;
        var pending =
          n.status === "published" &&
          (n.my_read_status || "unread") === "unread" &&
          n.is_target !== false;
        // 2026-09-29 清理：原首项为 esc(ui.noticeTypeBadge ? "" : "")，恒为空串的
        // 无意义表达式（类型徽章由下方 noticeTypeBadge(n.notice_type) 渲染）。
        var timeInfo =
          esc(ui.formatDateTime(n.published_at)) +
          (n.effective_at ? " · 生效：" + esc(ui.formatDateTime(n.effective_at)) : "") +
          (n.deadline_at ? " · 截止：" + esc(ui.formatDateTime(n.deadline_at)) : "");
        var checkCell = isAdmin
          ? '<label style="display:flex;align-items:center;padding-right:4px;">' +
            '<input type="checkbox" class="notice-check" data-id="' + n.id + '"' +
            (selectedIds.has(n.id) ? " checked" : "") +
            ' aria-label="选择通知：' + esc(n.title) + '"></label>'
          : "";
        return (
          '<div class="notice-item' + (pending ? " needs-action" : "") + '" data-id="' + n.id + '">' +
          checkCell +
          '<div class="notice-main">' +
          '<div class="notice-title"><a href="/notices/' + n.id + '">' + esc(n.title) + "</a></div>" +
          '<div class="notice-sub">' +
          ui.noticeTypeBadge(n.notice_type) +
          (isAdmin ? ui.noticeStatusBadge(n.status) : "") +
          "<span>" + timeInfo + "</span>" +
          rateBar(n) +
          "</div></div>" +
          opsFor(n, pending, expiredPending) +
          "</div>"
        );
      })
      .join("");
    syncBulkBar();
  }

  async function load(focusAfter) {
    clearSelection();
    setLibraryEmpty(false);
    var skeleton = "";
    for (var i = 0; i < 4; i++) {
      skeleton += '<div class="skeleton skeleton-row"></div>';
    }
    listEl.innerHTML = '<div class="list-skeleton">' + skeleton + "</div>";
    listEl.setAttribute("aria-busy", "true");
    if (liveEl) liveEl.textContent = "正在加载通知…";
    syncUrl();
    syncAdvCount();
    renderFilterSummary();
    try {
      var data = await api.get("/api/v1/notices", buildQuery());
      var total = typeof data.total === "number" ? data.total : (data.items || []).length;
      var emptyLibrary = total === 0 && !hasActiveFilters();
      // 2026-11-04 产品决策：取消「1–2 条自动变大卡片」。
      // 大卡片在刚上线、只有几条通知时会占满整屏，与侧栏密度也割裂；
      // 统一用紧凑列表，条数少时只是页面略空，不会突兀。
      if (emptyLibrary) {
        setLibraryEmpty(true);
        listEl.innerHTML = "";
        pager.innerHTML = "";
        if (totalEl) totalEl.textContent = "";
        if (liveEl) liveEl.textContent = "暂无通知";
        loadTabCounts();
        return;
      }
      render(data.items);
      if (totalEl) totalEl.textContent = "共 " + total + " 条通知";
      if (liveEl) liveEl.textContent = "共 " + total + " 条通知";
      ui.renderPagination(pager, data.page, data.pages, function (p) {
        state.page = p;
        load(true);
      });
      if (focusAfter) {
        var heading = document.getElementById("notices-page-title");
        if (heading) heading.focus();
      }
    } catch (e) {
      setLibraryEmpty(false);
      pager.innerHTML = "";
      listEl.innerHTML =
        '<div class="list-error" role="alert"><p>加载通知失败：' + esc(e.message) + "</p>" +
        '<button class="btn btn-secondary btn-sm" type="button" id="btn-retry-notices">重试</button></div>';
      if (liveEl) liveEl.textContent = "通知加载失败";
      var retry = document.getElementById("btn-retry-notices");
      if (retry) retry.addEventListener("click", load);
    } finally {
      listEl.removeAttribute("aria-busy");
    }
    loadTabCounts();
  }

  /* ---------------- 事件 ---------------- */
  listEl.addEventListener("change", function (e) {
    var cb = e.target.closest(".notice-check");
    if (!cb) return;
    var id = parseInt(cb.dataset.id, 10);
    var row = cb.closest(".notice-item");
    var titleEl = row ? row.querySelector(".notice-title a") : null;
    if (cb.checked) {
      selectedIds.add(id);
      selectedTitles[id] = titleEl ? titleEl.textContent : ("通知 #" + id);
    } else {
      selectedIds.delete(id);
      delete selectedTitles[id];
    }
    syncBulkBar();
  });

  if (bulkCheckAll) {
    bulkCheckAll.addEventListener("change", function () {
      listEl.querySelectorAll(".notice-check").forEach(function (cb) {
        cb.checked = bulkCheckAll.checked;
        var id = parseInt(cb.dataset.id, 10);
        var row = cb.closest(".notice-item");
        var titleEl = row ? row.querySelector(".notice-title a") : null;
        if (bulkCheckAll.checked) {
          selectedIds.add(id);
          selectedTitles[id] = titleEl ? titleEl.textContent : ("通知 #" + id);
        } else {
          selectedIds.delete(id);
          delete selectedTitles[id];
        }
      });
      syncBulkBar();
    });
  }

  if (bulkClearBtn) {
    bulkClearBtn.addEventListener("click", function () {
      clearSelection();
      listEl.querySelectorAll(".notice-check").forEach(function (cb) { cb.checked = false; });
    });
  }

  if (bulkCancelBtn) {
    bulkCancelBtn.addEventListener("click", async function () {
      var ids = Array.from(selectedIds);
      if (!ids.length) {
        ui.toast("请先勾选要移入回收站的通知", "warning");
        return;
      }
      var reason = await ui.confirmModal({
        title: "批量移入回收站",
        danger: true,
        message: "将把已选的 " + ids.length + " 条通知移入回收站（软删除，可恢复）。确定吗？",
        inputLabel: "删除原因（可选）",
        inputPlaceholder: "例如：批量整理历史通知",
        confirmText: "确认移入",
      });
      if (reason === false) return;
      ui.btnLoading(bulkCancelBtn, true, "处理中…");
      try {
        var res = await api.post("/api/v1/notices/bulk-cancel", {
          notice_ids: ids,
          reason: reason || null,
        });
        showBulkResult(res, "移入回收站");
        var skipped = (res.skipped || []).length;
        ui.toast("成功 " + (res.succeeded || []).length + " 条，跳过 " + skipped + " 条", "success");
        clearSelection();
        await load();
      } catch (err) {
        ui.toast(err.message, "error");
      } finally {
        ui.btnLoading(bulkCancelBtn, false);
      }
    });
  }

  listEl.addEventListener("click", async function (e) {
    var btn = e.target.closest("[data-action]");
    if (!btn) return;
    e.stopPropagation();
    var id = parseInt(btn.dataset.id, 10);
    var title = btn.dataset.title || "";
    var needReload = false;
    ui.btnLoading(btn, true, "处理中…");
    // 2026-11-03：confirm-inline 分支随确认功能下线移除
    // 2026-11-04：publish / delete（草稿）分支随草稿状态下线移除
    if (btn.dataset.action === "cancel") needReload = await cancelNotice(id, title);
    ui.btnLoading(btn, false);
    if (needReload) load();
  });

  function resetFilters() {
    keywordEl.value = "";
    typeEl.value = "";
    dateFromEl.value = "";
    dateToEl.value = "";
    state.tab = "";
    state.urlType = "";
    state.page = 1;
    paintTabs();
    load();
  }

  document.getElementById("btn-search").addEventListener("click", function () {
    state.page = 1;
    load();
  });
  document.getElementById("btn-reset").addEventListener("click", resetFilters);
  keywordEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      state.page = 1;
      load();
    }
  });
  typeEl.addEventListener("change", function () {
    state.urlType = "";
    state.page = 1;
    load();
  });
  [dateFromEl, dateToEl].forEach(function (el) {
    el.addEventListener("change", function () {
      state.page = 1;
      load();
    });
  });

  initFromUrl();
  paintTabs();
  loadTypes().then(load);
})();
