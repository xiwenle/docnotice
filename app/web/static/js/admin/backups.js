/**
 * admin/backups.js —— 备份恢复
 * REST：POST /api/v1/backups（type=full|database）
 *      GET  /api/v1/backups、/api/v1/backups/{id}
 *      POST /api/v1/backups/{id}/verify
 *      POST /api/v1/backups/{id}/restore {confirm:true, reason}
 *      DELETE /api/v1/backups/{id} {confirm:true}（仅 admin）
 */
(function () {
  "use strict";

  var esc = ui.escapeHtml;
  var state = { page: 1, page_size: 15 };
  var me = ui.currentUser();
  var isAdmin = ui.isAdmin();

  var tbody = document.getElementById("backup-tbody");
  var pager = document.getElementById("backup-pagination");

  var STATUS_LABELS = {
    completed: '<span class="badge badge-active">完成</span>',
    failed: '<span class="badge badge-expired">失败</span>',
    restored: '<span class="badge badge-info">已用于恢复</span>',
    purged: '<span class="badge badge-cancelled">已删除</span>',
    running: '<span class="badge badge-pending">进行中</span>',
  };

  function buildQuery() {
    return { page: state.page, page_size: state.page_size };
  }

  function renderPolicy(policy) {
    var el = document.getElementById("backup-policy");
    if (!el || !policy) return;
    var html;
    if (policy.schedule) {
      var text = scheduleText(policy.schedule);
      var parts = ["自动备份：<strong>" + esc(text) + "</strong>"];
      if (policy.next_run_at) {
        parts.push("下次执行：" + esc(ui.formatDateTime(policy.next_run_at)));
      }
      var keep = [];
      if (policy.keep_days) keep.push("保留 " + policy.keep_days + " 天");
      if (policy.keep_count) keep.push("最多 " + policy.keep_count + " 份");
      keep.push("恢复留旧目录保留最近 " + esc(policy.pre_keep_count) + " 组");
      parts.push(esc(keep.join("，")) + "，超期自动清理");
      html = '<div class="alert alert-info" style="margin-bottom:16px;"><span>' +
        parts.join(" · ") + "</span></div>";
    } else {
      html = '<div class="alert alert-info" style="margin-bottom:16px;"><span>' +
        "自动备份未启用（未配置 BACKUP_SCHEDULE，见 .env.example）。" +
        (policy.keep_days ? "保留策略：" + policy.keep_days + " 天。" : "") +
        "</span></div>";
    }
    el.innerHTML = html;
  }

  var WEEKDAY_TEXT = {
    mon: "每周一", tue: "每周二", wed: "每周三", thu: "每周四",
    fri: "每周五", sat: "每周六", sun: "每周日", daily: "每天",
  };

  function scheduleText(schedule) {
    var parts = schedule.trim().toLowerCase().split(/\s+/);
    var day = WEEKDAY_TEXT[parts[0]] || parts[0];
    return day + " " + parts[1];
  }

  function render(items) {
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="6"><div class="table-empty">暂无备份记录</div></td></tr>';
      return;
    }
    tbody.innerHTML = items
      .map(function (b) {
        var ops = "";
        if (b.status === "completed") {
          ops =
            '<button class="btn btn-sm btn-ghost" data-action="detail" data-id="' + b.id + '">详情</button>' +
            '<button class="btn btn-sm btn-ghost" data-action="verify" data-id="' + b.id + '">校验</button>' +
            '<button class="btn btn-sm btn-secondary" data-action="restore" data-id="' + b.id + '" data-name="#' + b.id + '">恢复</button>';
          if (isAdmin) {
            ops +=
              '<button class="btn btn-sm btn-danger-outline" data-action="delete" data-id="' + b.id + '" data-name="#' + b.id + '">删除</button>';
          }
        } else {
          ops = '<button class="btn btn-sm btn-ghost" data-action="detail" data-id="' + b.id + '">详情</button>';
        }
        var sourceBadge =
          b.source === "auto" ? ' <span class="badge badge-info">自动</span>' : "";
        return (
          "<tr>" +
          "<td>" + (b.backup_type === "database" ? '<span class="tag-chip">仅数据库</span>' : '<span class="tag-chip">完整备份</span>') + sourceBadge + "</td>" +
          "<td>" + esc(ui.formatBytes(b.size_bytes)) + "</td>" +
          "<td>" + esc(b.file_count !== undefined ? b.file_count : "—") + "</td>" +
          "<td>" + (STATUS_LABELS[b.status] || esc(b.status)) + "</td>" +
          '<td class="text-muted" style="font-size:13px;">' +
          esc(b.created_by_username || b.created_by || "") + "<br>" + esc(ui.formatDateTime(b.created_at)) + "</td>" +
          '<td class="row-actions">' + ops + "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  function renderVerifyResult(result, id) {
    var checks = result.checks || {};
    var labels = [
      ["manifest", "manifest"],
      ["db_sha256", "数据库 sha256"],
      ["file_sha256", "业务文件 sha256"],
      ["sqlite_integrity_check", "SQLite integrity_check"],
    ];
    var rows = labels.map(function (pair) {
      var check = checks[pair[0]] || {};
      var badge = check.ok
        ? '<span class="badge badge-active">通过</span>'
        : '<span class="badge badge-expired">失败</span>';
      var note = check.message || "";
      if (pair[0] === "file_sha256" && check.checked !== undefined) {
        note += "（已检查 " + check.checked + " 个文件）";
      }
      if (pair[0] === "sqlite_integrity_check" && check.result) {
        note += "：" + check.result;
      }
      return '<div class="meta-item"><div class="meta-label">' + esc(pair[1]) +
        "</div><div class='meta-value'>" + badge +
        '<div class="text-muted" style="font-size:12px;margin-top:4px;">' + esc(note) + "</div></div></div>";
    }).join("");
    var errors = (result.errors || []).length
      ? '<div class="alert alert-warning" style="margin-top:12px;"><span>' +
        (result.ok ? "校验通过" : "校验未通过") + "：" + esc((result.errors || []).join("；")) +
        "</span></div>"
      : '<div class="alert alert-info" style="margin-top:12px;"><span>全部校验通过，可进一步用于恢复。</span></div>';
    ui.openModal(
      '<div class="doc-meta-grid">' +
      '<div class="meta-item"><div class="meta-label">结果</div><div class="meta-value">' +
      (result.ok ? '<span class="badge badge-active">通过</span>' : '<span class="badge badge-expired">未通过</span>') +
      "</div></div>" +
      '<div class="meta-item"><div class="meta-label">校验时间</div><div class="meta-value">' +
      esc(ui.formatDateTime(result.checked_at)) + "</div></div>" +
      rows +
      "</div>" + errors +
      '<div class="mt-3"><h4 style="font-size:14px;">完整校验结果</h4><pre style="background:var(--bg-hover);padding:12px;border-radius:8px;font-size:12px;overflow:auto;max-height:280px;white-space:pre-wrap;word-break:break-word;">' +
      esc(JSON.stringify(result, null, 2)) + "</pre></div>",
      { title: "备份校验 #" + id, size: "lg" }
    );
  }

  async function load() {
    tbody.innerHTML = '<tr><td colspan="6"><div class="table-empty">加载中…</div></td></tr>';
    try {
      var data = await api.get("/api/v1/backups", buildQuery());
      renderPolicy(data.policy);
      render(data.items);
      ui.renderPagination(pager, data.page, data.pages, function (p) {
        state.page = p;
        load();
      });
    } catch (e) {
      tbody.innerHTML = '<tr><td colspan="6"><div class="table-empty text-danger">' + esc(e.message) + "</div></td></tr>";
    }
  }

  document.getElementById("btn-create-backup").addEventListener("click", async function () {
    var btn = document.getElementById("btn-create-backup");
    var type = document.getElementById("backup-type").value;
    ui.btnLoading(btn, true, "备份中…（同步执行，请稍候）");
    try {
      var res = await api.post("/api/v1/backups", { type: type });
      // 2026-09-14：仅库备份没有业务文件，显示「0 个文件」会让人误以为备份为空
      ui.toast(
        "备份完成 #" + res.id +
          (type === "full" && res.file_count !== undefined
            ? "（" + res.file_count + " 个文件）"
            : "（仅数据库）"),
        "success"
      );
      state.page = 1;
      load();
    } catch (e) {
      ui.toast(e.message, "error");
    } finally {
      ui.btnLoading(btn, false);
    }
  });

  tbody.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-action]");
    if (!btn) return;
    var id = parseInt(btn.dataset.id, 10);

    if (btn.dataset.action === "detail") {
      api
        .get("/api/v1/backups/" + id)
        .then(function (b) {
          var manifest = b.manifest || null;
          var overlay = ui.openModal(
            '<div class="doc-meta-grid">' +
            '<div class="meta-item"><div class="meta-label">备份类型</div><div class="meta-value">' + esc(b.backup_type) + "</div></div>" +
            '<div class="meta-item"><div class="meta-label">大小</div><div class="meta-value">' + esc(ui.formatBytes(b.size_bytes)) + "</div></div>" +
            '<div class="meta-item"><div class="meta-label">文件数</div><div class="meta-value">' + esc(b.file_count) + "</div></div>" +
            '<div class="meta-item"><div class="meta-label">数据库指纹</div><div class="meta-value" style="font-size:12px;">' + esc((b.db_sha256 || "").slice(0, 24)) + "…</div></div>" +
            '<div class="meta-item"><div class="meta-label">备份路径</div><div class="meta-value" style="font-size:12px;">' + esc(b.backup_path || "—") + "</div></div>" +
            '<div class="meta-item"><div class="meta-label">创建时间</div><div class="meta-value">' + esc(ui.formatDateTime(b.created_at)) + "</div></div>" +
            "</div>" +
            (manifest
              ? '<div class="mt-3"><h4 style="font-size:14px;">manifest 摘要</h4><pre style="background:var(--bg-hover);padding:12px;border-radius:8px;font-size:12px;overflow:auto;max-height:220px;">' +
                esc(typeof manifest === "string" ? manifest : JSON.stringify(manifest, null, 2)) +
                "</pre></div>"
              : ""),
            { title: "备份详情 #" + id, size: "lg" }
          );
        })
        .catch(function (err) {
          ui.toast(err.message, "error");
        });
      return;
    }

    if (btn.dataset.action === "verify") {
      (async function () {
        ui.btnLoading(btn, true, "校验中…");
        try {
          var result = await api.post("/api/v1/backups/" + id + "/verify", {});
          ui.btnLoading(btn, false);
          renderVerifyResult(result, id);
          ui.toast(result.ok ? "备份校验通过" : "备份校验未通过", result.ok ? "success" : "error");
        } catch (err) {
          ui.btnLoading(btn, false);
          ui.toast(err.message, "error");
        }
      })();
      return;
    }

    if (btn.dataset.action === "restore") {
      // 一步完成：危险确认 + 可选原因（不再要求打字，也不再弹第二次原因窗）
      ui.confirmModal({
        title: "恢复备份",
        danger: true,
        message:
          "将用备份 #" + id +
          " 覆盖当前数据库与文件；系统会先自动生成当前状态安全备份。恢复期间请避免并发操作。<br><strong>此操作不可直接撤销</strong>（可再恢复其它备份）。",
        inputLabel: "恢复原因（可选，写入审计日志）",
        inputPlaceholder: "例如：误删数据回滚",
        confirmText: "开始恢复",
      }).then(async function (answer) {
        if (answer === false) return;
        ui.btnLoading(btn, true, "恢复中…（请勿刷新页面）");
        try {
          await api.post("/api/v1/backups/" + id + "/restore", {
            confirm: true,
            reason: (answer || "").trim() || "未填写",
          });
          ui.toast("恢复完成", "success");
          setTimeout(function () {
            location.reload();
          }, 800);
        } catch (err) {
          ui.toast(err.message, "error");
          ui.btnLoading(btn, false);
        }
      });
      return;
    }

    if (btn.dataset.action === "delete") {
      ui.confirmModal({
        title: "删除备份",
        danger: true,
        message: "将物理删除备份 #" + id + " 的备份目录，不可恢复。",
        confirmText: "删除备份",
      }).then(async function (ok) {
        if (!ok) return;
        ui.btnLoading(btn, true, "删除中…");
        try {
          await api.del("/api/v1/backups/" + id, { confirm: true });
          ui.toast("备份已删除", "success");
          load();
        } catch (err) {
          ui.toast(err.message, "error");
          ui.btnLoading(btn, false);
        }
      });
    }
  });

  load();
})();