/**
 * admin/recycle_bin.js —— 回收站
 * REST：GET /api/v1/recycle-bin（reason 筛选 + 分页）
 *      POST /api/v1/recycle-bin/{id}/restore（admin+）
 *      DELETE /api/v1/recycle-bin/{id}（仅 admin，须 {confirm:true} 二次确认）
 */
(function () {
  "use strict";

  var esc = ui.escapeHtml;
  var state = { page: 1, page_size: 15 };
  var me = ui.currentUser();
  var isAdmin = ui.isAdmin();

  var reasonEl = document.getElementById("f-reason");
  var purgeAllBtn = document.getElementById("btn-purge-all");
  var binCount = { files: 0, notices: 0 };
  var tbody = document.getElementById("rb-tbody");
  var pager = document.getElementById("rb-pagination");

  var REASON_LABELS = {
    replaced: '<span class="badge badge-pending">文件被替换</span>',
    archived: '<span class="badge badge-expired">资料归档</span>',
    deleted: '<span class="badge badge-expired">资料删除</span>',
    attachment_deleted: '<span class="badge badge-expired">删除附件</span>',
  };

  function buildQuery() {
    return {
      page: state.page,
      page_size: state.page_size,
      reason: reasonEl.value,
    };
  }

  function render(items) {
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="7"><div class="table-empty">回收站是空的</div></td></tr>';
      return;
    }
    tbody.innerHTML = items
      .map(function (r) {
        var ops = "";
        if (r.restored_at) {
          ops = '<span class="text-muted" style="font-size:12px;">已恢复</span>';
        } else {
          ops =
            '<button class="btn btn-sm btn-secondary" data-action="restore" data-id="' + r.id + '" data-name="' + esc(r.original_filename) + '">恢复</button>';
          if (isAdmin) {
            ops +=
              '<button class="btn btn-sm btn-danger-outline" data-action="purge" data-id="' + r.id + '" data-name="' + esc(r.original_filename) + '">永久删除</button>';
          }
        }
        // 归档条目可能同时带着主文件与附件（2026-11-06）：
        // 附件单独一行小字；仅有附件的条目（替换/删除附件）直接显示附件名，
        // 否则「原文件名」列会错显成资料标题。
        var isAttachmentOnly = !r.file_record_id && r.attachment_file_record_id;
        var attNote = "";
        if (r.attachment_filename && !isAttachmentOnly) {
          attNote = '<div class="row-title" style="font-weight:400;font-size:12px;">' +
            ui.fileTypeBadge(r.attachment_file_type) + " " +
            esc(r.attachment_filename) + "（附件）</div>";
        }
        var sizeHtml = r.size_bytes
          ? esc(ui.formatBytes(r.size_bytes))
          : r.attachment_size_bytes
            ? esc(ui.formatBytes(r.attachment_size_bytes))
            : "—";
        return (
          "<tr>" +
          '<td><div class="row-title">' + esc(r.original_filename || "（无主文件）") + "</div>" +
          attNote + "</td>" +
          '<td><a href="/documents/' + r.document_id + '">' + esc(r.document_title) + "</a></td>" +
          "<td>" + (REASON_LABELS[r.reason] || esc(r.reason)) + "</td>" +
          "<td>" + sizeHtml + "</td>" +
          "<td>" +
          (function (t) {
            return t ? ui.fileTypeBadge(t) : '<span class="text-muted">—</span>';
          })(r.file_type || r.attachment_file_type) +
          "</td>" +
          "<td class=\"text-muted\" style=\"font-size:13px;\">" +
          esc(r.deleted_username || "") + "<br>" + esc(ui.formatDateTime(r.deleted_at)) + "</td>" +
          '<td class="row-actions">' + ops + "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  async function load() {
    tbody.innerHTML = '<tr><td colspan="7"><div class="table-empty">加载中…</div></td></tr>';
    try {
      var data = await api.get("/api/v1/recycle-bin", buildQuery());
      binCount.files = data.total || 0;
      // 清空动作作用于全部条目（purge_all 不认 reason 筛选）：
      // 有筛选时另取未筛选总数，避免提示数小于实际永久删除数
      if (reasonEl.value) {
        var all = await api.get("/api/v1/recycle-bin", { page: 1, page_size: 1 });
        binCount.files = all.total || 0;
      }
      updatePurgeAll();
      render(data.items);
      ui.renderPagination(pager, data.page, data.pages, function (p) {
        state.page = p;
        load();
      });
    } catch (e) {
      tbody.innerHTML = '<tr><td colspan="7"><div class="table-empty text-danger">' + esc(e.message) + "</div></td></tr>";
    }
  }

  tbody.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-action]");
    if (!btn) return;
    var id = parseInt(btn.dataset.id, 10);
    var name = btn.dataset.name || "";

    if (btn.dataset.action === "restore") {
      ui.confirmModal({
        title: "恢复文件",
        message:
          "恢复「" + name +
          "」：归档/删除类将恢复整条资料；单独删除的附件会重新挂回原资料；被替换的旧文件仅回文件库（不顶替当前文件）。若资料已有附件或已归档，恢复会被拒绝。确定吗？",
        confirmText: "确认恢复",
      }).then(async function (ok) {
        if (!ok) return;
        ui.btnLoading(btn, true, "恢复中…");
        try {
          await api.post("/api/v1/recycle-bin/" + id + "/restore", {});
          ui.toast("已恢复", "success");
          load();
        } catch (err) {
          ui.toast(err.message, "error");
          ui.btnLoading(btn, false);
        }
      });
      return;
    }

    if (btn.dataset.action === "purge") {
      ui.confirmModal({
        title: "永久删除",
        danger: true,
        message:
          "即将<strong>物理删除</strong>「" + name + "」的文件副本，此操作不可恢复！",
        confirmText: "永久删除",
      }).then(async function (ok) {
        if (!ok) return;
        ui.btnLoading(btn, true, "删除中…");
        try {
          await api.del("/api/v1/recycle-bin/" + id, { confirm: true });
          ui.toast("已永久删除", "success");
          load();
        } catch (err) {
          ui.toast(err.message, "error");
          ui.btnLoading(btn, false);
        }
      });
    }
  });

  document.getElementById("btn-search").addEventListener("click", function () {
    state.page = 1;
    load();
  });
  document.getElementById("btn-reset").addEventListener("click", function () {
    reasonEl.value = "";
    state.page = 1;
    load();
  });

  // ==================== 清空回收站（2026-08-30） ====================
  function updatePurgeAll() {
    if (!purgeAllBtn) return;
    purgeAllBtn.hidden = binCount.files + binCount.notices === 0;
  }

  if (purgeAllBtn) {
    purgeAllBtn.addEventListener("click", function () {
      ui.confirmModal({
        title: "清空回收站",
        danger: true,
        message:
          "将<strong>永久删除</strong>回收站全部内容：" + binCount.files +
          " 个文件条目、" + binCount.notices + " 条已删除通知，不可恢复。删除动作将写入审计日志。",
        confirmText: "清空回收站",
      }).then(async function (ok) {
        if (!ok) return;
        ui.btnLoading(purgeAllBtn, true, "清空中…");
        try {
          var r = await api.post("/api/v1/recycle-bin/purge-all", { confirm: true });
          ui.toast("已清空：文件 " + r.files_purged + " 条、通知 " + r.notices_purged + " 条", "success");
          load();
          loadNotices();
        } catch (err) {
          ui.toast(err.message, "error");
        } finally {
          ui.btnLoading(purgeAllBtn, false);
        }
      });
    });
  }

  // ==================== 已删除通知 ====================
  var noticeBody = document.getElementById("notice-rb-tbody");
  var noticeCount = document.getElementById("notice-rb-count");
  var noticeBulkBar = document.getElementById("notice-rb-bulk-bar");
  var noticeBulkCount = document.getElementById("notice-rb-bulk-count");
  var noticeCheckAll = document.getElementById("notice-rb-check-all");
  var noticeBulkRestoreBtn = document.getElementById("btn-notice-rb-bulk-restore");
  var noticeBulkClearBtn = document.getElementById("btn-notice-rb-bulk-clear");
  var noticeBulkResult = document.getElementById("notice-rb-bulk-result");
  var noticeSelectedIds = new Set();
  var noticeSelectedTitles = {};

  function syncNoticeBulkBar() {
    if (!noticeBulkBar) return;
    var boxes = noticeBody ? noticeBody.querySelectorAll(".notice-rb-check") : [];
    var count = noticeSelectedIds.size;
    if (noticeBulkCount) noticeBulkCount.textContent = count;
    noticeBulkBar.hidden = count === 0;
    if (noticeCheckAll) {
      noticeCheckAll.checked = boxes.length > 0 && count >= boxes.length;
      noticeCheckAll.indeterminate = count > 0 && count < boxes.length;
    }
  }

  function clearNoticeSelection() {
    noticeSelectedIds.clear();
    noticeSelectedTitles = {};
    if (noticeBody) {
      noticeBody.querySelectorAll(".notice-rb-check").forEach(function (cb) { cb.checked = false; });
    }
    syncNoticeBulkBar();
  }

  function showNoticeBulkResult(result) {
    if (!noticeBulkResult) return;
    var succeeded = result.succeeded || [];
    var skipped = result.skipped || [];
    var html = "<strong>批量恢复：成功 " + succeeded.length + " 条，跳过 " +
      skipped.length + " 条。</strong>";
    var rows = [];
    succeeded.forEach(function (row) {
      rows.push('<li>成功：' + esc(row.title || ("通知 #" + row.id)) +
        '（ID ' + esc(row.id) + "）</li>");
    });
    skipped.forEach(function (row) {
      var title = noticeSelectedTitles[row.id] || ("通知 #" + row.id);
      rows.push('<li>跳过：' + esc(title) + "（ID " + esc(row.id) + "）— " +
        esc(row.reason || "未说明原因") + "</li>");
    });
    if (rows.length) {
      html += '<ul style="margin:8px 0 0;padding-left:20px;">' + rows.join("") + "</ul>";
    }
    noticeBulkResult.innerHTML = html;
    noticeBulkResult.hidden = false;
  }

  function loadNotices() {
    if (!noticeBody) return;
    clearNoticeSelection();
    noticeBody.innerHTML = '<tr><td colspan="7"><div class="table-empty">加载中…</div></td></tr>';
    api.get("/api/v1/recycle-bin/notices").then(function (data) {
      var items = data.items || [];
      binCount.notices = items.length;
      updatePurgeAll();
      noticeCount.textContent = items.length ? "共 " + items.length + " 条" : "无已删除通知";
      if (!items.length) {
        noticeBody.innerHTML = '<tr><td colspan="7"><div class="table-empty">暂无已删除通知</div></td></tr>';
        syncNoticeBulkBar();
        return;
      }
      noticeBody.innerHTML = items.map(function (n) {
        var checked = noticeSelectedIds.has(n.id) ? " checked" : "";
        return (
          "<tr>" +
          '<td><input type="checkbox" class="notice-rb-check" data-id="' + n.id + '"' +
          ' aria-label="选择通知：' + esc(n.title) + '"' + checked + "></td>" +
          '<td><div class="row-title">' + esc(n.title) + "</div></td>" +
          "<td>" + ui.noticeTypeBadge(n.notice_type) + "</td>" +
          "<td>" + esc(ui.formatDateTime(n.published_at)) + "</td>" +
          "<td>" + esc(n.cancel_reason || "—") + "</td>" +
          "<td>" + (n.read_count !== undefined ? n.read_count + " 人" : "—") + "</td>" +
          '<td class="row-actions">' +
            '<button class="btn btn-sm btn-secondary" data-action="n-restore" data-id="' + n.id + '" data-name="' + esc(n.title) + '">恢复</button>' +
            (isAdmin ? '<button class="btn btn-sm btn-danger-outline" data-action="n-purge" data-id="' + n.id + '" data-name="' + esc(n.title) + '">永久删除</button>' : "") +
          "</td></tr>"
        );
      }).join("");
      syncNoticeBulkBar();
    }).catch(function (e) {
      noticeCount.textContent = "加载失败";
      noticeBody.innerHTML = '<tr><td colspan="7"><div class="table-empty text-danger">' + esc(e.message) + "</div></td></tr>";
    });
  }

  if (noticeBody) {
    noticeBody.addEventListener("change", function (e) {
      var cb = e.target.closest(".notice-rb-check");
      if (!cb) return;
      var id = parseInt(cb.dataset.id, 10);
      var row = cb.closest("tr");
      var titleEl = row ? row.querySelector(".row-title") : null;
      if (cb.checked) {
        noticeSelectedIds.add(id);
        noticeSelectedTitles[id] = titleEl ? titleEl.textContent : ("通知 #" + id);
      } else {
        noticeSelectedIds.delete(id);
        delete noticeSelectedTitles[id];
      }
      syncNoticeBulkBar();
    });

    noticeBody.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-action]");
      if (!btn) return;
      var id = parseInt(btn.dataset.id, 10);
      var name = btn.dataset.name || "";

      if (btn.dataset.action === "n-restore") {
        ui.confirmModal({
          title: "恢复通知",
          message: "确定将「" + name + "」恢复到通知列表吗？",
          confirmText: "确认恢复",
        }).then(function (ok) {
          if (!ok) return;
          ui.btnLoading(btn, true, "恢复中…");
          api.post("/api/v1/recycle-bin/notices/" + id + "/restore", {}).then(function () {
            ui.toast("通知已恢复", "success");
            loadNotices();
          }).catch(function (err) {
            ui.toast(err.message, "error");
            ui.btnLoading(btn, false);
          });
        });
        return;
      }

      if (btn.dataset.action === "n-purge") {
        ui.confirmModal({
          title: "彻底删除通知",
          danger: true,
          message: "即将永久删除「" + name + "」及其阅读统计，此操作不可恢复！",
          confirmText: "永久删除",
        }).then(function (ok) {
          if (!ok) return;
          ui.btnLoading(btn, true, "删除中…");
          api.del("/api/v1/recycle-bin/notices/" + id, { confirm: true }).then(function () {
            ui.toast("已彻底删除", "success");
            loadNotices();
          }).catch(function (err) {
            ui.toast(err.message, "error");
            ui.btnLoading(btn, false);
          });
        });
      }
    });
  }

  if (noticeCheckAll) {
    noticeCheckAll.addEventListener("change", function () {
      noticeBody.querySelectorAll(".notice-rb-check").forEach(function (cb) {
        cb.checked = noticeCheckAll.checked;
        var id = parseInt(cb.dataset.id, 10);
        var row = cb.closest("tr");
        var titleEl = row ? row.querySelector(".row-title") : null;
        if (noticeCheckAll.checked) {
          noticeSelectedIds.add(id);
          noticeSelectedTitles[id] = titleEl ? titleEl.textContent : ("通知 #" + id);
        } else {
          noticeSelectedIds.delete(id);
          delete noticeSelectedTitles[id];
        }
      });
      syncNoticeBulkBar();
    });
  }

  if (noticeBulkClearBtn) {
    noticeBulkClearBtn.addEventListener("click", clearNoticeSelection);
  }

  if (noticeBulkRestoreBtn) {
    noticeBulkRestoreBtn.addEventListener("click", async function () {
      var ids = Array.from(noticeSelectedIds);
      if (!ids.length) {
        ui.toast("请先勾选要恢复的通知", "warning");
        return;
      }
      var ok = await ui.confirmModal({
        title: "批量恢复通知",
        message: "确定恢复已选的 " + ids.length + " 条通知吗？废止/替换类通知会重新应用资料时效联动。",
        confirmText: "确认恢复",
      });
      if (!ok) return;
      ui.btnLoading(noticeBulkRestoreBtn, true, "恢复中…");
      try {
        var res = await api.post("/api/v1/recycle-bin/notices/bulk-restore", {
          notice_ids: ids,
        });
        showNoticeBulkResult(res);
        var skipped = (res.skipped || []).length;
        ui.toast("成功 " + (res.succeeded || []).length + " 条，跳过 " + skipped + " 条", "success");
        clearNoticeSelection();
        loadNotices();
        load();
      } catch (err) {
        ui.toast(err.message, "error");
      } finally {
        ui.btnLoading(noticeBulkRestoreBtn, false);
      }
    });
  }

  loadNotices();
  load();
})();