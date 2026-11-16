/**
 * dashboard.js —— 工作台
 * 数据源：GET /api/v1/dashboard（聚合，含待办计数；admin 额外返回管理待办字段）
 * - 待办条幅：我的未读通知；零待办整条隐藏
 * - 最近资料 / 最新通知：分页接口取前 10
 */
(function () {
  "use strict";

  var esc = ui.escapeHtml;
  var isAdmin = ui.isAdmin();
  var dashData = null;
  var docsData = [];
  var noticesData = [];
  var docsLoaded = false;
  var noticesLoaded = false;
  var docsError = "";
  var noticesError = "";

  /* ---------------- 待办条幅 ---------------- */
  function renderTodoBanner(dash) {
    var banner = document.getElementById("todo-banner");
    if (!banner) return;
    var unread = dash.my_unread_notices || 0;
    if (!unread) {
      banner.hidden = true;
      return;
    }
    document.getElementById("todo-text").innerHTML =
      "你有 <strong class=\"num-warn\">" + unread + "</strong> 条通知尚未阅读";
    document.getElementById("todo-link").href = "/notices?unread_only=1";
    document.getElementById("todo-sub").textContent = "打开通知详情即视为已查看";
    banner.hidden = false;
  }

  /* ---------------- 时间差 ---------------- */
  function daysSince(iso) {
    if (!iso) return null;
    var then = new Date(iso).getTime();
    return Math.floor((Date.now() - then) / 86400000);
  }

  /* ---------------- 内容密度：0 / 1–2 / ≥3 ---------------- */
  function setVisible(id, visible) {
    var el = document.getElementById(id);
    if (el) el.hidden = !visible;
  }

  function syncRecentColumns() {
    var docsCard = document.getElementById("recent-docs-card");
    var noticesCard = document.getElementById("recent-notices-card");
    var cols = document.getElementById("recent-columns");
    if (!docsCard || !noticesCard || !cols) return;
    docsCard.hidden = docsData.length === 0;
    noticesCard.hidden = noticesData.filter(function (n) { return n.status === "published"; }).length === 0;
    cols.hidden = docsCard.hidden && noticesCard.hidden;
    cols.classList.toggle("is-single", docsCard.hidden !== noticesCard.hidden);
  }

  function applyDensity() {
    var main = document.getElementById("dashboard-main");
    var empty = document.getElementById("dashboard-empty");
    if (!dashData || !main || !empty) return;

    var total = (dashData.document_count || 0) + (dashData.notice_count || 0);
    main.hidden = total === 0;
    empty.hidden = total !== 0;
    if (total === 0) return;
    if (!docsLoaded || !noticesLoaded) return;

    // 2026-11-04 产品决策：取消「≤2 条切成焦点卡」的稀疏态。
    // 与通知页/资料页保持一致——永远用常规双列（最近资料 + 最新通知），
    // 搜索框也始终显示。条数少时只是列表短，不再整页换布局。
    setVisible("global-search", true);
    setVisible("search-hint", true);
    setVisible("recent-columns", true);
    syncRecentColumns();
  }

  async function loadDashboard() {
    try {
      var dash = await api.get("/api/v1/dashboard");
      dashData = dash;
      renderTodoBanner(dash);
      applyDensity();
    } catch (e) { /* 条幅/卡片保持隐藏，不打扰 */ }
  }

  /* ---------------- 全局搜索 ---------------- */
  var searchForm = document.getElementById("global-search");
  if (searchForm) {
    searchForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var kw = document.getElementById("gs-input").value.trim();
      location.href = "/documents" + (kw ? "?keyword=" + encodeURIComponent(kw) : "");
    });
  }

  /* ---------------- 最近资料 ---------------- */
  async function loadRecentDocuments() {
    var wrap = document.getElementById("recent-documents");
    if (!wrap) return;
    docsError = "";
    try {
      var data = await api.get("/api/v1/documents", { page: 1, page_size: 10 });
      var items = (data.items || []).filter(function (d) { return !d.archived; });
      docsData = items;
      docsLoaded = true;
      if (!items.length) {
        wrap.innerHTML = '<div class="empty-state" style="padding:24px 0;"><p>暂无资料</p></div>';
        applyDensity();
        return;
      }
      wrap.innerHTML =
        '<div class="table-wrap" style="border:none;box-shadow:none;">' +
        '<table class="table" style="min-width:0;">' +
        "<thead><tr><th>标题</th><th>状态</th></tr></thead><tbody>" +
        items
          .map(function (d) {
            var expiredCls = d.status === "expired" ? ' class="doc-expired"' : "";
            return (
              "<tr" + expiredCls + ' data-href="/documents/' + d.id + '" style="cursor:pointer;">' +
              "<td>" +
              '<div class="row-title">' + esc(d.title) + "</div>" +
              "</td>" +
              "<td>" + ui.statusBadge(d.status) + "</td>" +
              "</tr>"
            );
          })
          .join("") +
        "</tbody></table></div>";
      wrap.querySelectorAll("tr[data-href]").forEach(function (tr) {
        tr.addEventListener("click", function () {
          location.href = tr.dataset.href;
        });
      });
      applyDensity();
    } catch (e) {
      docsLoaded = true;
      docsError = e.message;
      wrap.innerHTML = '<div class="empty-state" style="padding:24px 0;"><p>' + esc(e.message) + "</p></div>";
      applyDensity();
    }
  }

  /* ---------------- 最新通知 ---------------- */
  function adminNoticeProgress(n) {
    var s = n.read_stats;
    if (!s || !s.target) return '<span class="text-muted">—</span>';
    return '<span class="row-sub">已查看 ' + s.viewed + " / 应阅读 " + s.target + "</span>";
  }

  async function loadRecentNotices() {
    var wrap = document.getElementById("recent-notices");
    if (!wrap) return;
    noticesError = "";
    try {
      var data = await api.get("/api/v1/notices", { page: 1, page_size: 10, unread_only: 0 });
      // 2026-11-04：草稿状态下线，工作台只展示 published 通知
      noticesData = (data.items || []).filter(function (n) {
        return n.status === "published";
      });
      noticesLoaded = true;
      var items = noticesData.filter(function (n) { return n.status === "published"; });
      if (!items.length) {
        wrap.innerHTML = '<div class="empty-state" style="padding:24px 0;"><p>暂无通知</p></div>';
        applyDensity();
        return;
      }
      wrap.innerHTML =
        '<div class="table-wrap" style="border:none;box-shadow:none;">' +
        '<table class="table" style="min-width:0;">' +
        "<thead><tr><th>标题</th><th>" + (isAdmin ? "查看进度" : "阅读状态") + "</th></tr></thead><tbody>" +
        items
          .map(function (n) {
            return (
              '<tr data-href="/notices/' + n.id + '" style="cursor:pointer;">' +
              "<td>" +
              '<div class="row-title">' + esc(n.title) + "</div>" +
              '<div class="row-sub">' +
              ui.noticeTypeBadge(n.notice_type) +
              "　" + esc(ui.formatDateTime(n.published_at)) +
              "</div>" +
              "</td>" +
              "<td>" + (isAdmin ? adminNoticeProgress(n) : ui.readStatusBadge(n.my_read_status || "unread")) + "</td>" +
              "</tr>"
            );
          })
          .join("") +
        "</tbody></table></div>";
      wrap.querySelectorAll("tr[data-href]").forEach(function (tr) {
        tr.addEventListener("click", function () {
          location.href = tr.dataset.href;
        });
      });
      applyDensity();
    } catch (e) {
      noticesLoaded = true;
      noticesError = e.message;
      wrap.innerHTML = '<div class="empty-state" style="padding:24px 0;"><p>' + esc(e.message) + "</p></div>";
      applyDensity();
    }
  }

  /* ---------------- 权限提示（2026-09-15） ----------------
   * 普通用户访问管理页时后端 302 到 /?denied=forbidden。此前是静默跳转，
   * 用户只看到"页面自己跳走了"，无法分辨是权限问题还是系统坏了。
   * 这里读一次查询参数给出说明，随后用 replaceState 清掉参数，
   * 避免刷新时重复弹（也不污染"返回"/书签）。
   */
  function maybeShowDeniedNotice() {
    var params = new URLSearchParams(location.search);
    if (params.get("denied") !== "forbidden") return;
    ui.toast("该页面仅管理员可访问，已返回工作台", "warning");
    params.delete("denied");
    var qs = params.toString();
    history.replaceState(null, "", location.pathname + (qs ? "?" + qs : ""));
  }

  maybeShowDeniedNotice();
  loadDashboard();
  loadRecentDocuments();
  loadRecentNotices();
})();
