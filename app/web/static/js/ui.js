/**
 * ui.js —— 通用 UI 组件与格式化工具
 *
 * - toast(message, type)                轻提示（success/error/warning/info）
 * - confirmModal({...}) => Promise<boolean>  确认弹窗（danger/输入原因等）
 * - openModal(html, {title, size, closable, initialFocus, onRequestClose}) / closeModal()
 *   弹窗键盘可达性：Esc 关闭（closable !== false）、Tab/Shift+Tab 圈定、
 *   焦点自动落入与归还；onRequestClose 存在时 ✕/遮罩/Esc 经由回调关闭
 * - loading(text) / doneLoading()       全屏加载遮罩（带计数器，支持并发调用）
 * - btnLoading(btn, loading, text)      按钮 loading 态
 * - formatBytes / formatDateTime / formatDate / escapeHtml
 * - statusBadge / noticeStatusBadge / readStatusBadge / noticeTypeClass / roleBadge
 * - renderPagination(el, page, pages, onGo)
 * - fillSelect(el, items, opts)
 * - currentUser() / isAdmin()            当前用户与角色（唯一身份入口，勿用 DOM 反推）
 */
(function (global) {
  "use strict";

  /* 全站文件上传区：点击区域触发隐藏的 file input（修复"点击上传没反应"） */
  document.addEventListener("click", function (e) {
    /* 「返回」类旧按钮：只接受显式目标，避免无历史的页面被 history.back 留白。 */
    var backBtn = e.target.closest('[data-action="back"]');
    if (backBtn) {
      e.preventDefault();
      location.assign(backBtn.getAttribute("data-back-href") || "/");
      return;
    }
    var drop = e.target.closest(".file-upload");
    if (!drop) return;
    var input = drop.querySelector('input[type="file"]');
    if (input && e.target !== input && e.target.tagName !== "BUTTON") {
      input.click();
    }
  });

  /* ---------------------------------------------------------------
   * 密码显示/隐藏（事件委托，兼容登录页、改密页与弹窗）
   * --------------------------------------------------------------- */
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-password-toggle]");
    if (!btn) return;
    var input = document.getElementById(btn.getAttribute("data-password-toggle"));
    if (!input) return;
    var show = input.type === "password";
    input.type = show ? "text" : "password";
    btn.textContent = show ? "隐藏" : "显示";
    btn.setAttribute("aria-label", show ? "隐藏密码" : "显示密码");
    btn.setAttribute("aria-pressed", show ? "true" : "false");
  });

  /* ---------------------------------------------------------------
   * Toast
   * --------------------------------------------------------------- */
  var lastToast = { message: "", type: "", at: 0 };
  function toast(message, type, duration) {
    type = type || "info";
    duration = duration || 3200;
    var now = Date.now();
    if (lastToast.message === message && lastToast.type === type && now - lastToast.at < 1500) {
      return;
    }
    lastToast = { message: message, type: type, at: now };
    var container = document.getElementById("toast-container");
    if (!container) return;
    var el = document.createElement("div");
    el.className = "toast " + type;
    el.innerHTML = "<span>" + escapeHtml(message) + "</span>";
    container.appendChild(el);

    var close = function () {
      el.classList.add("out");
      setTimeout(function () {
        if (el.parentNode) el.parentNode.removeChild(el);
      }, 260);
    };
    setTimeout(close, duration);
    el.addEventListener("click", close);
  }

  /* ---------------------------------------------------------------
   * Modal
   * 键盘可达性：打开时焦点落入弹窗、Tab/Shift+Tab 圈定在弹窗内、
   * Esc 关闭（closable !== false 时）、关闭后焦点归还触发元素。
   * 叠层时仅最顶层弹窗响应 Tab/Esc。opts.onRequestClose 存在时，
   * ✕/遮罩/Esc 一律经由它请求关闭而不直接 closeModal——confirmModal
   * 借此保证 Promise 必定 resolve。
   * --------------------------------------------------------------- */
  var modalUid = 0;

  function topOverlay() {
    var root = document.getElementById("modal-root");
    if (!root) return null;
    var list = root.querySelectorAll(".modal-overlay");
    return list.length ? list[list.length - 1] : null;
  }

  function focusableIn(scope) {
    return Array.prototype.filter.call(
      scope.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), ' +
        'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      ),
      function (el) {
        return el.offsetParent !== null || el === document.activeElement;
      }
    );
  }

  function buildModal(html, opts) {
    opts = opts || {};
    var overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    var sizeClass = opts.size === "lg" ? " modal-lg" : opts.size === "sm" ? " modal-sm" : "";
    var titleId = "modal-title-" + (++modalUid);
    overlay.innerHTML =
      '<div class="modal' + sizeClass + '" role="dialog" aria-modal="true" aria-labelledby="' +
      titleId + '" tabindex="-1">' +
      '<div class="modal-header">' +
      '<h3 class="modal-title" id="' + titleId + '">' + escapeHtml(opts.title || "") + "</h3>" +
      '<button type="button" class="modal-close" aria-label="关闭">✕</button>' +
      "</div>" +
      '<div class="modal-body">' + html + "</div>" +
      "</div>";
    document.getElementById("modal-root").appendChild(overlay);
    // 下一帧加 show 触发动画
    requestAnimationFrame(function () {
      overlay.classList.add("show");
    });

    var trigger = document.activeElement;
    overlay.__trigger = trigger && trigger !== document.body ? trigger : null;

    function requestClose() {
      if (opts.onRequestClose) {
        opts.onRequestClose();
      } else {
        closeModal(overlay);
      }
    }

    function onKeydown(e) {
      if (topOverlay() !== overlay) return;
      if (e.key === "Escape") {
        if (opts.closable === false) return;
        e.preventDefault();
        e.stopPropagation();
        requestClose();
        return;
      }
      if (e.key !== "Tab") return;
      var list = focusableIn(overlay);
      if (!list.length) {
        e.preventDefault();
        overlay.querySelector(".modal").focus();
        return;
      }
      var first = list[0];
      var last = list[list.length - 1];
      var current = document.activeElement;
      if (e.shiftKey) {
        if (current === first || !overlay.contains(current)) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (current === last || !overlay.contains(current)) {
          e.preventDefault();
          first.focus();
        }
      }
    }
    document.addEventListener("keydown", onKeydown, true);
    overlay.__cleanup = function () {
      document.removeEventListener("keydown", onKeydown, true);
    };

    overlay.addEventListener("click", function (e) {
      if (e.target === overlay && opts.closable !== false) {
        requestClose();
      }
    });
    overlay.querySelector(".modal-close").addEventListener("click", function () {
      if (opts.closable !== false) requestClose();
    });

    // 初始焦点：优先 opts.initialFocus，否则正文第一个可聚焦元素（不落在 ✕ 上）
    var initial = null;
    if (typeof opts.initialFocus === "string") initial = overlay.querySelector(opts.initialFocus);
    else if (opts.initialFocus && opts.initialFocus.focus) initial = opts.initialFocus;
    if (!initial) {
      var bodyFocusable = focusableIn(overlay.querySelector(".modal-body"));
      initial = bodyFocusable.length ? bodyFocusable[0] : null;
    }
    if (initial && initial.focus) {
      initial.focus();
    } else {
      overlay.querySelector(".modal").focus();
    }
    return overlay;
  }

  function closeModal(overlay) {
    var root = document.getElementById("modal-root");
    var targets = [];
    if (overlay) {
      targets.push(overlay);
    } else if (root) {
      targets = Array.prototype.slice.call(root.querySelectorAll(".modal-overlay"));
    }
    targets.forEach(function (o) {
      if (typeof o.__cleanup === "function") {
        o.__cleanup();
        o.__cleanup = null;
      }
      var trigger = o.__trigger;
      o.classList.remove("show");
      setTimeout(function () {
        if (o.parentNode) o.parentNode.removeChild(o);
      }, 200);
      // 焦点归还触发元素；触发元素在弹窗内（叠层误存）时不归还
      if (trigger && document.contains(trigger) && !o.contains(trigger)) {
        trigger.focus();
      }
    });
  }

  /** 确认弹窗：resolve(true/false)；danger 样式 + 可选输入框（原因） */
  function confirmModal(opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      var overlay = null;
      var bodyHtml =
        '<div class="' + (opts.danger ? "alert alert-danger" : "alert alert-warning") + '">' +
        "<span>" + renderConfirmMessage(opts.message) + "</span></div>";
      if (opts.inputLabel) {
        bodyHtml +=
          '<div class="form-group mt-3">' +
          '<label class="form-label">' + escapeHtml(opts.inputLabel) +
          (opts.inputRequired ? '<span class="text-danger"> *</span>' : "") + "</label>" +
          '<input class="form-control" type="text" id="confirm-input" placeholder="' +
          escapeHtml(opts.inputPlaceholder || "") + '">' +
          '<p class="form-error">' + escapeHtml(opts.inputRequiredMsg || "此项必填") + "</p>" +
          "</div>";
        }
      overlay = buildModal(
        bodyHtml +
          '<div class="modal-footer">' +
          '<button type="button" class="btn btn-secondary" data-cancel>取消</button>' +
          '<button type="button" class="btn ' + (opts.danger ? "btn-danger" : "btn-primary") + '" data-confirm>' +
          escapeHtml(opts.confirmText || "确认") + "</button>" +
          "</div>",
        {
          title: opts.title || "操作确认",
          closable: true,
          // 危险操作默认聚焦「取消」防回车误触；有输入框时聚焦输入框
          initialFocus: opts.inputLabel ? "#confirm-input" : "[data-cancel]",
          // ✕ / 遮罩 / Esc 与「取消」同义：必须 resolve(false)，否则调用方永久挂起
          onRequestClose: function () {
            finish(false);
          },
        }
      );
      var input = overlay.querySelector("#confirm-input");
      var errEl = overlay.querySelector(".form-error");
      if (opts.inputLabel) {
        errEl.style.display = "none";
      }

      var closed = false;
      function finish(val) {
        if (closed) return;
        closed = true;
        closeModal(overlay);
        resolve(val);
      }

      overlay.querySelector("[data-cancel]").addEventListener("click", function () {
        finish(false);
      });
      overlay.querySelector("[data-confirm]").addEventListener("click", function () {
        if (opts.inputLabel) {
          var v = (input.value || "").trim();
          if (opts.inputRequired && !v) {
            errEl.style.display = "block";
            input.classList.add("is-invalid");
            input.focus();
            return;
          }
          finish(v);
        } else {
          finish(true);
        }
      });
    });
  }

  /* ---------------------------------------------------------------
   * Loading 遮罩（计数器，支持并发业务请求）
   * --------------------------------------------------------------- */
  var loadingCount = 0;
  var loadingRoot = null;

  function loading(text) {
    if (!loadingRoot) {
      loadingRoot = document.createElement("div");
      loadingRoot.className = "loading-overlay";
      document.getElementById("loading-root").appendChild(loadingRoot);
    }
    loadingCount += 1;
    loadingRoot.innerHTML =
      '<div class="spinner"></div>' +
      '<div class="loading-text">' + escapeHtml(text || "加载中…") + "</div>";
  }

  function doneLoading() {
    loadingCount = Math.max(0, loadingCount - 1);
    if (loadingCount === 0 && loadingRoot) {
      loadingRoot.innerHTML = "";
    }
  }

  /* ---------------------------------------------------------------
   * 按钮 loading 态
   * --------------------------------------------------------------- */
  function btnLoading(btn, isLoading, loadingText) {
    if (!btn) return;
    if (isLoading) {
      btn.dataset.originHtml = btn.innerHTML;
      btn.dataset.disabled = btn.disabled ? "1" : "";
      btn.disabled = true;
      btn.innerHTML =
        '<span class="btn-spinner"></span><span>' +
        escapeHtml(loadingText || "处理中…") + "</span>";
    } else {
      btn.disabled = !!btn.dataset.disabled && btn.dataset.disabled === "1";
      if (btn.dataset.originHtml) {
        btn.innerHTML = btn.dataset.originHtml;
        delete btn.dataset.originHtml;
      }
      delete btn.dataset.disabled;
    }
  }

  /* ---------------------------------------------------------------
   * 格式化
   * --------------------------------------------------------------- */
  function formatBytes(bytes) {
    if (bytes === null || bytes === undefined) return "—";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(2) + " MB";
    return (bytes / (1024 * 1024 * 1024)).toFixed(2) + " GB";
  }

  function pad2(n) {
    return n < 10 ? "0" + n : "" + n;
  }

  function formatDateTime(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return (
      d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) +
      " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes())
    );
  }

  function formatDate(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }

  function escapeHtml(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /**
   * DOCX 结构化预览：后端只返回 blocks（文本 + 结构），这里用 DOM API 创建节点，
   * 不把任何字符串当 HTML 解析，保持“预览内容永不作 HTML 信任”的 XSS 纵深。
   */
  function appendDocxRuns(target, runs, fallback) {
    var list = runs && runs.length ? runs : [{ text: fallback || "" }];
    list.forEach(function (run) {
      var text = run && run.text !== null && run.text !== undefined ? String(run.text) : "";
      if (!text) return;
      var node = target;
      if (run.bold) {
        var bold = document.createElement("strong");
        node.appendChild(bold);
        node = bold;
      }
      if (run.italic) {
        var italic = document.createElement("em");
        node.appendChild(italic);
        node = italic;
      }
      if (run.underline) {
        var underline = document.createElement("u");
        node.appendChild(underline);
        node = underline;
      }
      node.appendChild(document.createTextNode(text));
    });
  }

  function renderDocxBlocks(container, data) {
    if (!container) return;
    container.textContent = "";
    var blocks = data && data.blocks ? data.blocks : [];
    if (!blocks.length) {
      var fallback = document.createElement("div");
      fallback.className = "preview-text";
      fallback.textContent = (data && data.text) || "";
      container.appendChild(fallback);
    } else {
      var preview = document.createElement("div");
      preview.className = "docx-preview";
      blocks.forEach(function (block) {
        if (!block || !block.type) return;
        if (block.type === "heading") {
          var level = Math.min(Math.max(parseInt(block.level, 10) || 1, 1), 6);
          // 页面标题本身是 h1；DOCX 标题从 h2 起，避免一页多个 h1
          var heading = document.createElement("h" + Math.min(level + 1, 6));
          appendDocxRuns(heading, block.runs, block.text);
          preview.appendChild(heading);
        } else if (block.type === "paragraph") {
          var paragraph = document.createElement("p");
          appendDocxRuns(paragraph, block.runs, block.text);
          preview.appendChild(paragraph);
        } else if (block.type === "list") {
          var list = document.createElement(block.ordered ? "ol" : "ul");
          var listLevel = Math.max(parseInt(block.level, 10) || 0, 0);
          if (listLevel) list.style.paddingLeft = 24 + listLevel * 16 + "px";
          (block.items || []).forEach(function (item) {
            var li = document.createElement("li");
            appendDocxRuns(li, item.runs, item.text);
            list.appendChild(li);
          });
          preview.appendChild(list);
        } else if (block.type === "table") {
          var table = document.createElement("table");
          table.className = "docx-table";
          var tbody = document.createElement("tbody");
          (block.rows || []).forEach(function (row) {
            var tr = document.createElement("tr");
            (row || []).forEach(function (cell) {
              var td = document.createElement("td");
              td.textContent = cell === null || cell === undefined ? "" : String(cell);
              tr.appendChild(td);
            });
            tbody.appendChild(tr);
          });
          table.appendChild(tbody);
          preview.appendChild(table);
        }
      });
      container.appendChild(preview);
    }
    var note = document.createElement("div");
    note.className = "preview-note";
    note.textContent = data && data.truncated
      ? "内容较长已截断显示，请下载文件查看完整内容。"
      : "预览为文本内容，复杂版式请以下载文件为准。";
    container.appendChild(note);
  }

  /**
   * 确认弹窗文案渲染（confirmModal 专用）。
   *
   * 2026-09-29 修复：回收站「永久删除 / 清空回收站」、备份「恢复备份」、操作日志
   * 「删除所选」四处调用方在 message 里写了 <strong> / <br> 做强调，而 confirmModal
   * 原先一律 escapeHtml → 弹窗里显示成「<strong>物理删除</strong>」字面标签，文案像坏了。
   *
   * 这里**不放行任意 HTML**（那会给所有调用方开 XSS 面），只做
   * 「先整体转义 → 再把白名单标签的字面量回填成真标签」：
   * 白名单外的标签、以及任何带属性的标签都会匹配失败而保持转义状态。
   * escapeHtml 先把 & 转成 &amp;，所以用户输入里真写的 "&lt;strong&gt;" 也不会被回填。
   */
  var CONFIRM_ALLOWED_TAGS = /&lt;(\/?)(strong|br)&gt;/g;

  function renderConfirmMessage(message) {
    return escapeHtml(message || "确认执行此操作？").replace(
      CONFIRM_ALLOWED_TAGS,
      function (match, slash, tag) {
        return tag === "br" ? "<br>" : "<" + slash + tag + ">";
      }
    );
  }

  /* ---------------------------------------------------------------
   * 徽章 / 标签
   * --------------------------------------------------------------- */
  var STATUS_LABELS = {
    pending: "未生效",
    active: "生效中",
    expired: "已过期",
    archived: "已删除",
  };

  function statusBadge(status) {
    return '<span class="badge badge-' + escapeHtml(status || "") + '">' +
      (STATUS_LABELS[status] || status || "—") + "</span>";
  }

  /* 纯文本状态名（不带徽章 HTML）。
   * 2026-11-04 补：`documents.js` 的筛选 chips 一直在调 `ui.statusLabel()`，
   * 但本文件从未导出该函数 → 浏览器抛 `ui.statusLabel is not a function`，
   * 筛选摘要渲染中断（带状态筛选进入资料列表时必现）。 */
  function statusLabel(status) {
    return STATUS_LABELS[status] || status || "—";
  }

  // 2026-11-11：草稿下线后正常通知均创建即发布，不再显示冗余状态；仅已删除状态需要徽章
  var NOTICE_STATUS_LABELS = { cancelled: "已删除" };

  function noticeStatusBadge(status, viewDate) {
    if (status === "published") return "";
    var label = NOTICE_STATUS_LABELS[status] || status || "—";
    return '<span class="badge badge-' + escapeHtml(status || "") + '">' + label + "</span>";
  }

  var READ_STATUS_LABELS = { unread: "未查看", read: "已查看" };

  function readStatusBadge(st) {
    return '<span class="badge badge-' + escapeHtml(st || "unread") + '">' +
      (READ_STATUS_LABELS[st] || st || "未查看") + "</span>";
  }

  var NOTICE_TYPE_CLASS = {
    发布: "badge-type",
    修订: "badge-type-revision",
    替换: "badge-type-replace",
    废止: "badge-type-abolish",
    延期: "badge-type-postpone",
    其他: "badge-type-other",
  };

  function noticeTypeBadge(type) {
    // 2026-11-09：删除通知类型后，已有通知类型置空；空类型显示为「未分类」，
    // 不再用「其他」冒充管理员显式选择。
    if (!type) return '<span class="badge badge-type-other">未分类</span>';
    return '<span class="badge ' + (NOTICE_TYPE_CLASS[type] || "badge-type-other") + '">' +
      escapeHtml(type) + "</span>";
  }

  function roleBadge(role) {
    var label =
      role === "admin" ? "管理员" : "普通用户";
    return '<span class="role-tag role-' + escapeHtml(role || "user") + '">' + label + "</span>";
  }

  function fileTypeBadge(ft) {
    return '<span class="file-chip ' + escapeHtml(ft || "") + '">' + escapeHtml(ft || "—") + "</span>";
  }

  /* ---------------------------------------------------------------
   * 分页渲染
   * --------------------------------------------------------------- */
  function renderPagination(el, page, pages, onGo) {
    if (!el) return;
    if (!pages || pages <= 1) {
      el.innerHTML = "";
      return;
    }
    var html = '<span class="page-info">第 ' + page + " / " + pages + " 页</span>";
    html +=
      '<button type="button" class="page-btn" data-p="' + (page - 1) + '"' +
      (page <= 1 ? " disabled" : "") + ">‹ 上一页</button>";

    var start = Math.max(1, page - 2);
    var end = Math.min(pages, page + 2);
    if (start > 1) {
      html += '<button type="button" class="page-btn" data-p="1">1</button>';
      if (start > 2) html += '<span class="page-btn" disabled>…</span>';
    }
    for (var i = start; i <= end; i++) {
      html +=
        '<button type="button" class="page-btn' + (i === page ? " active" : "") + '" data-p="' + i + '">' +
        i + "</button>";
    }
    if (end < pages) {
      if (end < pages - 1) html += '<span class="page-btn" disabled>…</span>';
      html += '<button type="button" class="page-btn" data-p="' + pages + '">' + pages + "</button>";
    }
    html +=
      '<button type="button" class="page-btn" data-p="' + (page + 1) + '"' +
      (page >= pages ? " disabled" : "") + ">下一页 ›</button>";
    // 跳页输入（页多时免逐页点）；change/Enter 触发
    html +=
      '<span class="page-jump">跳至' +
      '<input type="number" class="page-jump-input" min="1" max="' + pages + '" value="' + page + '" aria-label="跳转到指定页">' +
      "页</span>";
    el.innerHTML = html;
    el.querySelectorAll("[data-p]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var p = parseInt(btn.dataset.p, 10);
        if (p >= 1 && p <= pages && p !== page && onGo) onGo(p);
      });
    });
    var jump = el.querySelector(".page-jump-input");
    if (jump && onGo) {
      var goJump = function () {
        var p = parseInt(jump.value, 10);
        if (p >= 1 && p <= pages && p !== page) onGo(p);
      };
      jump.addEventListener("change", goJump);
      jump.addEventListener("keydown", function (e) {
        if (e.key === "Enter") goJump();
      });
    }
  }

  /* ---------------------------------------------------------------
   * 分类树展开状态记忆（localStorage）
   * 用法：树渲染完成后调 ui.treeState(key, treeEl).restore()。
   * 节点需在 .tree-node-content 上带 data-id 或 data-cat（数字 id）。
   * --------------------------------------------------------------- */
  function treeState(storageKey, treeEl) {
    function currentIds() {
      var out = [];
      treeEl.querySelectorAll(".tree-toggle.expanded").forEach(function (t) {
        var c = t.closest(".tree-node-content");
        if (!c) return;
        if (c.dataset.id) out.push(c.dataset.id);
        else if (c.dataset.cat && /^\d+$/.test(c.dataset.cat)) out.push(c.dataset.cat);
      });
      return out;
    }
    function save() {
      try {
        localStorage.setItem(storageKey, JSON.stringify(currentIds()));
      } catch (e) { /* 存储不可用时静默（隐私模式等） */ }
    }
    function restore() {
      var saved = [];
      try {
        saved = JSON.parse(localStorage.getItem(storageKey) || "[]");
      } catch (e) { saved = []; }
      var any = false;
      saved.forEach(function (id) {
        var content = treeEl.querySelector(
          '.tree-node-content[data-id="' + id + '"], .tree-node-content[data-cat="' + id + '"]'
        );
        if (!content) return;
        var toggle = content.querySelector(".tree-toggle");
        var kids = content.nextElementSibling;
        if (toggle && !toggle.classList.contains("leaf") &&
            kids && kids.classList.contains("tree-children")) {
          toggle.classList.add("expanded");
          kids.classList.remove("collapsed");
          any = true;
        }
      });
      // 恢复了展开态时同步单键双态按钮文案
      var btn = document.getElementById("btn-toggle-tree");
      if (any && btn) btn.textContent = "折叠全部 ▴";
      // 保存钩子挂在已有交互 listener 之后注册：点击/全部展开后写入最新状态
      treeEl.addEventListener("click", save);
      if (btn) btn.addEventListener("click", save);
    }
    return { restore: restore, save: save };
  }

  /* ---------------------------------------------------------------
   * Select 填充
   * --------------------------------------------------------------- */
  function fillSelect(el, items, opts) {
    opts = opts || {};
    var valueKey = opts.valueKey || "id";
    var labelKey = opts.labelKey || "name";
    var current = el.value;
    var html = "";
    if (opts.placeholder !== undefined) {
      html += '<option value="">' + escapeHtml(opts.placeholder) + "</option>";
    }
    (items || []).forEach(function (it) {
      html +=
        '<option value="' + escapeHtml(it[valueKey]) + '">' + escapeHtml(it[labelKey]) + "</option>";
    });
    el.innerHTML = html;
    if (current !== "" && current !== null && current !== undefined) {
      el.value = current;
    }
  }

  /* ---------------------------------------------------------------
   * 当前用户与角色（**唯一**身份入口）
   *
   * 历史教训：documents / notices / document_detail / notice_detail 四处的
   * isAdmin 曾是 `!!document.querySelector('#nav-admin-dropdown')` —— 以后端
   * 渲染的顶栏「管理」下拉是否存在来反推身份。2026-09-14 换肤把顶栏换成
   * 左侧栏后，该元素消失，这四处**静默**把管理员当成普通用户：
   * 资料列表因此不渲染选择列 → 表头 6 列、行 5 格 → 整行左移、标题挤成逐字换行。
   *
   * 结论：身份只能从 #user-data（后端注入的当前用户 JSON）读取，
   * 不得再通过"某个元素在不在"反推权限。新增页面请一律使用 ui.isAdmin()。
   * 注意：这里只控制"显示什么"，真正的权限一律由后端 REST 强制（AGENTS §33）。
   * --------------------------------------------------------------- */
  function currentUser() {
    try {
      var el = document.getElementById("user-data");
      return el ? JSON.parse(el.textContent) : null;
    } catch (e) {
      return null;
    }
  }

  function isAdmin() {
    var u = currentUser();
    return !!(u && u.role === "admin");
  }

  /* ---------------------------------------------------------------
   * 返回导航（2026-11-11）
   * - 列表页动态渲染的详情链接自动补 return_to（当前路径 + 原始 query）
   * - 详情页返回链接由服务端二次校验；这里只负责记录/恢复列表滚动位置
   * - 不依赖 history.back，也不创建第二条页内面包屑
   * --------------------------------------------------------------- */
  var LIST_RETURN_SPECS = {
    "/documents": /^\/documents\/\d+$/,
    "/notices": /^\/notices\/\d+$/,
  };
  var RETURN_SCROLL_PREFIX = "fms:return-scroll:";
  var RETURN_PENDING_PREFIX = "fms:return-pending:";
  var RETURN_SCROLL_TTL_MS = 30 * 60 * 1000;

  function listSpecForPath(pathname) {
    return Object.prototype.hasOwnProperty.call(LIST_RETURN_SPECS, pathname)
      ? LIST_RETURN_SPECS[pathname]
      : null;
  }

  function listUrlKey(urlLike) {
    var u = new URL(urlLike, location.href);
    return u.pathname + u.search;
  }

  function storageGet(key) {
    try {
      return sessionStorage.getItem(key);
    } catch (e) {
      return null;
    }
  }

  function storageSet(key, value) {
    try {
      sessionStorage.setItem(key, value);
    } catch (e) {
      /* 隐私模式或配额不足时静默降级：导航本身不受影响 */
    }
  }

  function storageRemove(key) {
    try {
      sessionStorage.removeItem(key);
    } catch (e) {
      /* 同上 */
    }
  }

  function sameOriginUrl(href) {
    try {
      var u = new URL(href, location.href);
      return u.origin === location.origin ? u : null;
    } catch (e) {
      return null;
    }
  }

  function decorateListDetailLink(link) {
    var spec = listSpecForPath(location.pathname);
    if (!spec || !link || !link.getAttribute) return;
    var href = link.getAttribute("href");
    if (!href || href.charAt(0) === "#") return;
    var target = sameOriginUrl(href);
    if (!target || !spec.test(target.pathname)) return;
    target.searchParams.set("return_to", location.pathname + location.search);
    var safeHref = target.pathname + target.search + target.hash;
    if (link.getAttribute("href") !== safeHref) link.setAttribute("href", safeHref);
  }

  function decorateListDetailLinks(root) {
    if (!root) return;
    if (root.nodeType === 1 && root.matches && root.matches("a[href]")) {
      decorateListDetailLink(root);
    }
    if (root.querySelectorAll) {
      root.querySelectorAll("a[href]").forEach(decorateListDetailLink);
    }
  }

  function storeListScroll(key) {
    storageSet(
      RETURN_SCROLL_PREFIX + key,
      JSON.stringify({ y: Math.max(0, window.scrollY || 0), at: Date.now() })
    );
  }

  function markReturnPending(key) {
    storageSet(RETURN_PENDING_PREFIX + key, String(Date.now()));
  }

  function readReturnScroll() {
    var spec = listSpecForPath(location.pathname);
    if (!spec) return null;
    var key = location.pathname + location.search;
    var pending = parseInt(storageGet(RETURN_PENDING_PREFIX + key), 10);
    if (!pending || Date.now() - pending > RETURN_SCROLL_TTL_MS) {
      storageRemove(RETURN_PENDING_PREFIX + key);
      return null;
    }
    var raw = storageGet(RETURN_SCROLL_PREFIX + key);
    if (!raw) {
      storageRemove(RETURN_PENDING_PREFIX + key);
      return null;
    }
    try {
      var state = JSON.parse(raw);
      if (!state || typeof state.y !== "number" || Date.now() - state.at > RETURN_SCROLL_TTL_MS) {
        storageRemove(RETURN_PENDING_PREFIX + key);
        storageRemove(RETURN_SCROLL_PREFIX + key);
        return null;
      }
      return { key: key, y: state.y };
    } catch (e) {
      storageRemove(RETURN_PENDING_PREFIX + key);
      storageRemove(RETURN_SCROLL_PREFIX + key);
      return null;
    }
  }

  function restoreListScroll() {
    var request = readReturnScroll();
    if (!request) return;
    var deadline = Date.now() + 7000;

    function attempt() {
      var root = document.documentElement;
      var maxY = Math.max(0, Math.max(root.scrollHeight, document.body.scrollHeight) - window.innerHeight);
      var done = maxY + 2 >= request.y;
      window.scrollTo(0, Math.min(request.y, maxY));
      if (done || Date.now() >= deadline) {
        storageRemove(RETURN_PENDING_PREFIX + request.key);
        storageRemove(RETURN_SCROLL_PREFIX + request.key);
        return;
      }
      setTimeout(attempt, 150);
    }

    requestAnimationFrame(attempt);
  }

  function initReturnNavigation() {
    decorateListDetailLinks(document);
    restoreListScroll();

    if (global.MutationObserver) {
      var observer = new MutationObserver(function (records) {
        records.forEach(function (record) {
          record.addedNodes.forEach(decorateListDetailLinks);
        });
      });
      observer.observe(document.body, { childList: true, subtree: true });
    }
  }

  /* 捕获阶段先于页面脚本：既补齐 href，也记录点击瞬间的真实滚动位置。 */
  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
      return;
    }
    var link = e.target.closest("a[href]");
    if (!link || (link.target && link.target !== "_self")) return;

    var listSpec = listSpecForPath(location.pathname);
    if (listSpec) {
      decorateListDetailLink(link);
      var detailUrl = sameOriginUrl(link.getAttribute("href"));
      if (detailUrl && listSpec.test(detailUrl.pathname)) {
        storeListScroll(location.pathname + location.search);
      }
    }

    if (link.hasAttribute("data-return-scroll")) {
      var returnUrl = sameOriginUrl(link.getAttribute("href"));
      if (returnUrl && listSpecForPath(returnUrl.pathname)) {
        markReturnPending(listUrlKey(returnUrl.href));
      }
    }
  }, true);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initReturnNavigation, { once: true });
  } else {
    initReturnNavigation();
  }

  global.ui = {
    toast: toast,
    confirmModal: confirmModal,
    openModal: buildModal,
    closeModal: closeModal,
    loading: loading,
    doneLoading: doneLoading,
    btnLoading: btnLoading,
    formatBytes: formatBytes,
    formatDateTime: formatDateTime,
    formatDate: formatDate,
    escapeHtml: escapeHtml,
    renderDocxBlocks: renderDocxBlocks,
    statusBadge: statusBadge,
    statusLabel: statusLabel,
    noticeStatusBadge: noticeStatusBadge,
    readStatusBadge: readStatusBadge,
    noticeTypeBadge: noticeTypeBadge,
    roleBadge: roleBadge,
    fileTypeBadge: fileTypeBadge,
    renderPagination: renderPagination,
    fillSelect: fillSelect,
    treeState: treeState,
    currentUser: currentUser,
    isAdmin: isAdmin,
  };
})(window);