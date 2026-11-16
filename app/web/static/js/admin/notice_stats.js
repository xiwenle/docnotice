/**
 * admin/notice_stats.js —— 通知阅读统计页
 *
 * 2026-11-03 重做（用户反馈「这个页面不是很直观」）：
 * - 顶部大数字本身就是筛选器：点「未查看」直接列出没看的人，
 *   不用先在下面的下拉里选一遍再滚到底部找名单。
 * - 名单区常驻搜索框：按姓名 / 账号 / 部门搜，几百人也找得到。
 * - 未查看的人排在最前（默认视图），查看时间列对未查看显示「—」。
 * - 确认功能已下线，只保留「已查看 / 未查看」单一维度。
 * - 管理员不计入统计（管理员能看全部通知是管理权限，不是被要求阅读）。
 *
 * 数据源：
 * - GET /api/v1/notices/{id}/statistics：应读/已查看/未查看 + 按部门
 * - GET /api/v1/notices/{id}/audience：名单（read_status + keyword + 分页）
 */
(function () {
  "use strict";

  var noticeId = window.NOTICE_ID;
  var esc = ui.escapeHtml;
  // 默认就落在「未查看」——管理员打开统计页，最想知道的就是谁还没看
  var state = {
    page: 1, page_size: 50, read_status: "unread", keyword: "",
    department_id: null, department_name: "",
  };
  var root = document.getElementById("stats-root");

  function pct(v) {
    return Math.round((v || 0) * 100) + "%";
  }

  function render(stats) {
    var total = stats.target_user_count || 0;
    var unviewed = stats.unviewed_count || 0;
    var viewed = stats.viewed_count || 0;

    root.innerHTML =
      '<div class="card">' +
      '<div class="card-header"><h3 class="card-title">阅读情况</h3>' +
      '<span class="text-muted" style="font-size:13px;">管理员不计入统计</span></div>' +
      '<div class="card-body">' +
      (total === 0
        ? '<div class="empty-state" style="padding:24px;"><p>该通知还没有应阅读人员</p>' +
          '<p class="text-muted" style="font-size:13px;">创建时会按阅读对象生成名单；' +
          '如果对象里只有管理员，名单会是空的。</p></div>'
        : '<div class="stats-overview">' +
          statNum("unviewed", "未查看", unviewed, "unread", true) +
          statNum("viewed", "已查看", viewed, "read") +
          statNum("target", "应阅读", total, "all") +
          '</div>' +
          '<div class="mt-3"><div class="row-sub mb-1">已查看率：' + pct(stats.viewed_rate) + "</div>" +
          '<div class="rate-bar"><div style="width:' + pct(stats.viewed_rate) +
          ';background:var(--info);"></div></div></div>' +
          (unviewed > 0
            ? '<p class="row-sub mt-3" style="color:var(--warning);">还有 <strong>' +
              unviewed + "</strong> 人未查看，名单已列在下方。</p>"
            : '<p class="row-sub mt-3" style="color:var(--success);">全部人员均已查看。</p>')
      ) +
      "</div></div>" +

      deptSection(stats, total) +
      audienceSection(total);
  }

  /* 三个大数字都可点击 → 切换下方名单筛选（点哪看哪，无需二次操作） */
  function statNum(kind, label, num, filter, highlight) {
    return (
      '<button type="button" class="stat-num-card ' + kind +
      (highlight && num > 0 ? " is-alert" : "") + '" data-filter="' + filter +
      '" aria-pressed="' + (state.read_status === filter ? "true" : "false") + '">' +
      '<div class="num">' + esc(num) + "</div>" +
      '<div class="label">' + esc(label) + "</div>" +
      "</button>"
    );
  }

  function deptSection(stats, total) {
    if (!total) return "";
    var rows = stats.by_department || [];
    if (!rows.length) return "";
    // 未查看人数降序：最需要催的部门排在最前
    rows = rows.slice().sort(function (a, b) {
      return (b.unviewed || 0) - (a.unviewed || 0);
    });
    return (
      '<div class="card mt-4"><div class="card-header"><h3 class="card-title">按部门</h3>' +
      '<span class="text-muted" style="font-size:12px;">按未查看人数排序</span></div>' +
      '<div class="table-wrap" style="border:none;border-radius:0;">' +
      '<table class="table"><thead><tr>' +
      '<th>部门</th><th>应读</th><th>已查看</th><th>未查看</th><th style="text-align:right;">名单</th>' +
      "</tr></thead><tbody>" +
      rows
        .map(function (r) {
          var name = r.name || "部门#" + r.department_id;
          return (
            '<tr data-department-filter="' + r.department_id + '" data-department-name="' +
            esc(name) + '" tabindex="0" role="button" aria-pressed="' +
            (state.department_id === r.department_id ? "true" : "false") +
            '" aria-label="查看' + esc(name) + '阅读名单" style="cursor:pointer;">' +
            "<td>" + esc(name) + "</td>" +
            "<td>" + esc(r.target) + "</td>" +
            "<td>" + esc(r.viewed) + "</td>" +
            '<td' + ((r.unviewed || 0) > 0 ? ' style="color:var(--warning);font-weight:600;"' : "") +
            ">" + esc(r.unviewed || 0) + "</td>" +
            '<td style="text-align:right;color:var(--primary);">筛选 →</td>' +
            "</tr>"
          );
        })
        .join("") +
      "</tbody></table></div></div>"
    );
  }

  function audienceSection(total) {
    if (!total) return "";
    return (
      '<div class="card mt-4">' +
      '<div class="card-header"><h3 class="card-title" id="audience-title">人员名单</h3>' +
      '<div class="flex gap-2" style="align-items:center;">' +
      '<button type="button" class="btn btn-ghost btn-sm" id="clear-dept-filter" hidden>查看全部部门</button>' +
      '<input class="form-control" type="search" id="audience-search" ' +
      'placeholder="搜索姓名 / 账号 / 部门" aria-label="搜索人员" style="max-width:220px;">' +
      '<select class="form-control" id="audience-filter" style="max-width:130px;" ' +
      'aria-label="按查看状态筛选">' +
      '<option value="unread">未查看</option>' +
      '<option value="read">已查看</option>' +
      '<option value="all">全部</option>' +
      "</select></div></div>" +
      '<div class="table-wrap" style="border:none;border-radius:0;">' +
      '<table class="table"><thead><tr>' +
      "<th>姓名</th><th>账号</th><th>部门</th><th>查看时间</th>" +
      '</tr></thead><tbody id="audience-tbody">' +
      '<tr><td colspan="4"><div class="table-empty">加载中…</div></td></tr>' +
      "</tbody></table></div>" +
      '<div class="card-footer"><div class="pagination" id="audience-pagination" ' +
      'style="margin-top:0;"></div></div>' +
      "</div>"
    );
  }

  async function loadAudience() {
    var tbody = document.getElementById("audience-tbody");
    var pager = document.getElementById("audience-pagination");
    var titleEl = document.getElementById("audience-title");
    if (!tbody) return;
    var label = { unread: "未查看", read: "已查看", all: "全部" }[state.read_status] || "";
    if (titleEl) {
      titleEl.textContent = "人员名单 · " + label +
        (state.department_name ? " · 部门「" + state.department_name + "」" : "") +
        (state.keyword ? " · 搜索「" + state.keyword + "」" : "");
    }
    var clearDept = document.getElementById("clear-dept-filter");
    if (clearDept) clearDept.hidden = state.department_id === null;
    tbody.innerHTML = '<tr><td colspan="4"><div class="table-empty">加载中…</div></td></tr>';
    try {
      var params = {
        read_status: state.read_status,
        page: state.page,
        page_size: state.page_size,
      };
      if (state.keyword) params.keyword = state.keyword;
      if (state.department_id !== null) params.department_id = state.department_id;
      var data = await api.get("/api/v1/notices/" + noticeId + "/audience", params);
      var items = data.items || [];
      if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="4"><div class="table-empty">' +
          (state.keyword ? "没有匹配「" + esc(state.keyword) + "」的人员"
                         : state.department_name ? "该部门没有符合条件的人员"
                         : "没有符合条件的人员") + "</div></td></tr>";
      } else {
        tbody.innerHTML = items
          .map(function (r) {
            var readAt = r.read_at
              ? esc(ui.formatDateTime(r.read_at))
              : '<span class="text-muted">未查看</span>';
            return (
              "<tr><td>" + esc(r.display_name || r.username) + "</td>" +
              "<td>" + esc(r.username) + "</td>" +
              "<td>" + ((r.department_names || []).map(function (n) {
                return '<span class="tag-chip">' + esc(n) + "</span>";
              }).join("") || "—") + "</td>" +
              "<td>" + readAt + "</td>" +
              "</tr>"
            );
          })
          .join("");
      }
      ui.renderPagination(pager, data.page, data.pages, function (p) {
        state.page = p;
        loadAudience();
      });
    } catch (e) {
      tbody.innerHTML =
        '<tr><td colspan="4"><div class="table-empty text-danger">' + esc(e.message) + "</div></td></tr>";
    }
  }

  function bindControls() {
    var filterEl = document.getElementById("audience-filter");
    if (filterEl) {
      filterEl.value = state.read_status;
      filterEl.addEventListener("change", function (e) {
        state.read_status = e.target.value;
        state.page = 1;
        syncStatCards();
        loadAudience();
      });
    }

    var clearDept = document.getElementById("clear-dept-filter");
    if (clearDept) {
      clearDept.addEventListener("click", function () {
        state.department_id = null;
        state.department_name = "";
        state.page = 1;
        syncDepartmentRows();
        loadAudience();
      });
    }

    var searchEl = document.getElementById("audience-search");
    if (searchEl) {
      var timer = null;
      searchEl.addEventListener("input", function (e) {
        clearTimeout(timer);
        timer = setTimeout(function () {
          state.keyword = e.target.value.trim();
          state.page = 1;
          loadAudience();
        }, 300);  // 防抖：边打边搜，但不每键打一次请求
      });
    }

    // 部门行 → 下钻该部门名单；大数字卡 → 切换查看状态
    root.addEventListener("click", function (e) {
      var deptRow = e.target.closest("[data-department-filter]");
      if (deptRow) {
        applyDepartmentFilter(deptRow.dataset.departmentFilter, deptRow.dataset.departmentName);
        return;
      }
      var card = e.target.closest("[data-filter]");
      if (!card) return;
      state.read_status = card.dataset.filter;
      state.page = 1;
      if (filterEl) filterEl.value = state.read_status;
      syncStatCards();
      loadAudience();
      scrollAudienceIntoView();
    });

    root.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" && e.key !== " ") return;
      var deptRow = e.target.closest("[data-department-filter]");
      if (!deptRow) return;
      e.preventDefault();
      applyDepartmentFilter(deptRow.dataset.departmentFilter, deptRow.dataset.departmentName);
    });
  }

  function scrollAudienceIntoView() {
    var box = document.getElementById("audience-tbody");
    if (box && box.scrollIntoView) {
      box.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  function applyDepartmentFilter(departmentId, departmentName) {
    state.department_id = parseInt(departmentId, 10);
    state.department_name = departmentName || ("部门#" + departmentId);
    state.page = 1;
    syncDepartmentRows();
    loadAudience();
    scrollAudienceIntoView();
  }

  function syncDepartmentRows() {
    root.querySelectorAll("[data-department-filter]").forEach(function (row) {
      var active = parseInt(row.dataset.departmentFilter, 10) === state.department_id;
      row.setAttribute("aria-pressed", active ? "true" : "false");
      row.style.background = active ? "var(--primary-lighter)" : "";
    });
  }

  function syncStatCards() {
    root.querySelectorAll("[data-filter]").forEach(function (el) {
      el.setAttribute("aria-pressed", el.dataset.filter === state.read_status ? "true" : "false");
      el.classList.toggle("is-active", el.dataset.filter === state.read_status);
    });
  }

  async function init() {
    try {
      var stats = await api.get("/api/v1/notices/" + noticeId + "/statistics");
      render(stats);
      bindControls();
      syncStatCards();
      syncDepartmentRows();
      loadAudience();
    } catch (e) {
      root.innerHTML =
        '<div class="card"><div class="card-body empty-state">' +
        '<p class="text-danger">' + esc(e.message) + "</p></div></div>";
    }
  }

  init();
})();
