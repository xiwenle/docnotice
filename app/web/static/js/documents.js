/**
 * documents.js —— 资料列表页
 * 布局：左侧分类导航（逐级下钻 / 展开全树）+ 右侧主区（搜索/高级折叠/tabs/表格）
 * 数据源：GET /api/v1/documents（category_id 筛选含全部子孙分类，后端已保证）
 *
 * 分类导航（2026-11-04 改版）：
 * - 默认「逐级下钻」：只渲染当前层的子分类，配面包屑；分类上千也不会滚不到底
 * - 可切换「展开全树」：只展开文件夹（分类），资料始终只在右侧列表出现
 * - 两种视图都支持跨层级分类搜索（显示父级路径 + 关键词高亮）
 *
 * 交互约定：
 * - 筛选状态同步 URL（可刷新/分享）：keyword / category_id / tag_id / status /
 *   uncategorized / untagged / cover_from / cover_to / page；
 *   管理员另含 audience_scope / sort / page_size
 * - 批量操作条：本页 ids / 当前筛选全部 filter 两种选择模式（仅管理员）
 * - 过期行灰化删除线；生效中且 ≤30 天到期显示"即将到期"徽章
 */
(function () {
  "use strict";

  var esc = ui.escapeHtml;
  var state = {
    page: 1, page_size: 15, status: "",
    uncategorized: false, untagged: false,
    category_id: null,
    nav_cat: null,               // 逐级下钻：当前所在层级
    cat_view: "drill",           // drill | tree
    tree_expanded: false,        // 全树视图：是否已「展开全部」
    tree_open: {},               // 全树视图：手动展开的文件夹 id
    cat_keyword: "",
    total: 0,
    visible_ids: [],
  };
  // 身份只从 #user-data 读。2026-09-14：此处曾用 #nav-admin-dropdown 探测，
  // 换肤移除顶栏后管理员被当成普通用户，资料列表不渲染选择列 → 行内列错位。
  var isAdmin = ui.isAdmin();
  var selectedIds = new Set();
  var selectionMode = "ids";     // ids | filter（当前筛选全部）
  var excludedIds = new Set();   // filter 模式下被逐条排除的 id
  var allCats = [];
  var catById = {};               // id -> 节点
  var catChildren = {};           // parentId -> [节点]
  var allTags = [];
  var me = ui.currentUser();
  var directoryCache = { loaded: false, loading: null, departments: [], users: [] };
  var batchModal = null;
  var batchPreview = null;

  var keywordEl = document.getElementById("f-keyword");
  var tagEl = document.getElementById("f-tag");
  var coverFromEl = document.getElementById("f-cover-from");
  var coverToEl = document.getElementById("f-cover-to");
  var audienceScopeEl = document.getElementById("f-audience-scope");
  var sortEl = document.getElementById("f-sort");
  var pageSizeEl = document.getElementById("f-page-size");
  var tabsEl = document.getElementById("doc-status-tabs");
  var tabBtns = tabsEl
    ? Array.prototype.slice.call(tabsEl.querySelectorAll(".filter-tab[data-status]"))
    : [];
  var organizeBtns = tabsEl
    ? Array.prototype.slice.call(tabsEl.querySelectorAll(".filter-tab[data-organize]"))
    : [];
  var tbody = document.getElementById("doc-tbody");
  var pager = document.getElementById("doc-pagination");
  var treeEl = document.getElementById("category-tree");
  var bulkBar = document.getElementById("doc-bulk-bar");
  var emptyEl = document.getElementById("docs-empty");
  var liveEl = document.getElementById("doc-result-live");
  var tableWrap = document.querySelector(".docs-main .table-wrap");
  var searchRow = document.querySelector(".docs-main .search-row");
  var advFilter = document.getElementById("adv-filter");
  var filterSummaryEl = document.getElementById("doc-filter-summary");
  var catSearchEl = document.getElementById("cat-search");
  var catSearchWrap = document.getElementById("cat-search-wrap");
  var treeToolbar = document.getElementById("tree-toolbar");
  var expandAllBtn = document.getElementById("btn-expand-all");
  var sidebarFooter = document.querySelector(".category-sidebar .sidebar-footer");
  var treeLiveEl = document.getElementById("cat-live");

  var CAT_VIEW_KEY = "zc_cat_view";

  function hasActiveFilters() {
    return !!(
      keywordEl.value.trim() ||
      state.status ||
      tagEl.value ||
      coverFromEl.value ||
      coverToEl.value ||
      state.uncategorized ||
      state.untagged ||
      state.category_id ||
      (audienceScopeEl && audienceScopeEl.value)
    );
  }

  function currentStatusFilter() {
    return state.status || undefined;
  }

  function currentSelectionFilters() {
    return {
      keyword: keywordEl.value.trim() || undefined,
      category_id: state.category_id || undefined,
      tag_id: tagEl.value ? parseInt(tagEl.value, 10) : undefined,
      status: currentStatusFilter(),
      cover_from: coverFromEl.value || undefined,
      cover_to: coverToEl.value || undefined,
      uncategorized: state.uncategorized || undefined,
      untagged: state.untagged || undefined,
      audience_scope: audienceScopeEl && audienceScopeEl.value || undefined,
    };
  }

  function documentStatusLabel(status) {
    if (status === "expiring") return "即将到期";
    return ui.statusLabel(status);
  }

  function renderEmptyGuide() {
    if (!emptyEl) return;
    var art =
      '<div class="eg-art"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M3 7l9-4 9 4v10l-9 4-9-4z"/><path d="M3 7l9 4 9-4M12 11v10"/></svg></div>';
    emptyEl.className = "empty-guide" + (isAdmin ? "" : " is-simple");
    if (isAdmin) {
      emptyEl.innerHTML =
        art +
        '<div class="eg-copy"><p class="eg-kicker">资料库 · 0 份资料</p>' +
        "<h2>资料库还是空的</h2>" +
        "<p>从上传第一份资料开始。设置分类和时效后，员工就能在资料页找到它；不需要一次填完所有字段。</p>" +
        '<div class="eg-actions"><a class="btn btn-primary" href="/admin/documents/new">＋ 新建资料</a>' +
        '<a class="text-muted" href="/admin/categories">先管理分类 →</a></div></div>' +
        '<ol class="eg-steps"><li><span class="n">01</span><div><b>上传文件</b><small>PDF / DOCX / XLSX / XLS / 图片</small></div></li>' +
        '<li><span class="n">02</span><div><b>设置分类与时效</b><small>员工按目录和有效期查找</small></div></li>' +
        '<li><span class="n">03</span><div><b>创建通知</b><small>创建后记录查看状态</small></div></li></ol>';
    } else {
      emptyEl.innerHTML =
        art +
        '<div class="eg-copy"><p class="eg-kicker">资料库 · 0 份资料</p>' +
        "<h2>暂无可见资料</h2>" +
        "<p>当前没有分配给你的资料。资料由管理员统一维护；如需访问，请联系管理员。</p>" +
        '<div class="eg-actions"><a class="btn btn-secondary" href="/">返回工作台</a></div></div>';
    }
  }

  function setLibraryEmpty(empty) {
    if (empty) renderEmptyGuide();
    if (emptyEl) emptyEl.hidden = !empty;
    [searchRow, advFilter, tabsEl, filterSummaryEl, tableWrap, pager].forEach(function (el) {
      if (el) el.hidden = !!empty;
    });
  }

  /* ---------------- URL 同步 ---------------- */
  function syncUrl() {
    var q = new URLSearchParams();
    if (keywordEl.value.trim()) q.set("keyword", keywordEl.value.trim());
    if (state.category_id) q.set("category_id", state.category_id);
    if (tagEl.value) q.set("tag_id", tagEl.value);
    if (state.status) q.set("status", state.status);
    if (state.uncategorized) q.set("uncategorized", "1");
    if (state.untagged) q.set("untagged", "1");
    if (coverFromEl.value) q.set("cover_from", coverFromEl.value);
    if (coverToEl.value) q.set("cover_to", coverToEl.value);
    if (audienceScopeEl && audienceScopeEl.value) q.set("audience_scope", audienceScopeEl.value);
    if (sortEl && sortEl.value !== "id_desc") q.set("sort", sortEl.value);
    if (pageSizeEl && state.page_size !== 15) q.set("page_size", String(state.page_size));
    if (state.page > 1) q.set("page", state.page);
    var s = q.toString();
    history.replaceState(null, "", location.pathname + (s ? "?" + s : ""));
  }

  function initFiltersFromUrl() {
    var q = new URLSearchParams(location.search);
    if (q.get("keyword")) keywordEl.value = q.get("keyword");
    if (q.get("status")) state.status = q.get("status");
    if (q.get("uncategorized")) state.uncategorized = true;
    if (q.get("untagged")) state.untagged = true;
    if (q.get("cover_from")) coverFromEl.value = q.get("cover_from");
    if (q.get("cover_to")) coverToEl.value = q.get("cover_to");
    if (audienceScopeEl && ["all", "department", "user"].indexOf(q.get("audience_scope")) >= 0) {
      audienceScopeEl.value = q.get("audience_scope");
    }
    var allowedSorts = [
      "id_desc", "created_desc", "updated_desc", "title_asc",
      "effective_from_desc", "effective_to_asc",
    ];
    if (sortEl && allowedSorts.indexOf(q.get("sort")) >= 0) sortEl.value = q.get("sort");
    var requestedPageSize = parseInt(q.get("page_size"), 10);
    if (pageSizeEl && [15, 30, 50, 100].indexOf(requestedPageSize) >= 0) {
      state.page_size = requestedPageSize;
      pageSizeEl.value = String(requestedPageSize);
    }
    if (q.get("page")) state.page = parseInt(q.get("page"), 10) || 1;
    state.urlCategory = q.get("category_id");
    state.urlTag = q.get("tag_id");
  }

  /* ---------------- 状态 tabs ---------------- */
  function paintTabs() {
    tabBtns.forEach(function (b) {
      b.classList.toggle("active", (b.dataset.status || "") === state.status);
    });
    organizeBtns.forEach(function (b) {
      b.classList.toggle("active", !!state[b.dataset.organize]);
    });
  }

  tabBtns.forEach(function (b) {
    b.addEventListener("click", function () {
      clearSelection();
      state.status = this.dataset.status || "";
      state.page = 1;
      paintTabs();
      load();
    });
  });
  organizeBtns.forEach(function (b) {
    b.addEventListener("click", function () {
      clearSelection();
      var key = this.dataset.organize;
      state[key] = !state[key];
      if (state[key]) {
        // 整理视角与分类筛选互斥
        state.category_id = null;
        state.nav_cat = null;
      }
      state.page = 1;
      paintTabs();
      renderNav();
      load();
    });
  });

  /* ---------------- 分类索引 ---------------- */
  function indexCategories(roots) {
    catById = {};
    catChildren = {};
    allCats = roots || [];

    (function walk(list, parent) {
      list.forEach(function (c) {
        if (c.is_active === false) return;
        c.parent_id = c.parent_id === undefined ? (parent ? parent.id : null) : c.parent_id;
        c.depth = parent ? parent.depth + 1 : 1;
        c.path_names = parent ? parent.path_names.concat([c.name]) : [c.name];
        catById[c.id] = c;
        var key = c.parent_id === null ? "root" : String(c.parent_id);
        (catChildren[key] = catChildren[key] || []).push(c);
        walk(c.children || [], c);
      });
    })(allCats, null);
  }

  function childrenOf(id) {
    return catChildren[id === null ? "root" : String(id)] || [];
  }

  function nodeOf(id) {
    return id === null || id === undefined ? null : catById[id] || null;
  }

  function pathNamesOf(id) {
    var n = nodeOf(id);
    return n ? n.path_names : [];
  }

  function parentPathText(n) {
    return n.path_names.slice(0, -1).join(" / ") || "一级分类";
  }

  /** 祖先链 id（不含自身），用于全树视图自动展开到当前选中项 */
  function ancestorIds(id) {
    var ids = [];
    var n = nodeOf(id);
    while (n && n.parent_id !== null) {
      ids.push(n.parent_id);
      n = nodeOf(n.parent_id);
    }
    return ids;
  }

  /* ---------------- 侧栏渲染 ---------------- */
  function highlight(text, q) {
    var safe = esc(text);
    if (!q) return safe;
    var idx = text.toLowerCase().indexOf(q.toLowerCase());
    if (idx < 0) return safe;
    return esc(text.slice(0, idx)) + "<mark>" + esc(text.slice(idx, idx + q.length)) +
      "</mark>" + esc(text.slice(idx + q.length));
  }

  function catRow(opts) {
    var cls = "tree-node-content";
    if (opts.active) cls += " active";
    var count = opts.count;
    var countCls = "tree-count" + (opts.countClass ? " " + opts.countClass : "") +
      (!count && count !== 0 ? "" : "");
    return (
      '<button type="button" class="' + cls + '" data-cat="' + opts.key + '"' +
      (opts.tree ? ' data-tree-row="1"' : "") + ">" +
      (opts.icon || '<span class="tree-toggle leaf"></span>') +
      '<span class="tree-label">' + opts.label +
      (opts.sub ? '<span class="tree-sub">' + opts.sub + "</span>" : "") + "</span>" +
      '<span class="' + countCls + '">' + count + "</span>" +
      (opts.chevron ? '<span class="tree-chevron" aria-hidden="true">›</span>' : "") +
      "</button>"
    );
  }

  function iconSvg(paths) {
    return '<svg class="tree-row-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" ' +
      'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      paths + "</svg>";
  }

  var ICON_HOME = iconSvg('<path d="M2.5 7L8 2.5 13.5 7v6a.5.5 0 0 1-.5.5h-3v-4H6v4H3a.5.5 0 0 1-.5-.5z"/>');
  var ICON_WARN = iconSvg('<circle cx="8" cy="8" r="5.5"/><path d="M8 5.5v3.2M8 10.6v.4"/>');

  function crumbsHtml(navId) {
    if (navId === null) return "";
    var names = pathNamesOf(navId);
    var html = '<div class="cat-crumbs">' +
      '<button type="button" class="cat-crumb" data-crumb="__root__">全部资料</button>';
    names.forEach(function (name, i) {
      // 逐级找对应的 id
      var target = null;
      var cur = nodeOf(navId);
      var chain = [cur];
      while (cur && cur.parent_id !== null) { cur = nodeOf(cur.parent_id); chain.unshift(cur); }
      target = chain[i] ? chain[i].id : null;
      var isLast = i === names.length - 1;
      html += '<span class="cat-sep">/</span>';
      html += isLast
        ? '<span class="cat-crumb cur" aria-current="page">' + esc(name) + "</span>"
        : '<button type="button" class="cat-crumb" data-crumb="' + target + '">' + esc(name) + "</button>";
    });
    return html + "</div>";
  }

  function pinnedRows() {
    var html =
      '<div class="cat-pinned">' +
      catRow({
        key: "__all__", label: "全部资料", icon: ICON_HOME,
        count: state.all_count !== undefined ? state.all_count : "—",
        active: !state.category_id && !state.uncategorized,
      }) +
      catRow({
        key: "__uncategorized__", label: "未分类", icon: ICON_WARN,
        count: state.uncategorized_count !== undefined ? state.uncategorized_count : "—",
        active: state.uncategorized,
        countClass: state.uncategorized ? "" : "",
      }) +
      "</div>";
    return html;
  }

  function searchRows(q) {
    var hits = [];
    Object.keys(catById).forEach(function (id) {
      var c = catById[id];
      if (c.name.toLowerCase().indexOf(q.toLowerCase()) >= 0) hits.push(c);
    });
    var shown = hits.slice(0, 80);
    var html = '<div class="cat-section"><span>找到 ' + hits.length + " 个分类</span>" +
      (hits.length > shown.length ? "<span>仅显示前 80</span>" : "") + "</div>";
    if (!hits.length) {
      html += '<div class="cat-empty"><b>没有匹配「' + esc(q) + '」的分类</b>' +
        "换个关键词试试，或清空搜索继续浏览</div>";
      return html;
    }
    shown.forEach(function (c) {
      html += catRow({
        key: c.id,
        label: highlight(c.name, q),
        sub: esc(parentPathText(c)),
        count: c.document_count !== undefined ? c.document_count : "",
        active: state.category_id === c.id,
        chevron: childrenOf(c.id).length > 0,
        countClass: "",
      });
    });
    return html;
  }

  /* 逐级下钻 */
  function renderDrill() {
    var q = state.cat_keyword.trim();
    if (q) return searchRows(q);

    var html = pinnedRows();
    var navId = state.nav_cat;

    if (navId === null) {
      var roots = childrenOf(null);
      html += '<div class="cat-section"><span>一级分类</span>' +
        '<span class="cat-section-meta">' + roots.length + " 个</span></div>";
      roots.forEach(function (c) {
        html += catRow({
          key: c.id,
          label: esc(c.name),
          count: c.document_count !== undefined ? c.document_count : "",
          active: state.category_id === c.id,
          chevron: childrenOf(c.id).length > 0,
        });
      });
      if (!roots.length) {
        html += '<div class="cat-empty"><b>还没有分类</b>管理员可在「分类管理」中建立</div>';
      }
      return html;
    }

    var cur = nodeOf(navId);
    if (!cur) { state.nav_cat = null; return renderDrill(); }

    html += crumbsHtml(navId);
    var backKey = cur.parent_id === null ? "__root__" : cur.parent_id;
    html += '<button type="button" class="cat-back" data-crumb="' + backKey + '">' +
      '‹ 返回上级<span class="cat-back-kbd"><kbd>Backspace</kbd></span></button>';

    var kids = childrenOf(navId);
    html += '<div class="cat-section"><span>子分类</span>' +
      '<span class="cat-section-meta">' + kids.length + " 个 · 共 " +
      (cur.document_count !== undefined ? cur.document_count : 0) + " 份资料</span></div>";
    if (!kids.length) {
      html += '<div class="cat-empty"><b>这一层没有子分类</b>右侧显示本分类及下级的全部资料</div>';
    }
    kids.forEach(function (c) {
      html += catRow({
        key: c.id,
        label: esc(c.name),
        count: c.document_count !== undefined ? c.document_count : "",
        active: state.category_id === c.id,
        chevron: childrenOf(c.id).length > 0,
      });
    });
    return html;
  }

  /* 展开全树：只展开文件夹，资料不进树 */
  function renderTree() {
    var q = state.cat_keyword.trim();
    var html = pinnedRows();
    if (q) return html + searchRows(q);

    var openPath = state.category_id ? ancestorIds(state.category_id) : [];

    function node(c) {
      var kids = childrenOf(c.id);
      var has = kids.length > 0;
      var open = has && (
        state.tree_expanded ||
        !!state.tree_open[c.id] ||
        openPath.indexOf(c.id) >= 0
      );
      var out =
        '<div class="tree-node">' +
        '<button type="button" class="tree-node-content" data-cat="' + c.id + '" data-tree-row="1">' +
        (has
          ? '<span class="tree-toggle' + (open ? " expanded" : "") + '" data-toggle="' + c.id +
            '" role="button" tabindex="-1" aria-label="' + (open ? "收起" : "展开") + esc(c.name) + '">▸</span>'
          : '<span class="tree-toggle leaf"></span>') +
        '<span class="tree-label">' + esc(c.name) + "</span>" +
        '<span class="tree-count">' + (c.document_count !== undefined ? c.document_count : "") + "</span>" +
        "</button>";
      // 收起的分支不渲染子节点：分类上千时首屏依然轻
      if (open) {
        out += '<div class="tree-children">' + kids.map(node).join("") + "</div>";
      }
      return out + "</div>";
    }

    childrenOf(null).forEach(function (c) { html += node(c); });
    return html;
  }

  function renderNav(focusAfter) {
    if (!treeEl) return;
    if (state.cat_view === "tree") {
      treeEl.innerHTML = renderTree();
    } else {
      treeEl.innerHTML = renderDrill();
    }
    if (expandAllBtn) expandAllBtn.textContent = state.tree_expanded ? "折叠全部 ▴" : "展开全部 ▾";
    if (treeToolbar) treeToolbar.hidden = state.cat_view !== "tree";
    if (catSearchWrap) catSearchWrap.classList.toggle("has-value", !!state.cat_keyword);

    // 逐级下钻用 roving tabindex；全树保持全部可 Tab
    var rows = treeEl.querySelectorAll(".tree-node-content[data-cat]");
    if (state.cat_view === "drill") {
      rows.forEach(function (r, i) { r.tabIndex = i === 0 ? 0 : -1; });
    } else {
      rows.forEach(function (r) { r.tabIndex = 0; });
    }
    if (focusAfter) {
      var first = treeEl.querySelector(".tree-node-content[data-cat]");
      if (first) first.focus();
    }
    paintSidebarFooter();
    announceNav();
  }

  function paintSidebarFooter() {
    if (!sidebarFooter) return;
    var catsEl = document.getElementById("footer-cats");
    var docsEl = document.getElementById("footer-docs");
    if (catsEl) catsEl.textContent = Object.keys(catById).length + " 个分类";
    if (docsEl) {
      docsEl.textContent = (state.all_count !== undefined ? state.all_count : "—") + " 份资料";
    }
  }

  function announceNav() {
    if (!treeLiveEl) return;
    if (state.cat_keyword.trim()) {
      treeLiveEl.textContent = "搜索分类：" + state.cat_keyword.trim();
      return;
    }
    if (state.cat_view === "tree") {
      treeLiveEl.textContent = "展开全树视图";
      return;
    }
    if (state.nav_cat === null) {
      treeLiveEl.textContent = "当前位置：全部资料";
    } else {
      var n = nodeOf(state.nav_cat);
      treeLiveEl.textContent = "已进入 " + n.path_names.join("，");
    }
  }

  /* 全部资料 / 未分类 计数（dashboard + 未分类 total） */
  function refreshSidebarCounts() {
    api.get("/api/v1/dashboard").then(function (d) {
      state.all_count = d.document_count;
      paintSidebarFooter();
      renderNav();
    }).catch(function () {});

    api.get("/api/v1/documents", { uncategorized: 1, page_size: 1 }).then(function (d) {
      state.uncategorized_count = d.total;
      renderNav();
    }).catch(function () {});
  }

  /* ---------------- 筛选选项加载 ---------------- */
  async function loadFilters() {
    var cats = [];
    var tags = [];
    await Promise.all([
      api.get("/api/v1/categories", { with_document_count: 1 }).then(function (d) {
        cats = d.items || [];
      }).catch(function () {}),
      api.get("/api/v1/tags", { with_count: 1 }).then(function (d) {
        tags = d.items || [];
      }).catch(function () {}),
    ]);
    indexCategories(cats);
    allTags = tags;

    var tagHtml = '<option value="">全部标签</option>';
    tags.forEach(function (t) {
      tagHtml += '<option value="' + t.id + '">' + esc(t.name) +
        (t.document_count !== undefined ? "（" + t.document_count + "）" : "") + "</option>";
    });
    tagEl.innerHTML = tagHtml;

    if (state.urlTag) tagEl.value = state.urlTag;
    if (state.urlCategory) {
      var cid = parseInt(state.urlCategory, 10);
      var node = nodeOf(cid);
      if (node) {
        state.category_id = cid;
        state.nav_cat = cid;
      }
    }

    paintTabs();
    renderNav();
    refreshSidebarCounts();
  }

  /* ---------------- 分类导航交互 ---------------- */
  function selectCategory(id) {
    clearSelection();
    state.category_id = id;
    state.uncategorized = false;
    state.untagged = false;
    state.nav_cat = id;
    state.page = 1;
    if (state.cat_keyword) {
      state.cat_keyword = "";
      if (catSearchEl) catSearchEl.value = "";
    }
    paintTabs();
    renderNav();
    load();
  }

  function goAll() {
    clearSelection();
    state.category_id = null;
    state.uncategorized = false;
    state.nav_cat = null;
    state.page = 1;
    paintTabs();
    renderNav();
    load();
  }

  function goUp() {
    var cur = nodeOf(state.nav_cat);
    if (!cur) { goAll(); return; }
    if (cur.parent_id === null) { goAll(); return; }
    // 返回上级同时也是右侧资料的筛选范围变化，不能只重绘左侧目录。
    selectCategory(cur.parent_id);
  }

  if (treeEl) {
    treeEl.addEventListener("click", function (e) {
      var toggle = e.target.closest("[data-toggle]");
      if (toggle) {
        e.preventDefault();
        e.stopPropagation();
        var tid = parseInt(toggle.getAttribute("data-toggle"), 10);
        if (state.tree_open[tid]) delete state.tree_open[tid];
        else state.tree_open[tid] = true;
        renderNav();
        return;
      }

      var crumb = e.target.closest("[data-crumb]");
      if (crumb) {
        e.preventDefault();
        var val = crumb.getAttribute("data-crumb");
        var target = val === "__root__" ? null : parseInt(val, 10);
        // 面包屑跳级后同步刷新右侧资料，避免“目录回去了、列表还停在原分类”。
        if (target === null) goAll();
        else selectCategory(target);
        return;
      }

      var row = e.target.closest("[data-cat]");
      if (!row) return;
      var key = row.getAttribute("data-cat");
      if (key === "__all__") { goAll(); return; }
      if (key === "__uncategorized__") {
        clearSelection();
        state.category_id = null;
        state.nav_cat = null;
        state.uncategorized = !state.uncategorized;
        state.page = 1;
        if (state.cat_keyword) {
          state.cat_keyword = "";
          if (catSearchEl) catSearchEl.value = "";
        }
        paintTabs();
        renderNav();
          load();
        return;
      }
      selectCategory(parseInt(key, 10));
    });

    treeEl.addEventListener("keydown", function (e) {
      var rows = Array.prototype.slice.call(
        treeEl.querySelectorAll(".tree-node-content[data-cat]")
      );
      if (!rows.length) return;
      var current = document.activeElement;
      var idx = rows.indexOf(current);

      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        var next = e.key === "ArrowDown"
          ? Math.min(rows.length - 1, idx < 0 ? 0 : idx + 1)
          : Math.max(0, idx < 0 ? 0 : idx - 1);
        rows.forEach(function (r) { r.tabIndex = -1; });
        rows[next].tabIndex = 0;
        rows[next].focus();
        return;
      }
      if (e.key === "Enter" || e.key === " ") {
        if (!current || !current.classList.contains("tree-node-content")) return;
        e.preventDefault();
        current.click();
        return;
      }
      if (e.key === "Backspace" && state.cat_view === "drill" && !state.cat_keyword) {
        e.preventDefault();
        goUp();
      }
    });
  }

  var viewDrillBtn = document.getElementById("btn-view-drill");
  var viewTreeBtn = document.getElementById("btn-view-tree");

  function setCatView(view) {
    state.cat_view = view;
    if (view === "tree") {
      state.tree_expanded = false;
      state.tree_open = {};
    }
    if (viewDrillBtn) {
      viewDrillBtn.classList.toggle("on", view === "drill");
      viewDrillBtn.setAttribute("aria-pressed", view === "drill");
    }
    if (viewTreeBtn) {
      viewTreeBtn.classList.toggle("on", view === "tree");
      viewTreeBtn.setAttribute("aria-pressed", view === "tree");
    }
    try { localStorage.setItem(CAT_VIEW_KEY, view); } catch (err) { /* 隐私模式静默 */ }
    renderNav();
  }

  if (viewDrillBtn) viewDrillBtn.addEventListener("click", function () { setCatView("drill"); });
  if (viewTreeBtn) viewTreeBtn.addEventListener("click", function () { setCatView("tree"); });
  if (expandAllBtn) {
    expandAllBtn.addEventListener("click", function () {
      state.tree_expanded = !state.tree_expanded;
      state.tree_open = {};
      renderNav();
    });
  }

  // 侧栏分类搜索（140ms 防抖）
  var catSearchTimer = null;
  if (catSearchEl) {
    catSearchEl.addEventListener("input", function () {
      var v = this.value;
      if (catSearchWrap) catSearchWrap.classList.toggle("has-value", !!v);
      clearTimeout(catSearchTimer);
      catSearchTimer = setTimeout(function () {
        state.cat_keyword = v;
        renderNav();
      }, 140);
    });
  }
  var catSearchClear = document.getElementById("cat-search-clear");
  if (catSearchClear) {
    catSearchClear.addEventListener("click", function () {
      state.cat_keyword = "";
      if (catSearchEl) { catSearchEl.value = ""; catSearchEl.focus(); }
      renderNav();
    });
  }

  // 全局快捷键：/ 聚焦分类搜索；Esc 清空
  document.addEventListener("keydown", function (e) {
    var tag = (e.target.tagName || "").toLowerCase();
    var typing = tag === "input" || tag === "textarea" || e.target.isContentEditable;
    if (e.key === "/" && !typing && catSearchEl) {
      e.preventDefault();
      catSearchEl.focus();
      return;
    }
    if (e.key === "Escape" && state.cat_keyword) {
      state.cat_keyword = "";
      if (catSearchEl) catSearchEl.value = "";
      renderNav();
    }
  });

  /* ---------------- 已选筛选摘要 ---------------- */
  function filterChip(key, label) {
    return (
      '<button type="button" class="filter-chip" data-filter="' + key + '">' +
      esc(label) + ' <span aria-hidden="true">×</span>' +
      '<span class="sr-only">，移除该筛选</span></button>'
    );
  }

  function optionText(el, fallback) {
    if (!el) return fallback || "";
    var opt = el.options[el.selectedIndex];
    return opt && opt.text ? opt.text : (fallback || el.value);
  }

  function renderFilterSummary() {
    if (!filterSummaryEl) return;
    var chips = [];
    var kw = keywordEl.value.trim();
    if (kw) chips.push(filterChip("keyword", "关键词：" + kw));
    if (state.category_id) {
      var path = pathNamesOf(state.category_id);
      chips.push(filterChip("category", "分类：" +
        (path.length ? path.join(" / ") : state.category_id)));
    }
    if (state.uncategorized) chips.push(filterChip("uncategorized", "未分类"));
    if (state.untagged) chips.push(filterChip("untagged", "未打标签"));
    if (state.status) {
      chips.push(filterChip("status", "状态：" + documentStatusLabel(state.status)));
    }
    if (tagEl.value) {
      chips.push(filterChip("tag", "标签：" + optionText(tagEl, tagEl.value)));
    }
    if (coverFromEl.value || coverToEl.value) {
      chips.push(filterChip("cover", "有效期覆盖：" +
        (coverFromEl.value || "…") + " ~ " + (coverToEl.value || "…")));
    }
    if (audienceScopeEl && audienceScopeEl.value) {
      chips.push(filterChip("audience", "可见范围：" + optionText(audienceScopeEl)));
    }
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
    clearSelection();
    if (key === "keyword") keywordEl.value = "";
    else if (key === "category") { state.category_id = null; state.nav_cat = null; }
    else if (key === "uncategorized") state.uncategorized = false;
    else if (key === "untagged") state.untagged = false;
    else if (key === "status") state.status = "";
    else if (key === "tag") tagEl.value = "";
    else if (key === "cover") {
      coverFromEl.value = "";
      coverToEl.value = "";
    }
    else if (key === "audience" && audienceScopeEl) audienceScopeEl.value = "";
    state.page = 1;
    paintTabs();
    renderNav();
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

  /* ---------------- 高级筛选计数 ---------------- */
  function syncAdvCount() {
    var badge = document.getElementById("adv-count");
    if (!badge) return;
    var n = 0;
    if (tagEl.value) n++;
    if (coverFromEl.value || coverToEl.value) n++;
    if (audienceScopeEl && audienceScopeEl.value) n++;
    badge.textContent = n;
    badge.hidden = n === 0;
  }

  /* ---------------- 列表渲染 ---------------- */
  function soonBadge(d) {
    if (!d.effective_to || d.status !== "active") return "";
    var days = Math.ceil((new Date(d.effective_to + "T23:59:59") - Date.now()) / 86400000);
    if (days < 0 || days > 30) return "";
    return ' <span class="badge badge-soon">' + (days === 0 ? "今天到期" : days + " 天后到期") + "</span>";
  }

  function render(items) {
    state.visible_ids = (items || []).map(function (d) { return d.id; });
    if (!items || !items.length) {
      // colspan 必须跟实际列数走：普通用户是 5 列（无选择列），写死 6 会让
      // 浏览器多分配一列，整表被压缩、表头文字挤向左侧（2026-11-04 修）。
      tbody.innerHTML =
        '<tr><td colspan="' + (isAdmin ? 6 : 5) + '"><div class="table-empty">' +
        "<p>没有符合条件的资料</p>" +
        '<button class="btn btn-secondary btn-sm" id="btn-clear-filters" type="button">清除筛选条件</button>' +
        "</div></td></tr>";
      var clearBtn = document.getElementById("btn-clear-filters");
      if (clearBtn) clearBtn.addEventListener("click", resetFilters);
      syncBulkBar();
      return;
    }
    tbody.innerHTML = items
      .map(function (d) {
        var dateRange = d.effective_from + (d.effective_to ? " ~ " + d.effective_to : " ~ 暂无失效日期");
        var sub = [];
        var path = (d.category_path || []).join(" / ");
        sub.push(path ? esc(path) : "未分类");
        var metaBadges =
          '<span class="doc-meta-badge doc-meta-audience" title="资料可见范围">' +
            esc(d.audience_label || "全体可见") + "</span>" +
          (d.has_attachment
            ? '<span class="doc-meta-badge doc-meta-attachment" title="有附件；下载时默认获取附件">附件</span>'
            : "");
        var chips = (d.tags || []).slice(0, 2).map(function (t) {
          return '<span class="tag-chip" data-tag-id="' + t.id + '" title="按该标签筛选">' + esc(t.name) + "</span>";
        });
        var extra = (d.tags || []).length > 2 ? '<span class="text-muted" style="font-size:12px;">+' + ((d.tags || []).length - 2) + "</span>" : "";
        // 2026-11-04：allow_download 开关已移除，能查看即可下载，不再有「仅预览」徽标
        var fileInfo = d.has_file
          ? ui.fileTypeBadge(d.file_type)
          : '<span class="text-muted">暂无文件</span>';
        var ops =
          '<a class="btn btn-sm btn-primary" href="/documents/' + d.id + '">查看</a>' +
          (isAdmin
            ? '<details class="more-menu" data-id="' + d.id + '">' +
              '<summary aria-label="更多操作">⋯</summary>' +
              '<div class="more-menu-body">' +
              '<a class="more-item" href="/admin/documents/' + d.id + '/edit">编辑</a>' +
              '<button type="button" class="more-item danger" data-action="archive" data-id="' + d.id + '" data-title="' + esc(d.title) + '">删除</button>' +
              "</div></details>"
            : "");
        // 非 admin 不渲染选择列（表头同条件整体不渲染，保持列数一致不错位）
        var isSelected = selectionMode === "ids"
          ? selectedIds.has(d.id)
          : !excludedIds.has(d.id);
        var checkCell = isAdmin
          ? '<td class="cell-check"><input type="checkbox" class="doc-check" data-id="' + d.id + '"' +
            (isSelected ? " checked" : "") + ' aria-label="选择该资料"></td>'
          : "";
        return (
          '<tr class="doc-row' + (d.status === "expired" ? " doc-expired" : "") + '">' +
          checkCell +
          '<td class="cell-title"><a class="doc-title-link" href="/documents/' + d.id + '">' + esc(d.title) + "</a>" +
          '<div class="row-sub"><span class="doc-meta-line">' + metaBadges + "</span>" +
          '<span class="doc-row-path">' + sub.join(" · ") + "</span> " + chips.join("") + extra + "</div></td>" +
          '<td class="doc-range" data-label="生效区间" title="' + esc(dateRange) + '">' + esc(dateRange) + "</td>" +
          '<td data-label="状态">' + ui.statusBadge(d.status) + soonBadge(d) + "</td>" +
          '<td data-label="文件">' + fileInfo + "</td>" +
          '<td class="row-actions" data-label="操作">' + ops + "</td>" +
          "</tr>"
        );
      })
      .join("");
    syncBulkBar();
  }

  /* ---------------- 批量选择与批量操作（管理员） ---------------- */
  function clearSelection() {
    selectedIds.clear();
    excludedIds.clear();
    selectionMode = "ids";
    batchPreview = null;
    syncBulkBar();
  }

  function currentPageSelectedCount() {
    return state.visible_ids.filter(function (id) {
      return selectionMode === "filter" ? !excludedIds.has(id) : selectedIds.has(id);
    }).length;
  }

  function totalSelectedCount() {
    if (selectionMode === "filter") {
      return Math.max(0, state.total - excludedIds.size);
    }
    return selectedIds.size;
  }

  function syncBulkBar() {
    if (!bulkBar) return;
    var pageCount = currentPageSelectedCount();
    var selectedCount = totalSelectedCount();
    if (selectionMode === "filter" && selectedCount === 0) {
      selectedIds.clear();
      excludedIds.clear();
      selectionMode = "ids";
      pageCount = 0;
    }

    var num = document.getElementById("bulk-count-num");
    var pageCountEl = document.getElementById("bulk-page-count");
    var otherCountEl = document.getElementById("bulk-other-count");
    var modeLabel = document.getElementById("bulk-mode-label");
    var selectAllBtn = document.getElementById("btn-bulk-select-all");
    var checkAll = document.getElementById("doc-check-all");
    var boxes = tbody.querySelectorAll(".doc-check");
    var pageSize = boxes.length;

    if (num) num.textContent = selectedCount;
    if (pageCountEl) pageCountEl.textContent = pageCount;
    if (otherCountEl) otherCountEl.textContent = Math.max(0, selectedCount - pageCount);
    if (modeLabel) modeLabel.hidden = selectionMode !== "filter";
    if (selectAllBtn) {
      selectAllBtn.hidden = selectionMode === "filter" || state.total <= selectedCount;
      selectAllBtn.textContent = "选择当前筛选全部（" + state.total + " 条）";
    }
    bulkBar.hidden = selectedCount === 0;
    bulkBar.classList.toggle("show", selectedCount > 0);

    if (checkAll) {
      checkAll.checked = pageSize > 0 && pageCount === pageSize;
      checkAll.indeterminate = pageCount > 0 && pageCount < pageSize;
      checkAll.disabled = pageSize === 0;
      checkAll.setAttribute(
        "aria-label",
        selectedCount
          ? "全选本页（已选 " + selectedCount + " 条，本页 " + pageCount + " 条）"
          : "全选本页"
      );
    }
  }

  function setCurrentPageSelected(checked) {
    state.visible_ids.forEach(function (id) {
      if (selectionMode === "filter") {
        if (checked) excludedIds.delete(id);
        else excludedIds.add(id);
      } else if (checked) {
        selectedIds.add(id);
      } else {
        selectedIds.delete(id);
      }
      var box = tbody.querySelector('.doc-check[data-id="' + id + '"]');
      if (box) box.checked = checked;
    });
    syncBulkBar();
  }

  function buildSelectionPayload() {
    if (selectionMode === "filter") {
      return {
        mode: "filter",
        ids: [],
        filters: currentSelectionFilters(),
        exclude_ids: Array.from(excludedIds),
      };
    }
    return {
      mode: "ids",
      ids: Array.from(selectedIds),
      filters: {},
      exclude_ids: [],
    };
  }

  function selectionSummaryText() {
    var total = totalSelectedCount();
    if (selectionMode === "filter") {
      return "当前筛选全部 " + state.total + " 条" +
        (excludedIds.size ? "，排除 " + excludedIds.size + " 条" : "") +
        "；有效选择 " + total + " 条";
    }
    return "逐条选择 " + total + " 条";
  }

  tbody.addEventListener("change", function (e) {
    var cb = e.target.closest(".doc-check");
    if (!cb) return;
    var id = parseInt(cb.dataset.id, 10);
    if (selectionMode === "filter") {
      if (cb.checked) excludedIds.delete(id);
      else excludedIds.add(id);
    } else if (cb.checked) {
      selectedIds.add(id);
    } else {
      selectedIds.delete(id);
    }
    syncBulkBar();
  });

  var checkAllEl = document.getElementById("doc-check-all");
  if (checkAllEl) {
    checkAllEl.addEventListener("change", function () {
      setCurrentPageSelected(checkAllEl.checked);
    });
  }

  var bulkClearBtn = document.getElementById("btn-bulk-clear");
  if (bulkClearBtn) {
    bulkClearBtn.addEventListener("click", function () {
      clearSelection();
      ui.toast("已清空选择", "info");
    });
  }

  var bulkSelectAllBtn = document.getElementById("btn-bulk-select-all");
  if (bulkSelectAllBtn) {
    bulkSelectAllBtn.addEventListener("click", function () {
      if (!state.total) {
        ui.toast("当前筛选没有可选择的资料", "warning");
        return;
      }
      selectedIds.clear();
      excludedIds.clear();
      selectionMode = "filter";
      setCurrentPageSelected(true);
      ui.toast("已选择当前筛选全部 " + state.total + " 条", "success");
    });
  }

  function actionLabel(key) {
    return {
      set_category: "设置分类",
      clear_category: "清除分类",
      add_tags: "追加标签",
      remove_tags: "移除标签",
      set_audience: "设置可见范围",
      archive: "删除到回收站",
      restore: "从回收站恢复",
    }[key] || key;
  }

  function actionDescription(key) {
    return {
      set_category: "把所选资料统一归入一个分类。",
      clear_category: "将所选资料置为未分类，不删除资料或文件。",
      add_tags: "为所选资料追加标签；已有同一标签的资料不会重复添加。",
      remove_tags: "从所选资料上移除指定标签，不删除标签本身。",
      set_audience: "调整谁能在资料列表中看到这些资料。扩大为全体可见时会显示警告。",
      archive: "移入回收站，资料和主文件/附件一起保留，可由管理员恢复。",
      restore: "仅对回收站中的资料生效；当前列表不是已删除状态时会被逐条跳过。",
    }[key] || "";
  }

  function categoryOptionHtml() {
    var html = '<option value="">请选择分类…</option>';
    (function walk(list, depth) {
      list.forEach(function (c) {
        html += '<option value="' + c.id + '">' +
          new Array(depth + 1).join("　") + (depth ? "└ " : "") + esc(c.name) +
          "</option>";
        walk(childrenOf(c.id), depth + 1);
      });
    })(childrenOf(null), 0);
    return html;
  }

  function batchCheckListHtml(id, labelId, items, labelFn, searchPlaceholder) {
    if (!items.length) {
      return '<div class="batch-empty-note">暂无可选项</div>';
    }
    var options = items.map(function (item) {
      return '<label class="batch-check-item">' +
        '<input type="checkbox" value="' + esc(String(item.id)) + '">' +
        '<span class="batch-check-text">' + esc(labelFn(item)) + "</span></label>";
    }).join("");
    return '<div class="batch-check-tools">' +
      '<input class="form-control batch-check-search" type="search" ' +
      'data-batch-check-search="' + esc(id) + '" aria-label="' +
      esc(searchPlaceholder) + '" placeholder="' + esc(searchPlaceholder) + '">' +
      '<span class="batch-check-count" data-batch-check-count="' + esc(id) + '">已选 0 项</span>' +
      '</div><div class="batch-check-list" id="' + esc(id) + '" role="group" aria-labelledby="' +
      esc(labelId) + '">' + options + "</div>";
  }

  function auditOldLabel(value) {
    return {
      all: "全体可见",
      department: "指定部门",
      user: "指定人员",
    }[value] || value;
  }

  function parsePageItems(body) {
    return {
      items: (body && body.items) || [],
      pages: (body && body.pages) || 0,
    };
  }

  async function fetchDirectoryPages(path, extraParams) {
    var firstParams = Object.assign({ page: 1, page_size: 100 }, extraParams || {});
    var first = parsePageItems(await api.get(path, firstParams));
    var items = first.items.slice();
    for (var page = 2; page <= first.pages; page++) {
      var next = parsePageItems(await api.get(path, Object.assign({}, firstParams, { page: page })));
      items = items.concat(next.items);
    }
    return items;
  }

  function ensureDirectories() {
    if (directoryCache.loaded) return Promise.resolve(directoryCache);
    if (directoryCache.loading) return directoryCache.loading;
    directoryCache.loading = Promise.all([
      fetchDirectoryPages("/api/v1/departments", { is_active: true }),
      fetchDirectoryPages("/api/v1/users", { is_active: true }),
    ]).then(function (results) {
      directoryCache.departments = results[0];
      directoryCache.users = results[1].filter(function (u) {
        return u.is_active && u.role !== "admin";
      });
      directoryCache.loaded = true;
      directoryCache.loading = null;
      return directoryCache;
    }).catch(function (err) {
      directoryCache.loading = null;
      throw err;
    });
    return directoryCache.loading;
  }

  function renderBatchParams(overlay) {
    var actionKey = overlay.querySelector("#batch-action-key").value;
    var container = overlay.querySelector("#batch-params");
    if (!container) return;
    var html = "";
    if (actionKey === "set_category") {
      html = '<div class="form-group"><label class="form-label" for="batch-category-id">目标分类</label>' +
        '<select class="form-control" id="batch-category-id">' + categoryOptionHtml() + "</select></div>";
    } else if (actionKey === "clear_category") {
      html = '<div class="batch-action-note">所选资料会变为未分类；已有文件、标签和通知关联不受影响。</div>';
    } else if (actionKey === "add_tags" || actionKey === "remove_tags") {
      html = '<div class="form-group">' +
        '<label class="form-label" id="batch-tag-ids-label">选择标签（可多选）</label>' +
        batchCheckListHtml("batch-tag-ids", "batch-tag-ids-label", allTags,
          function (t) { return t.name; }, "搜索标签…") +
        '<p class="form-hint">单击标签即可选中或取消，可连续选择多个。</p></div>';
    } else if (actionKey === "set_audience") {
      html = '<div class="form-group"><label class="form-label" for="batch-audience-scope">可见范围</label>' +
        '<select class="form-control" id="batch-audience-scope">' +
        '<option value="all">全体可见</option>' +
        '<option value="department">指定部门</option>' +
        '<option value="user">指定人员</option></select></div>' +
        '<div id="batch-audience-directory"></div>';
      container.innerHTML = html;
      updateBatchAudienceFields(overlay);
      if (!directoryCache.loaded) {
        var directoryHost = overlay.querySelector("#batch-audience-directory");
        directoryHost.innerHTML = '<div class="batch-inline-loading">正在加载部门与人员…</div>';
        ensureDirectories().then(function () {
          if (document.body.contains(overlay) &&
              overlay.querySelector("#batch-action-key").value === "set_audience") {
            updateBatchAudienceFields(overlay);
          }
        }).catch(function (err) {
          if (directoryHost) {
            directoryHost.innerHTML = '<div class="form-error">目录加载失败：' + esc(err.message) + "</div>";
          }
        });
      }
      return;
    } else if (actionKey === "archive") {
      html = '<div class="form-group"><label class="form-label" for="batch-archive-reason">删除原因（可选）</label>' +
        '<input class="form-control" id="batch-archive-reason" maxlength="255" ' +
        'placeholder="例如：2026 年制度换版"></div>';
    } else if (actionKey === "restore") {
      html = '<div class="batch-action-note">恢复会同时恢复资料当前主文件与附件。当前列表中的未删除资料会被逐条跳过。</div>';
    }
    container.innerHTML = html;
  }

  function updateBatchAudienceFields(overlay) {
    var scopeEl = overlay.querySelector("#batch-audience-scope");
    var host = overlay.querySelector("#batch-audience-directory");
    if (!scopeEl || !host) return;
    if (!directoryCache.loaded) return;
    var scope = scopeEl.value;
    if (scope === "all") {
      host.innerHTML = '<div class="batch-action-note">全体登录用户都能看到所选资料。</div>';
      return;
    }
    if (scope === "department") {
      host.innerHTML = '<div class="form-group">' +
        '<label class="form-label" id="batch-department-ids-label">目标部门（可多选）</label>' +
        batchCheckListHtml("batch-department-ids", "batch-department-ids-label",
          directoryCache.departments, function (d) { return d.name; }, "搜索部门…") +
        '<p class="form-hint">单击部门即可选中或取消，可连续选择多个。</p></div>';
      return;
    }
    host.innerHTML = '<div class="form-group">' +
      '<label class="form-label" id="batch-user-ids-label">目标人员（可多选）</label>' +
      batchCheckListHtml("batch-user-ids", "batch-user-ids-label", directoryCache.users,
        function (u) { return u.display_name + "（" + u.username + "）"; }, "搜索人员…") +
      '<p class="form-hint">单击姓名即可选中或取消，可连续选择多人。</p></div>';
  }

  function selectedNumberValues(overlay, selector) {
    var list = overlay.querySelector(selector);
    if (!list) return [];
    return Array.prototype.slice.call(
      list.querySelectorAll('input[type="checkbox"]:checked')
    ).map(function (input) {
      return parseInt(input.value, 10);
    }).filter(function (n) { return !Number.isNaN(n); });
  }

  function updateBatchCheckCount(list) {
    if (!list || !list.id) return;
    var counter = document.querySelector(
      '[data-batch-check-count="' + list.id + '"]'
    );
    if (counter) {
      counter.textContent = "已选 " + list.querySelectorAll(
        'input[type="checkbox"]:checked'
      ).length + " 项";
    }
  }

  function filterBatchCheckList(searchInput) {
    var listId = searchInput.getAttribute("data-batch-check-search");
    var list = listId && document.getElementById(listId);
    if (!list) return;
    var query = searchInput.value.trim().toLocaleLowerCase();
    Array.prototype.forEach.call(list.querySelectorAll(".batch-check-item"), function (item) {
      item.hidden = !!query && item.textContent.toLocaleLowerCase().indexOf(query) < 0;
    });
  }

  function collectBatchParams(overlay, actionKey) {
    if (actionKey === "set_category") {
      var categoryId = parseInt((overlay.querySelector("#batch-category-id") || {}).value, 10);
      if (!categoryId) throw new Error("请选择目标分类");
      return { category_id: categoryId };
    }
    if (actionKey === "add_tags" || actionKey === "remove_tags") {
      var tagIds = selectedNumberValues(overlay, "#batch-tag-ids");
      if (!tagIds.length) throw new Error("请至少选择一个标签");
      return { tag_ids: tagIds };
    }
    if (actionKey === "set_audience") {
      var scopeEl = overlay.querySelector("#batch-audience-scope");
      var scope = scopeEl ? scopeEl.value : "";
      if (["all", "department", "user"].indexOf(scope) < 0) {
        throw new Error("请选择可见范围");
      }
      var departmentIds = scope === "department"
        ? selectedNumberValues(overlay, "#batch-department-ids") : [];
      var userIds = scope === "user"
        ? selectedNumberValues(overlay, "#batch-user-ids") : [];
      if (scope === "department" && !departmentIds.length) throw new Error("请至少选择一个部门");
      if (scope === "user" && !userIds.length) throw new Error("请至少选择一名人员");
      return {
        scope: scope,
        department_ids: departmentIds,
        user_ids: userIds,
      };
    }
    if (actionKey === "archive") {
      var reasonEl = overlay.querySelector("#batch-archive-reason");
      return { reason: reasonEl && reasonEl.value.trim() || null };
    }
    return {};
  }

  function invalidateBatchPreview(overlay) {
    batchPreview = null;
    var previewEl = overlay.querySelector("#batch-preview");
    var executeBtn = overlay.querySelector("#btn-batch-execute");
    if (previewEl) {
      previewEl.hidden = true;
      previewEl.innerHTML = "";
    }
    if (executeBtn) {
      executeBtn.disabled = true;
      executeBtn.classList.toggle("btn-danger", false);
    }
  }

  function renderSkippedList(items, emptyText) {
    if (!items || !items.length) {
      return '<p class="batch-empty-note">' + esc(emptyText || "无跳过项") + "</p>";
    }
    return '<div class="batch-skip-list">' + items.map(function (item) {
      return '<div class="batch-skip-item"><span class="batch-skip-title">#' +
        esc(String(item.id || "—")) + " " + esc(item.title || "未命名资料") + "</span>" +
        '<span class="batch-skip-reason">' + esc(item.reason || "未说明原因") + "</span></div>";
    }).join("") + "</div>";
  }

  function renderBatchPreview(overlay, data, actionKey) {
    batchPreview = data;
    var previewEl = overlay.querySelector("#batch-preview");
    var executeBtn = overlay.querySelector("#btn-batch-execute");
    if (!previewEl || !executeBtn) return;
    var warnings = data.warnings || [];
    var skipped = data.skipped || [];
    var oldCounts = data.old_counts || {};
    var oldKeys = Object.keys(oldCounts);
    previewEl.hidden = false;
    previewEl.innerHTML =
      '<div class="batch-preview-head"><span>预览摘要</span><strong>' + esc(actionLabel(actionKey)) + "</strong></div>" +
      '<div class="batch-stat-grid">' +
      '<div class="batch-stat"><span>匹配</span><strong>' + (data.matched_count || 0) + " 条</strong></div>" +
      '<div class="batch-stat"><span>预计变更</span><strong>' + (data.affected_count || 0) + " 条</strong></div>" +
      '<div class="batch-stat"><span>跳过</span><strong>' + (data.skipped_count || skipped.length || 0) + " 条</strong></div>" +
      "</div>" +
      (oldKeys.length
        ? '<div class="batch-old-counts">' + oldKeys.map(function (key) {
            return '<span>' + esc(auditOldLabel(key)) + "：" + oldCounts[key] + " 条</span>";
          }).join("") + "</div>"
        : "") +
      (warnings.length
        ? '<div class="batch-warning-list"><b>注意（' + warnings.length + '）</b>' +
          warnings.map(function (w) { return "<p>" + esc(w.message || w.code || "") + "</p>"; }).join("") + "</div>"
        : "") +
      '<div class="batch-skipped"><b>逐条跳过</b>' + renderSkippedList(skipped, "预览没有跳过项") + "</div>" +
      '<p class="batch-preview-foot">预览 5 分钟内有效；执行前会再次核对资料集合与变更数。</p>';
    executeBtn.disabled = (data.matched_count || 0) === 0;
    executeBtn.classList.toggle("btn-danger", actionKey === "archive");
    executeBtn.textContent = actionKey === "archive" ? "确认批量删除" : "确认执行";
  }

  function renderBatchResult(overlay, actionKey, data) {
    var panel = overlay.querySelector("#doc-batch-panel");
    var titleEl = overlay.querySelector(".modal-title");
    if (titleEl) titleEl.textContent = "批量执行结果";
    if (!panel) return;
    var skipped = data.skipped || [];
    var failures = data.failures || [];
    panel.innerHTML =
      '<div class="batch-result-card" role="status" aria-live="polite">' +
      '<div class="batch-result-mark">✓</div>' +
      '<div><h4>' + esc(actionLabel(actionKey)) + "已完成</h4>" +
      '<p>匹配 ' + (data.matched_count || 0) + " 条，实际变更 " +
      (data.affected_count || 0) + " 条，跳过 " + skipped.length + " 条。</p></div></div>" +
      '<div class="batch-result-section"><h4>逐条跳过</h4>' +
      renderSkippedList(skipped, "没有跳过项") + "</div>" +
      (failures.length
        ? '<div class="batch-result-section"><h4>失败项</h4>' +
          renderSkippedList(failures, "没有失败项") + "</div>"
        : "") +
      '<div class="batch-modal-actions"><button type="button" class="btn btn-primary" data-batch-close>完成</button></div>';
  }

  function closeBatchPanel(overlay) {
    if (overlay) ui.closeModal(overlay);
    batchModal = null;
    batchPreview = null;
  }

  function openBatchPanel() {
    var selectedCount = totalSelectedCount();
    if (!selectedCount) {
      ui.toast("请先勾选资料，或选择当前筛选全部", "warning");
      return;
    }
    if (batchModal) closeBatchPanel(batchModal);
    batchPreview = null;
    var html =
      '<div class="doc-batch-panel" id="doc-batch-panel">' +
      '<div class="batch-selection-summary"><span>本次操作范围</span><strong>' +
      esc(selectionSummaryText()) + "</strong></div>" +
      '<div class="form-group"><label class="form-label" for="batch-action-key">动作</label>' +
      '<select class="form-control" id="batch-action-key">' +
      '<option value="set_category">设置分类</option>' +
      '<option value="clear_category">清除分类</option>' +
      '<option value="add_tags">追加标签</option>' +
      '<option value="remove_tags">移除标签</option>' +
      '<option value="set_audience">设置可见范围</option>' +
      '<option value="archive">删除到回收站</option>' +
      '<option value="restore">从回收站恢复</option>' +
      "</select>" +
      '<p class="form-hint" id="batch-action-description">' + esc(actionDescription("set_category")) + "</p></div>" +
      '<div id="batch-params" class="batch-params"></div>' +
      '<div id="batch-preview" class="batch-preview" aria-live="polite" hidden></div>' +
      '<div class="batch-modal-actions">' +
      '<button type="button" class="btn btn-ghost" data-batch-close>取消</button>' +
      '<span class="bulk-spacer"></span>' +
      '<button type="button" class="btn btn-secondary" id="btn-batch-preview">预览影响</button>' +
      '<button type="button" class="btn btn-primary" id="btn-batch-execute" disabled>确认执行</button>' +
      "</div></div>";
    var overlay;
    overlay = ui.openModal(html, {
      title: "批量操作",
      size: "lg",
      initialFocus: "#batch-action-key",
      onRequestClose: function () { closeBatchPanel(overlay); },
    });
    batchModal = overlay;
    renderBatchParams(overlay);

    overlay.addEventListener("click", function (e) {
      if (e.target.closest("[data-batch-close]")) {
        closeBatchPanel(overlay);
      }
    });
    overlay.querySelector("#batch-action-key").addEventListener("change", function () {
      var actionKey = this.value;
      overlay.querySelector("#batch-action-description").textContent = actionDescription(actionKey);
      invalidateBatchPreview(overlay);
      renderBatchParams(overlay);
    });
    overlay.querySelector("#batch-params").addEventListener("change", function (e) {
      if (e.target && e.target.id === "batch-audience-scope") {
        updateBatchAudienceFields(overlay);
      }
      if (e.target && e.target.matches('.batch-check-list input[type="checkbox"]')) {
        updateBatchCheckCount(e.target.closest(".batch-check-list"));
      }
      invalidateBatchPreview(overlay);
    });
    overlay.querySelector("#batch-params").addEventListener("input", function (e) {
      if (e.target && e.target.matches(".batch-check-search")) {
        filterBatchCheckList(e.target);
      }
      invalidateBatchPreview(overlay);
    });

    overlay.querySelector("#btn-batch-preview").addEventListener("click", async function () {
      var previewBtn = this;
      var actionKey = overlay.querySelector("#batch-action-key").value;
      var params;
      try {
        params = collectBatchParams(overlay, actionKey);
      } catch (err) {
        ui.toast(err.message, "warning");
        return;
      }
      invalidateBatchPreview(overlay);
      ui.btnLoading(previewBtn, true, "预览中…");
      try {
        var data = await api.post(
          "/api/v1/documents/batch-actions/" + encodeURIComponent(actionKey) + "/preview",
          {
            selection: buildSelectionPayload(),
            params: params,
          }
        );
        renderBatchPreview(overlay, data, actionKey);
      } catch (err) {
        ui.toast(err.message, "error");
      } finally {
        ui.btnLoading(previewBtn, false);
      }
    });

    overlay.querySelector("#btn-batch-execute").addEventListener("click", async function () {
      if (!batchPreview) {
        ui.toast("请先预览影响范围", "warning");
        return;
      }
      var executeBtn = this;
      var actionKey = overlay.querySelector("#batch-action-key").value;
      var params;
      try {
        params = collectBatchParams(overlay, actionKey);
      } catch (err) {
        ui.toast(err.message, "warning");
        return;
      }
      var payload = {
        selection: buildSelectionPayload(),
        params: params,
        expected_count: batchPreview.affected_count,
        preview_token: batchPreview.preview_token,
      };
      ui.btnLoading(executeBtn, true, "执行中…");
      try {
        var data = await api.post(
          "/api/v1/documents/batch-actions/" + encodeURIComponent(actionKey) + "/execute",
          payload
        );
        renderBatchResult(overlay, actionKey, data);
        clearSelection();
        load();
      } catch (err) {
        invalidateBatchPreview(overlay);
        ui.toast(err.message, "error");
      } finally {
        ui.btnLoading(executeBtn, false);
      }
    });
  }

  var bulkOpenBtn = document.getElementById("btn-bulk-open");
  if (bulkOpenBtn) bulkOpenBtn.addEventListener("click", openBatchPanel);

  /* ---------------- 行操作 ---------------- */
  tbody.addEventListener("click", async function (e) {
    // 标签 chip → 按标签筛选
    var chip = e.target.closest(".tag-chip[data-tag-id]");
    if (chip) {
      e.preventDefault();
      e.stopPropagation();
      clearSelection();
      tagEl.value = chip.dataset.tagId;
      state.page = 1;
      syncAdvCount();
      load();
      return;
    }
    var archiveBtn = e.target.closest("[data-action=archive]");
    if (!archiveBtn) return;
    e.stopPropagation();
    var reason = await ui.confirmModal({
      title: "删除资料",
      danger: true,
      message:
        "删除后该资料进入回收站（软删除，不物理删除），普通用户将不可见；" +
        "可在「管理 → 回收站」中恢复。确定删除「" + archiveBtn.dataset.title + "」吗？",
      inputLabel: "删除原因（可选）",
      inputPlaceholder: "例如：版本废止，不再使用",
      confirmText: "确认删除",
    });
    if (reason === false) return;
    ui.btnLoading(archiveBtn, true, "处理中");
    try {
      await api.del("/api/v1/documents/" + archiveBtn.dataset.id, { reason: reason || null });
      ui.toast("资料已删除，可在回收站中恢复", "success");
      clearSelection();
      load();
    } catch (err) {
      ui.toast(err.message, "error");
      ui.btnLoading(archiveBtn, false);
    }
  });

  /* ---------------- 加载 ---------------- */
  function buildQuery() {
    var query = {
      page: state.page,
      page_size: state.page_size,
      keyword: keywordEl.value.trim(),
      category_id: state.category_id || undefined,
      tag_id: tagEl.value || undefined,
      status: state.status || undefined,
      cover_from: coverFromEl.value || undefined,
      cover_to: coverToEl.value || undefined,
      uncategorized: state.uncategorized || undefined,
      untagged: state.untagged || undefined,
    };
    if (audienceScopeEl && audienceScopeEl.value) {
      query.audience_scope = audienceScopeEl.value;
    }
    query.sort = sortEl ? sortEl.value : "id_desc";
    return query;
  }

  async function load(focusAfter) {
    var colspan = isAdmin ? 6 : 5;
    setLibraryEmpty(false);
    var skeletonRows = "";
    for (var i = 0; i < 4; i++) {
      skeletonRows +=
        '<tr><td colspan="' + colspan + '"><div class="skeleton skeleton-row"></div></td></tr>';
    }
    tbody.innerHTML = skeletonRows;
    tbody.setAttribute("aria-busy", "true");
    if (liveEl) liveEl.textContent = "正在加载资料…";
    syncUrl();
    syncAdvCount();
    renderFilterSummary();
    try {
      var data = await api.get("/api/v1/documents", buildQuery());
      var total = typeof data.total === "number" ? data.total : (data.items || []).length;
      state.total = total;
      var emptyLibrary = total === 0 && !hasActiveFilters();
      // 2026-11-04 产品决策：与通知页一致，取消「1–2 条自动变卡片」。
      if (emptyLibrary) {
        setLibraryEmpty(true);
        clearSelection();
        state.visible_ids = [];
        tbody.innerHTML = "";
        pager.innerHTML = "";
        if (liveEl) liveEl.textContent = "资料库暂无资料";
        return;
      }
      if (total === 0) clearSelection();
      render(data.items);
      if (liveEl) liveEl.textContent = "共 " + total + " 份资料";
      ui.renderPagination(pager, data.page, data.pages, function (p) {
        state.page = p;
        load(true);
      });
      if (focusAfter) {
        var heading = document.getElementById("docs-page-title");
        if (heading) heading.focus();
      }
    } catch (e) {
      setLibraryEmpty(false);
      pager.innerHTML = "";
      tbody.innerHTML =
        '<tr><td colspan="' + colspan + '"><div class="list-error" role="alert">' +
        "<p>加载资料失败：" + esc(e.message) + "</p>" +
        '<button class="btn btn-secondary btn-sm" type="button" id="btn-retry-docs">重试</button>' +
        "</div></td></tr>";
      if (liveEl) liveEl.textContent = "资料加载失败";
      var retry = document.getElementById("btn-retry-docs");
      if (retry) retry.addEventListener("click", load);
    } finally {
      tbody.removeAttribute("aria-busy");
    }
  }

  /* ---------------- 事件绑定 ---------------- */
  function resetFilters() {
    clearSelection();
    keywordEl.value = "";
    tagEl.value = "";
    coverFromEl.value = "";
    coverToEl.value = "";
    if (audienceScopeEl) audienceScopeEl.value = "";
    if (sortEl) sortEl.value = "id_desc";
    if (pageSizeEl) {
      state.page_size = 15;
      pageSizeEl.value = "15";
    }
    state.status = "";
    state.uncategorized = false;
    state.untagged = false;
    state.category_id = null;
    state.nav_cat = null;
    state.page = 1;
    paintTabs();
    renderNav();
    syncAdvCount();
    load();
  }

  function onFilterControlChange() {
    clearSelection();
    state.page = 1;
    syncAdvCount();
    load();
  }

  document.getElementById("btn-search").addEventListener("click", function () {
    clearSelection();
    state.page = 1;
    load();
  });
  document.getElementById("btn-reset").addEventListener("click", resetFilters);
  keywordEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      clearSelection();
      state.page = 1;
      load();
    }
  });
  [tagEl, coverFromEl, coverToEl, audienceScopeEl, sortEl].forEach(function (el) {
    if (!el) return;
    el.addEventListener("change", onFilterControlChange);
  });
  if (pageSizeEl) {
    pageSizeEl.addEventListener("change", function () {
      clearSelection();
      state.page_size = parseInt(pageSizeEl.value, 10) || 15;
      state.page = 1;
      syncAdvCount();
      load();
    });
  }

  // 记住上次使用的分类视图（默认逐级下钻）
  try {
    var savedView = localStorage.getItem(CAT_VIEW_KEY);
    if (savedView === "tree" || savedView === "drill") state.cat_view = savedView;
  } catch (err) { /* 隐私模式静默 */ }

  initFiltersFromUrl();
  setCatView(state.cat_view);
  loadFilters().then(load);
})();


/* 2026-11-09 UI 重审计收尾：390px 分类目录默认收起，点标题展开。
   只切换本地展示类，不改分类数据、筛选状态或 URL。 */
(function bindCategorySidebarToggle() {
  function bind() {
    var sidebar = document.getElementById("category-sidebar");
    var btn = document.getElementById("btn-cat-toggle");
    if (!sidebar || !btn || btn.dataset.catToggleBound === "1") return;
    btn.dataset.catToggleBound = "1";
    btn.addEventListener("click", function () {
      var open = sidebar.classList.toggle("cat-open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
