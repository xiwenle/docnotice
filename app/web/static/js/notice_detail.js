/**
 * notice_detail.js —— 通知详情页
 * - 打开详情即 POST /notices/{id}/read 记录 read_at（§20）
 * - 文件预览/下载、关联资料、audience_meta 展示（admin）
 * REST：GET/POST /api/v1/notices/{id}、/{id}/read、/{id}/file、/{id}/preview/*
 */
(function () {
  "use strict";

  var noticeId = window.NOTICE_ID;
  var esc = ui.escapeHtml;
  var root = document.getElementById("detail-root");
  // 管理视图仅 admin（权限模型 v3）
  var isAdmin = ui.isAdmin();

  /* ---------------- 渲染 ---------------- */
  function render(d) {
    document.getElementById("bc-title").textContent = d.title;

    var targetDesc = "";
    if (d.audience_meta) {
      var m = d.audience_meta;
      // 优先显示名称（后端 audience_meta.*_names）；旧缓存/已删除对象回退 ID
      function namesOrIds(names, ids) {
        return (names && names.length ? names : ids || []).join("、");
      }
      if (m.scope === "all") targetDesc = "全体用户";
      else if (m.scope === "department") targetDesc = "指定部门（" + namesOrIds(m.department_names, m.department_ids) + "）";
      else if (m.scope === "user") targetDesc = "指定用户（" + namesOrIds(m.user_names, m.user_ids) + "）";
    }

    var adminActions = "";
    if (isAdmin) {
      adminActions =
        // 2026-11-11：草稿状态下线后，正常通知均创建即发布
        (d.status === "published"
          ? '<a class="btn btn-secondary btn-sm" href="/admin/notices/' + d.id + '/edit">编辑</a>' +
            '<a class="btn btn-secondary btn-sm" href="/admin/notices/' + d.id + '/stats">阅读统计</a>' +
            '<button class="btn btn-danger-outline btn-sm" id="btn-cancel">删除</button>'
          : '<a class="btn btn-secondary btn-sm" href="/admin/notices/' + d.id + '/stats">阅读统计</a>');
    }

    var publishedLine =
      "创建 " + esc(ui.formatDateTime(d.published_at)) +
      (d.effective_at ? " · 生效 " + esc(ui.formatDateTime(d.effective_at)) : " · 创建即生效");

    var statusCard =
      '<section class="card rail-card">' +
      '<div class="card-header"><h3 class="card-title">我的状态</h3>' +
      (d.in_audience === false ? '<span class="badge">非阅读对象</span>' : ui.readStatusBadge(d.my_read_status || "unread")) +
      "</div>" +
      '<div class="card-body"><dl class="rail-list">' +
      railItem("查看时间", esc(ui.formatDateTime(d.my_read_at))) +
      "</dl></div></section>";

    var metaCard =
      '<section class="card rail-card">' +
      '<div class="card-header"><h3 class="card-title">时间与对象</h3></div>' +
      '<div class="card-body"><dl class="rail-list">' +
      railItem("创建时间", esc(ui.formatDateTime(d.published_at))) +
      railItem("生效时间", d.effective_at ? esc(ui.formatDateTime(d.effective_at)) : "创建即生效") +
      (d.deadline_at ? railItem("截止时间", esc(ui.formatDateTime(d.deadline_at))) : "") +
      railItem("阅读对象", esc(targetDesc || "—")) +
      "</dl></div></section>";

    var adminCard = isAdmin
      ? '<section class="card rail-card">' +
        '<div class="card-header"><h3 class="card-title">管理操作</h3></div>' +
        '<div class="card-body rail-actions">' + adminActions + "</div></section>"
      : "";

    // 2026-11-03：确认条已移除（确认功能下线，只保留「已查看」状态展示）
    var actionBar = "";

    root.innerHTML =
      '<div class="doc-header">' +
      '<div style="flex:1;min-width:0;">' +
      '<div class="flex gap-2 flex-wrap mb-2">' +
      ui.noticeTypeBadge(d.notice_type) +
      (isAdmin ? ui.noticeStatusBadge(d.status) : "") +
      // 非阅读对象不显示「未查看」：打开详情不置读，该徽章会误导为待办
      (d.in_audience === false ? "" : ui.readStatusBadge(d.my_read_status || "unread")) +
      "</div>" +
      "<h1>" + esc(d.title) + "</h1>" +
      '<p class="detail-sub">' + publishedLine + "</p>" +
      "</div>" +
      "</div>" +

      '<div class="notice-layout">' +
      '<div class="notice-main">' +
      '<div class="card">' +
      '<div class="card-header"><h3 class="card-title">通知内容</h3></div>' +
      '<div class="notice-content">' +
      (d.content ? esc(d.content) : '<span class="text-muted">（纯文件通知，无文字正文）</span>') +
      "</div>" +
      (d.has_file ? fileBar(d) : "") +
      "</div>" +
      linkedDocsSection(d) +
      "</div>" +
      '<aside class="notice-rail">' +
      (isAdmin ? adminCard + statusCard : actionBar + statusCard) +
      metaCard +
      (isAdmin ? actionBar : "") +
      "</aside>" +
      "</div>";
  }

  function railItem(label, value) {
    return "<div><dt>" + esc(label) + "</dt><dd>" + value + "</dd></div>";
  }

  function fileBar(d) {
    var type = (d.file_type || "").toLowerCase();
    var previewBtn = "";
    if (type === "pdf") {
      previewBtn = '<button class="btn btn-primary btn-sm" id="btn-preview-pdf">在线预览 PDF</button>';
    } else if (type === "docx") {
      previewBtn = '<button class="btn btn-primary btn-sm" id="btn-preview-docx">在线预览 DOCX</button>';
    } else if (type === "xlsx" || type === "xls") {
      previewBtn = '<button class="btn btn-primary btn-sm" id="btn-preview-xlsx">在线预览 Excel</button>';
    } else if (["jpg", "jpeg", "png", "gif", "webp"].indexOf(type) >= 0) {
      previewBtn = '<button class="btn btn-primary btn-sm" id="btn-preview-image">在线预览图片</button>';
    }
    var hint = previewBtn
      ? '点击「在线预览」查看通知文件，或点「下载通知文件」保存到本地'
      : "该格式不支持在线预览，请下载通知文件查看";
    return (
      '<div class="preview-toolbar" style="border-top:1px solid var(--border);">' +
      '<span>附件：' + ui.fileTypeBadge(d.file_type) + '</span>' +
      '<span style="flex:1;"></span>' +
      '<a class="btn btn-secondary btn-sm" href="/api/v1/notices/' + d.id + '/file">下载通知文件</a>' +
      "</div>" +
      // data-type 已移除（2026-11-06）：只写不读，无消费方。
      '<div class="preview-body" id="preview-body">' +
      '<div class="empty-state" style="padding:24px;">' +
      '<p style="margin-bottom:12px;">' + esc(hint) + "</p>" +
      '<div class="flex gap-2" style="justify-content:center;">' +
      previewBtn +
      '<a class="btn btn-secondary btn-sm" href="/api/v1/notices/' + d.id + '/file">下载通知文件</a>' +
      "</div></div></div>"
    );
  }

  function linkedDocsSection(d) {
    var docs = d.documents || [];
    if (!docs.length) {
      return '<div class="linked-empty">未关联资料</div>';
    }
    var html =
      '<div class="card">' +
      '<div class="card-header"><h3 class="card-title">关联资料</h3>' +
      '<span class="text-muted" style="font-size:13px;">共 ' + docs.length + " 个</span></div>" +
      '<div class="card-body" style="padding-top:0;padding-bottom:0;">';
    html += docs
        .map(function (x) {
          return (
            '<div style="padding:10px 0;border-bottom:1px solid var(--border);">' +
            '<a href="/documents/' + x.id + '">' + esc(x.title) + "</a>" +
            (x.status ? '<span style="margin-left:8px;">' + ui.statusBadge(x.status) + "</span>" : "") +
            "</div>"
          );
        })
        .join("");
    html += "</div></div>";
    return html;
  }

  /* ---------------- 初始化 ---------------- */
  async function init() {
    try {
      var d = await api.get("/api/v1/notices/" + noticeId);
      render(d);

      // 1) 自动标记已查看（§20：打开详情即记录 read_at；幂等）
      if (d.my_read_status === "unread" && d.status === "published") {
        try {
          await api.post("/api/v1/notices/" + noticeId + "/read", {});
          d = await api.get("/api/v1/notices/" + noticeId);
          render(d);  // 徽章/查看时间即时更新
        } catch (e) {
          // 非目标/状态非 published 时后端会 403（页面可见性由后端保证）；静默
        }
      }

      // 3) 预览按钮
      var previewPdfBtn = document.getElementById("btn-preview-pdf");
      if (previewPdfBtn) {
        previewPdfBtn.addEventListener("click", async function () {
          var previewBody = document.getElementById("preview-body");
          if (!previewBody) return;
          ui.btnLoading(previewPdfBtn, true, "加载预览…");
          try {
            // 用 fetch 将 PDF 读为 Blob → blob URL 给 iframe，
            // 避免浏览器内置 PDF 查看器在 iframe 内二次请求不带 cookie 导致 401。
            var r = await fetch("/api/v1/notices/" + noticeId + "/preview/pdf", { credentials: "same-origin" });
            var ct = (r.headers.get("content-type") || "").toLowerCase();
            if (!r.ok || ct.indexOf("application/pdf") < 0) {
              throw new Error("预览不可用");
            }
            var blob = await r.blob();
            var blobUrl = URL.createObjectURL(blob);
            previewBody.innerHTML = '<iframe src="' + blobUrl + '" style="width:100%;height:calc(100vh - 230px);min-height:560px;border:none;"></iframe>';
          } catch (e) {
            previewBody.innerHTML =
              '<div class="empty-state" style="padding:32px;">' +
              "<p>该附件暂时无法在线预览（可能已损坏或格式异常），请下载通知文件查看。</p>" +
              '<a class="btn btn-primary" href="/api/v1/notices/' + noticeId + '/file">下载通知文件</a>' +
              "</div>";
            ui.btnLoading(previewPdfBtn, false);
          }
        });
      }
      var previewImageBtn = document.getElementById("btn-preview-image");
      if (previewImageBtn) {
        previewImageBtn.addEventListener("click", async function () {
          var previewBody = document.getElementById("preview-body");
          if (!previewBody) return;
          ui.btnLoading(previewImageBtn, true, "加载预览…");
          try {
            var r = await fetch("/api/v1/notices/" + noticeId + "/preview/image",
                                { credentials: "same-origin" });
            var ct = (r.headers.get("content-type") || "").toLowerCase();
            if (!r.ok || ct.indexOf("image/") !== 0) throw new Error("预览不可用");
            var blob = await r.blob();
            previewBody.innerHTML = "";
            var img = document.createElement("img");
            img.className = "image-preview";
            img.alt = "图片预览";
            img.src = URL.createObjectURL(blob);
            previewBody.appendChild(img);
          } catch (e) {
            previewBody.innerHTML =
              '<div class="empty-state" style="padding:32px;">' +
              "<p>该图片暂时无法在线预览（可能已损坏或格式异常），请下载通知文件查看。</p>" +
              '<a class="btn btn-primary" href="/api/v1/notices/' + noticeId + '/file">下载通知文件</a>' +
              "</div>";
            ui.btnLoading(previewImageBtn, false);
          }
        });
      }
      var previewDocxBtn = document.getElementById("btn-preview-docx");
      if (previewDocxBtn) {
        previewDocxBtn.addEventListener("click", async function () {
          var previewBody = document.getElementById("preview-body");
          if (!previewBody) return;
          ui.btnLoading(previewDocxBtn, true, "加载预览…");
          try {
            var res = await api.get("/api/v1/notices/" + noticeId + "/preview/docx");
            ui.renderDocxBlocks(previewBody, res);
          } catch (e) {
            ui.toast("预览失败：" + e.message, "error");
            ui.btnLoading(previewDocxBtn, false);
          }
        });
      }
      var previewXlsxBtn = document.getElementById("btn-preview-xlsx");
      if (previewXlsxBtn) {
        previewXlsxBtn.addEventListener("click", async function () {
          var previewBody = document.getElementById("preview-body");
          if (!previewBody) return;
          ui.btnLoading(previewXlsxBtn, true, "加载预览…");
          try {
            var res = await api.get("/api/v1/notices/" + noticeId + "/preview/xlsx");
            var html = '<table style="width:100%;border-collapse:collapse;">';
            html += '<thead><tr>';
            for (var i = 0; i < res.headers.length; i++) {
              html += '<th style="border:1px solid #ddd;padding:8px;background:#f8f9fa;">' + esc(res.headers[i]) + '</th>';
            }
            html += '</tr></thead><tbody>';
            for (var r = 0; r < res.rows.length; r++) {
              html += '<tr>';
              for (var c = 0; c < res.rows[r].length; c++) {
                html += '<td style="border:1px solid #ddd;padding:8px;">' + esc(res.rows[r][c]) + '</td>';
              }
              html += '</tr>';
            }
            html += '</tbody></table>';
            if (res.truncated) {
              html += '<p style="color:#666;margin-top:8px;">（仅显示前 ' + res.rows.length + ' 行）</p>';
            }
            previewBody.innerHTML = html;
          } catch (e) {
            ui.toast("预览失败：" + e.message, "error");
            ui.btnLoading(previewXlsxBtn, false);
          }
        });
      }

      // 4) 管理操作
      if (isAdmin) {
        // 2026-11-04：草稿下线，发布按钮与删除草稿逻辑一并移除
        var cancel = document.getElementById("btn-cancel");
        if (cancel) {
          cancel.addEventListener("click", async function () {
            var ok = await ui.confirmModal({
              title: "删除通知",
              danger: true,
              message: "删除后该通知将移至回收站，可在那里恢复。确定吗？",
              confirmText: "确认删除",
            });
            if (!ok) return;
            ui.btnLoading(cancel, true, "删除中…");
            try {
              await api.post("/api/v1/notices/" + noticeId + "/cancel", {});
              ui.toast("通知已移至回收站", "success");
              setTimeout(function () {
                location.href = "/notices";
              }, 800);
            } catch (e) {
              ui.toast(e.message, "error");
              ui.btnLoading(cancel, false);
            }
          });
        }
      }
    } catch (e) {
      root.innerHTML =
        '<div class="card"><div class="card-body empty-state">' +
        '<p class="text-danger">' + esc(e.message) + "</p>" +
        '<a class="btn btn-secondary" href="/notices">返回通知列表</a></div></div>';
    }
  }

  init();
})();