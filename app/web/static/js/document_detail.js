/**
 * document_detail.js —— 资料详情页
 * - 元数据 / 状态（可指定日期重算）/ 文件预览 / 下载 / 关联通知
 * - admin：编辑 / 上传替换文件 / 归档 / 恢复
 * REST：GET /api/v1/documents/{id}、/status、/preview/*、/file、{id}/file(POST)、DELETE、/restore
 */
(function () {
  "use strict";

  var docId = window.DOC_ID;
  var esc = ui.escapeHtml;
  var root = document.getElementById("detail-root");
  var doc = null;
  // 管理视图仅 admin；下载权限 admin 全可（权限模型 v3）
  var isAdmin = ui.isAdmin();
  var me = ui.currentUser();

  /* ---------------- 状态徽章 ---------------- */
  function statusLine(d) {
    var html =
      '<div class="flex gap-2 flex-wrap">' + ui.statusBadge(d.status) +
      '<span class="text-muted" style="font-size:13px;">按 ' +
      esc(ui.formatDate(d.as_of_date)) + " 计算</span></div>";
    return html;
  }

  /* ---------------- 有效性横幅（时效=第一视觉属性，脊线色语义） ---------------- */
  function validityBanner(d) {
    if (d.status === "archived") {
      return '<div class="validity-banner vb-neutral">本资料已删除，当前仅管理员可见（回收站中可恢复）。</div>';
    }
    if (d.status === "expired") {
      return '<div class="validity-banner vb-danger">本资料已于 ' + esc(d.effective_to || "") +
        " 失效，请停止使用；如需最新版本请返回资料列表按分类查找。</div>";
    }
    if (d.status === "pending") {
      return '<div class="validity-banner vb-pending">本资料将于 ' + esc(d.effective_from) +
        " 起生效，当前显示为预览内容。</div>";
    }
    // active：提示即将到期
    if (d.effective_to) {
      var days = Math.ceil((new Date(d.effective_to + "T23:59:59") - Date.now()) / 86400000);
      if (days >= 0 && days <= 30) {
        return '<div class="validity-banner vb-warning">本资料当前有效，但将于 ' + esc(d.effective_to) +
          (days === 0 ? "（今天）" : "（还有 " + days + " 天）") + " 失效，请关注更新版本。</div>";
      }
    }
    return '<div class="validity-banner vb-success">本资料当前有效' +
      (d.effective_to ? "，有效期至 " + esc(d.effective_to) : "，暂无失效日期") + "。</div>";
  }

  /* ---------------- 主渲染 ---------------- */
  function render(d) {
    doc = d;
    document.getElementById("bc-title").textContent = d.title;

    var catPath = "";
    if (d.category) {
      catPath =
        (d.category.parent ? esc(d.category.parent.name) + " / " : "") +
        esc(d.category.name);
    }
    var tags = (d.tags || []).map(function (t) {
      return '<span class="tag-chip">#' + esc(t.name) + "</span>";
    }).join("");

    var adminOps = "";
    if (isAdmin) {
      if (d.status === "archived") {
        adminOps =
          '<button class="btn btn-secondary" id="btn-restore">恢复资料</button>';
      } else {
        adminOps =
          '<a class="btn btn-secondary" href="/admin/documents/' + d.id + "/edit\">编辑资料</a>" +
          '<button class="btn btn-secondary" id="btn-upload-file">上传 / 替换文件</button>' +
          '<button class="btn btn-danger-outline" id="btn-archive">删除资料</button>';
      }
    }

    root.innerHTML =
      '<div class="doc-header">' +
      '<div style="flex:1;min-width:0;">' +
      '<div class="flex gap-2 flex-wrap mb-2">' + ui.statusBadge(d.status) + "</div>" +
      "<h1>" + esc(d.title) + "</h1>" +
      "</div>" +
      '<div class="flex gap-2 flex-wrap">' + adminOps + "</div>" +
      "</div>" +
      validityBanner(d) +
      metaBar(d, catPath, tags) +
      /* 附件区在预览**之上**（2026-11-06 实测调整）：预览 iframe 很高，
         放在下面要把整份 PDF 滚到底才能看见附件，管理员的「替换/删除」
         等于被藏起来了。附件只是一条紧凑横条，放上面不会喧宾夺主。 */
      attachmentSection(d) +
      fileSection(d) +
      statusQuery(d) +
      noticesSection(d) +
      summarySection(d) +
      "";
  }

  /* 元数据收敛：一条紧凑信息条（分类 · 标签 · 生效区间 · 文件 · 版本 · 上传） */
  function metaBar(d, catPath, tags) {
    var items = [];
    items.push("分类：" + (catPath || "未分类"));
    items.push("标签：" + (tags || "未打标签"));
    items.push(
      "生效区间：" + esc(d.effective_from) + " ~ " +
      (d.effective_to ? esc(d.effective_to) : "暂无失效日期")
    );
    if (d.current_file) {
      items.push(
        "文件：" + esc(d.current_file.original_filename) +
        "（" + esc(ui.formatBytes(d.current_file.size_bytes)) + "）"
      );
    }
    items.push("版本：" + esc(d.rev_count !== undefined ? d.rev_count : "—"));
    // 可见范围（2026-11-03）：仅当不是「全体」时才占位置，避免信息噪声
    if (d.audience_scope && d.audience_scope !== "all") {
      items.push("可见范围：" + esc(d.audience_label || "指定对象"));
    }
    items.push("上传：" + esc(ui.formatDateTime(d.created_at)));
    return (
      '<div class="meta-bar">' +
      items.map(function (s) { return "<span>" + s + "</span>"; }).join("") +
      "</div>"
    );
  }

  /* 时效查询：收敛为一行工具 */
  function statusQuery(d) {
    return (
      '<div class="status-query">' +
      '<span class="status-query-label">按日期查询状态</span>' +
      '<input class="form-control" type="date" id="status-date" aria-label="按此日期查询时效状态" value="' + esc(ui.formatDate(d.as_of_date || new Date().toISOString())) + '">' +
      '<button class="btn btn-secondary btn-sm" id="btn-status-query">查询</button>' +
      '<span class="status-result" id="status-result"></span>' +
      "</div>"
    );
  }

  /* ---------------- 附件区（不预览，只下载） ----------------
     2026-11-06：附件是「额外文件槽」，与主文件类型无关，永不预览。
     有附件时主下载按钮指向附件（见 fileSection）。 */
  function attachmentSection(d) {
    var att = d.attachment_file;
    var canEdit = isAdmin && d.status !== "archived";

    if (!att) {
      if (!canEdit) return "";
      return (
        '<div class="card attachment-card">' +
        '<div class="card-body attachment-empty">' +
        '<span class="text-muted">暂无附件（可选：上传 Word / PPT / Excel 等原始文件，仅供下载，不做预览）</span>' +
        '<button class="btn btn-secondary btn-sm" id="btn-upload-attachment">添加附件</button>' +
        "</div></div>"
      );
    }

    var ops = canEdit
      ? '<button class="btn btn-secondary btn-sm" id="btn-replace-attachment">替换</button>' +
        '<button class="btn btn-danger-outline btn-sm" id="btn-delete-attachment">删除</button>'
      : "";

    return (
      '<div class="card attachment-card">' +
      '<div class="card-header"><h3 class="card-title">附件</h3>' +
      '<span class="text-muted" style="font-size:12px;">仅供下载，不做在线预览</span></div>' +
      '<div class="card-body attachment-row">' +
      '<span class="preview-file-type">' + ui.fileTypeBadge(att.file_type) + "</span>" +
      '<span class="attachment-name" title="' + esc(att.original_filename) + '">' +
      esc(att.original_filename) + "</span>" +
      '<span class="text-muted" style="font-size:12px;">' + esc(ui.formatBytes(att.size_bytes)) +
      " · 上传于 " + esc(ui.formatDateTime(att.uploaded_at)) + "</span>" +
      '<span style="flex:1;"></span>' +
      '<a class="btn btn-secondary btn-sm" href="/api/v1/documents/' + d.id +
      '/attachment" title="单独下载：' + esc(att.original_filename) + '">下载此附件（.' +
      esc(att.file_type) + "）</a>" +
      ops +
      "</div></div>"
    );
  }

  /* ---------------- 文件区（预览为主角） ---------------- */
  function fileSection(d) {
    if (!d.current_file) {
      // 创建时文件上传失败 → 带参跳转；显示持久警告并清参（刷新不再重复提示）
      var uploadFailed =
        new URLSearchParams(location.search).get("upload_failed") === "1";
      if (uploadFailed) {
        try {
          history.replaceState(null, "", "/documents/" + docId);
        } catch (e) { /* 特殊环境（如 file://）下忽略 */ }
      }
      return (
        '<div class="card"><div class="card-header"><h3 class="card-title">文件</h3></div>' +
        (uploadFailed
          ? '<div class="alert alert-warning" style="margin:12px 16px 0;">' +
            '<span class="alert-icon">⚠</span>文件上传失败（类型不支持或文件损坏）。' +
            (isAdmin ? "请点击上方「上传 / 替换文件」重新上传。" : "请联系管理员重新上传。") +
            "</div>"
          : "") +
        '<div class="card-body empty-state" style="padding:24px;">' +
        "<p>该资料暂未上传文件" +
        (isAdmin ? '：可点击上方「上传 / 替换文件」' : "") + "</p></div></div>"
      );
    }
    var f = d.current_file;
    /* 下载文案只说明「点下去会得到什么」，不改方案 A 的下载规则：
       /file 仍是默认下载（有附件=附件、无附件=当前主文件），/attachment 仍是附件直链。
       2026-11-08：两个链接都叫「下载原稿附件」会让人分不清默认下载与附件入口，
       工具栏统一用「默认下载：…」开头，并把实际文件名写进按钮。 */
    var att = d.attachment_file;
    var defaultName = att ? att.original_filename : f.original_filename;
    var defaultKind = att ? "原稿附件" : "当前主文件";
    var downloadHtml =
      '<a class="btn btn-primary btn-sm default-download" href="/api/v1/documents/' + d.id +
      '/file" title="默认下载：' + esc(defaultKind) + "：" + esc(defaultName) +
      '">默认下载：' + esc(defaultKind) + "（" + esc(defaultName) + "）</a>";

    return (
      '<div class="preview-card">' +
      '<div class="preview-toolbar">' +
      '<span class="preview-file-type">' + ui.fileTypeBadge(f.file_type) + "</span>" +
      '<span class="preview-file-name" title="' + esc(f.original_filename) + '">' + esc(f.original_filename) + "</span>" +
      '<span class="text-muted" style="font-size:12px;">' + esc(ui.formatBytes(f.size_bytes)) +
      " · 上传于 " + esc(ui.formatDateTime(f.uploaded_at)) + "</span>" +
      '<span style="flex:1;"></span>' +
      downloadHtml +
      "</div>" +
      // data-type 已移除（2026-11-06）：只写不读，类型分派由 renderPreview 用
      // current_file.file_type 直接判断，不需要在 DOM 上留一份冗余契约。
      '<div class="preview-body" id="preview-body">' +
      '<div class="empty-state" style="padding:32px;"><div class="spinner spinner-sm" style="margin:0 auto 8px;"></div><p>正在加载预览…</p></div>' +
      "</div></div>"
    );
  }

  /* 预览失败/未知格式兜底仍只有一个 /file 按钮，但文案要和工具栏一样
     写明默认下载到底会拿到附件还是当前主文件（2026-11-08）。 */
  function defaultDownloadLink(className) {
    var d = doc || {};
    var att = d.attachment_file;
    var f = d.current_file;
    var name = att ? att.original_filename : (f && f.original_filename) || "";
    var kind = att ? "原稿附件" : "当前主文件";
    var label = name
      ? "默认下载：" + kind + "（" + name + "）"
      : "默认下载：" + kind;
    return '<a class="' + className + ' default-download" href="/api/v1/documents/' + docId +
      '/file" title="默认下载：' + esc(name || kind) + '">' + esc(label) + "</a>";
  }

  var IMAGE_TYPES = ["jpg", "jpeg", "png", "gif", "webp"];

  /* 图片预览：与 PDF 同样走 fetch → blob:，避免 <img> 直连时的 Cookie/鉴权差异，
     也让「全屏查看」与 PDF 共用同一套 Fullscreen 逻辑。 */
  function renderImage() {
    var body = document.getElementById("preview-body");
    fetch("/api/v1/documents/" + docId + "/preview/image", { credentials: "same-origin" })
      .then(function (r) {
        var ct = (r.headers.get("content-type") || "").toLowerCase();
        if (!r.ok || ct.indexOf("image/") !== 0) {
          return r.json().catch(function () { return null; }).then(function (payload) {
            var msg = payload && payload.error && payload.error.message;
            throw new Error(msg || "preview unavailable");
          });
        }
        return r.blob();
      })
      .then(function (blob) {
        var img = document.createElement("img");
        img.className = "image-preview";
        img.alt = "图片预览";
        img.src = URL.createObjectURL(blob);
        body.innerHTML =
          '<div class="pdf-preview-bar">' +
          '<span class="text-muted" style="font-size:12px;">图片预览 · 点「全屏查看」整页阅读（Esc 退出）</span>' +
          '<button type="button" class="btn btn-secondary btn-sm" id="image-open-full">全屏查看</button></div>';
        body.appendChild(img);
        var btn = document.getElementById("image-open-full");
        if (btn) {
          btn.addEventListener("click", function () {
            var card = document.querySelector(".preview-card");
            if (!card) return;
            if (document.fullscreenElement) {
              document.exitFullscreen();
            } else if (card.requestFullscreen) {
              card.requestFullscreen();
            }
          });
        }
      })
      .catch(function (err) {
        var hasReason = err && err.message && err.message !== "preview unavailable";
        body.innerHTML =
          '<div class="empty-state" style="padding:32px;">' +
          "<p>该图片暂时无法在线预览。</p>" +
          '<p class="text-muted" style="font-size:13px;">' +
          (hasReason ? "原因：" + esc(err.message) : "可能已损坏或格式异常。") + "</p>" +
          defaultDownloadLink("btn btn-primary") +
          "</div>";
      });
  }

  function renderPdf() {
    var body = document.getElementById("preview-body");
    // 用 fetch 将 PDF 读为 Blob → 创建 blob URL 给 iframe，
    // 避免浏览器内置 PDF 查看器（Chrome PDFium）在 iframe 内发二次请求时不带 cookie 导致 401。
    fetch("/api/v1/documents/" + docId + "/preview/pdf", { credentials: "same-origin" })
      .then(function (r) {
        var ct = (r.headers.get("content-type") || "").toLowerCase();
        if (!r.ok || ct.indexOf("application/pdf") < 0) {
          // 透传后端可读原因（如「文件不存在」「不是 PDF」），取不到再走通用兜底
          return r.json().catch(function () { return null; }).then(function (body) {
            var msg = body && body.error && body.error.message;
            throw new Error(msg || "preview unavailable");
          });
        }
        return r.blob();
      })
      .then(function (blob) {
        var blobUrl = URL.createObjectURL(blob);
        body.innerHTML =
          '<div class="pdf-preview-bar">' +
          '<span class="text-muted" style="font-size:12px;">内嵌预览 · 点「全屏查看」整页阅读（Esc 退出）</span>' +
          '<button type="button" class="btn btn-secondary btn-sm" id="pdf-open-tab">全屏查看</button></div>' +
          '<iframe class="pdf-frame" src="' + blobUrl + '" title="PDF 预览"></iframe>';
        var openTab = document.getElementById("pdf-open-tab");
        if (openTab) {
          // Fullscreen API 页内全屏：不依赖新标签/弹窗策略，所有浏览器可用，
          // 「仅预览」用户同样可全屏（与内嵌预览同一 blob 数据源）
          openTab.addEventListener("click", function () {
            var card = document.querySelector(".preview-card");
            if (!card) return;
            if (document.fullscreenElement) {
              document.exitFullscreen();
            } else if (card.requestFullscreen) {
              card.requestFullscreen();
            }
          });
        }
      })
      .catch(function (err) {
        var hasReason = err && err.message && err.message !== "preview unavailable";
        var reason = hasReason
          ? "原因：" + esc(err.message)
          : "可能已损坏或格式异常。";
        body.innerHTML =
          '<div class="empty-state" style="padding:32px;">' +
          "<p>该文件暂时无法在线预览。</p>" +
          "<p class=\"text-muted\" style=\"font-size:13px;\">" + reason + "</p>" +
          defaultDownloadLink("btn btn-primary") +
          "</div>";
      });
  }

  async function renderDocx() {
    var body = document.getElementById("preview-body");
    try {
      var data = await api.get("/api/v1/documents/" + docId + "/preview/docx");
      ui.renderDocxBlocks(body, data);
    } catch (e) {
      body.innerHTML =
        '<div class="empty-state" style="padding:32px;"><p class="text-danger">' + esc(e.message) +
        '</p>' + defaultDownloadLink("btn btn-secondary btn-sm") + '</div>';
    }
  }

  async function renderXlsx() {
    var body = document.getElementById("preview-body");
    var currentSheet = 0;
    var data = null;

    async function loadSheet(sheet) {
      body.innerHTML =
        '<div class="empty-state" style="padding:32px;"><div class="spinner spinner-sm" style="margin:0 auto 8px;"></div><p>加载工作表…</p></div>';
      try {
        var res = await api.get("/api/v1/documents/" + docId + "/preview/xlsx", {
          sheet: sheet,
        });
        var tabs = (res.sheet_names || []).map(function (name, i) {
          return (
            '<button type="button" class="sheet-tab' + (i === res.active_sheet ? " active" : "") + '" data-sheet="' + i + '">' +
            esc(name) + "</button>"
          );
        }).join("");
        var rowsHtml = (res.rows || [])
          .map(function (row) {
            return "<tr>" + row.map(function (cell) {
              return "<td title=\"" + esc(String(cell === null || cell === undefined ? "" : cell)) + '">' +
                esc(String(cell === null || cell === undefined ? "" : cell)) + "</td>";
            }).join("") + "</tr>";
          })
          .join("");
        body.innerHTML =
          '<div class="sheet-tabs">' + tabs + "</div>" +
          '<div class="xlsx-preview-wrap"><table class="xlsx-table"><thead><tr>' +
          (res.headers || []).map(function (h) {
            return "<th>" + esc(h) + "</th>";
          }).join("") +
          "</tr></thead><tbody>" + rowsHtml + "</tbody></table></div>" +
          '<div class="preview-note">共 ' + esc(res.total_rows) + " 行" +
          (res.truncated ? "，超长已截断" : "") +
          " · 只读预览，请以原文件为准</div>";
        body.querySelectorAll(".sheet-tab").forEach(function (tab) {
          tab.addEventListener("click", function () {
            body.querySelectorAll(".sheet-tab").forEach(function (t) {
              t.classList.remove("active");
            });
            tab.classList.add("active");
            loadSheet(parseInt(tab.dataset.sheet, 10));
          });
        });
      } catch (e) {
        body.innerHTML =
          '<div class="empty-state" style="padding:32px;"><p class="text-danger">' + esc(e.message) +
          '</p>' + defaultDownloadLink("btn btn-secondary btn-sm") + '</div>';
      }
    }
    loadSheet(currentSheet);
  }

  /* 未知类型兜底（2026-11-06）：此前所有非已知类型都被丢给 renderXlsx，
     主文件是 pptx 等格式时会请求 /preview/xlsx 拿到 400，用户看到
     「该文件不是 Excel」这种莫名其妙的提示。 */
  function renderUnsupported(d) {
    var body = document.getElementById("preview-body");
    if (!body) return;
    var t = (d.current_file && d.current_file.file_type) || "";
    body.innerHTML =
      '<div class="empty-state" style="padding:32px;">' +
      "<p>该格式（" + esc(String(t).toUpperCase()) + "）不支持在线预览。当前用于在线预览：" +
      esc(d.current_file.original_filename || "当前主文件") + "。</p>" +
      defaultDownloadLink("btn btn-primary") +
      "</div>";
  }

  function renderPreview(d) {
    if (!d.current_file) return;
    var t = d.current_file.file_type;
    if (t === "pdf") renderPdf();
    else if (t === "docx") renderDocx();
    else if (IMAGE_TYPES.indexOf(t) >= 0) renderImage();
    else if (t === "xlsx" || t === "xls") renderXlsx();
    else renderUnsupported(d);
  }

  /* ---------------- 关联通知 ---------------- */
  function noticesSection(d) {
    var notices = d.linked_notices || [];
    var html =
      '<div class="card">' +
      '<div class="card-header"><h3 class="card-title">关联通知</h3>' +
      '<span class="text-muted" style="font-size:13px;">共 ' + notices.length + " 条</span></div>" +
      '<div class="card-body" style="padding-top:0;padding-bottom:0;">';
    if (!notices.length) {
      html += '<div class="empty-state" style="padding:24px;"><p>暂无关联通知</p></div>';
    } else {
      html += notices.map(function (n) {
        return (
          '<div class="flex-between" style="padding:12px 0;border-bottom:1px solid var(--border);">' +
          '<div><a href="/notices/' + n.id + '">' + esc(n.title) + "</a>" +
          '<div class="row-sub">生效：' + esc(ui.formatDateTime(n.effective_at)) + "</div></div>" +
          "<div>" + ui.noticeTypeBadge(n.notice_type) +
          ui.noticeStatusBadge(n.status) + "</div>" +
          "</div>"
        );
      }).join("");
    }
    html += "</div></div>";
    return html;
  }

  /* ---------------- 资料说明（低调折叠，默认收起） ----------------
     2026-11-11 用户确认：说明不放在标题下、不抢视觉；放在关联通知下面，
     管理员与普通用户都能点开看。这里只是「收起来」，不是权限隐藏。
     summary 仍参与关键词搜索；系统不自动解析文件正文。 */
  function summarySection(d) {
    var summary = String(d.summary || "").trim();
    if (!summary) return "";
    return (
      '<details class="doc-summary-disclosure">' +
      '<summary>资料说明</summary>' +
      '<div class="doc-summary-content">' + esc(summary) + '</div>' +
      '</details>'
    );
  }
  /* ---------------- 管理操作 ---------------- */
  async function setupAdminActions() {
    if (!isAdmin) return;

    var archiveBtn = document.getElementById("btn-archive");
    if (archiveBtn) {
      archiveBtn.addEventListener("click", async function () {
        var reason = await ui.confirmModal({
          title: "删除资料",
          danger: true,
          message: "删除后该资料进入回收站（软删除，不物理删除），普通用户不可见、不可访问；可在「管理 → 回收站」中恢复。确定删除吗？",
          inputLabel: "删除原因（可选）",
          inputPlaceholder: "例如：版本废止",
          confirmText: "确认删除",
        });
        if (reason === false) return;
        ui.btnLoading(archiveBtn, true, "删除中…");
        try {
          await api.del("/api/v1/documents/" + docId, { reason: reason || null });
          ui.toast("资料已删除，可在回收站中恢复", "success");
          setTimeout(function () {
            location.href = "/admin/recycle-bin";
          }, 600);
        } catch (e) {
          ui.toast(e.message, "error");
          ui.btnLoading(archiveBtn, false);
        }
      });
    }

    var restoreBtn = document.getElementById("btn-restore");
    if (restoreBtn) {
      restoreBtn.addEventListener("click", async function () {
        var ok = await ui.confirmModal({
          title: "恢复资料",
          message: "恢复后资料重新对普通用户可见。确定恢复吗？",
          confirmText: "确认恢复",
        });
        if (!ok) return;
        ui.btnLoading(restoreBtn, true, "恢复中…");
        try {
          await api.post("/api/v1/documents/" + docId + "/restore", {});
          ui.toast("资料已恢复", "success");
          setTimeout(function () {
            location.reload();
          }, 600);
        } catch (e) {
          ui.toast(e.message, "error");
          ui.btnLoading(restoreBtn, false);
        }
      });
    }

    var uploadBtn = document.getElementById("btn-upload-file");
    if (uploadBtn) {
      uploadBtn.addEventListener("click", function () {
        var overlay = ui.openModal(
          '<div class="file-upload" id="upload-drop">' +
          '<span class="flex" style="flex:1;min-width:0;">' +
          '<span class="file-icon" style="width:40px;height:40px;">📤</span>' +
          '<span style="flex:1;min-width:0;"><span class="file-name" id="upload-name">点击选择文件（PDF / DOCX / XLSX / XLS / 图片，≤' + (window.MAX_UPLOAD_MB || 50) + 'MB）</span>' +
          '<span class="file-meta">替换文件后旧文件自动进入回收站（不覆盖、不物理删除）；多张图片会按顺序合成一份多页 PDF</span></span></span>' +
          '<input type="file" id="upload-input" multiple accept=".pdf,.docx,.xlsx,.xls,.jpg,.jpeg,.png,.gif,.webp">' +
          "</div>" +
          '<button class="btn btn-primary mt-4" id="upload-submit" style="width:100%;" disabled>确认上传</button>',
          { title: "上传 / 替换文件", size: "sm" }
        );
        var input = overlay.querySelector("#upload-input");
        var drop = overlay.querySelector("#upload-drop");
        var nameEl = overlay.querySelector("#upload-name");
        var submit = overlay.querySelector("#upload-submit");

        input.addEventListener("change", function () {
          var files = Array.prototype.slice.call(input.files || []);
          if (!files.length) return;
          if (files.length === 1) {
            nameEl.textContent = files[0].name + "（" + ui.formatBytes(files[0].size) + "）";
          } else {
            var total = files.reduce(function (s, f) { return s + f.size; }, 0);
            var allImages = files.every(function (f) {
              return /\.(jpe?g|png|gif|webp)$/i.test(f.name);
            });
            nameEl.textContent = files.length + " 个文件（共 " + ui.formatBytes(total) + "）" +
              (allImages
                ? " · 将按选择顺序合成一份多页 PDF"
                : " · 一次只能上传多个图片，请逐个上传其它类型");
          }
          drop.classList.add("has-file");
          submit.disabled = false;
        });
        submit.addEventListener("click", async function () {
          var files = Array.prototype.slice.call(input.files || []);
          if (!files.length) return;
          var fd = new FormData();
          files.forEach(function (f) { fd.append("file", f); });
          ui.btnLoading(submit, true, files.length > 1 ? "合成中…" : "上传中…");
          try {
            var res = await api.upload("/api/v1/documents/" + docId + "/file", fd);
            ui.toast(
              res.merged
                ? res.merge_note + "，旧文件已进入回收站"
                : "文件上传成功" + (res.recycled_file_id ? "，旧文件已进入回收站" : ""),
              "success"
            );
            setTimeout(function () {
              location.reload();
            }, 600);
          } catch (e) {
            ui.toast(e.message, "error");
            ui.btnLoading(submit, false);
          }
        });
      });
    }

    setupAttachmentActions();
  }

  /* 附件上传 / 替换 / 删除（2026-11-06，admin only） */
  function setupAttachmentActions() {
    var accent = ".pdf,.docx,.xlsx,.xls,.pptx,.jpg,.jpeg,.png,.gif,.webp";

    function openAttachmentModal(title, submitLabel) {
      var overlay = ui.openModal(
        '<div class="file-upload" id="att-drop">' +
        '<span class="flex" style="flex:1;min-width:0;">' +
        '<span class="file-icon" style="width:40px;height:40px;">📎</span>' +
        '<span style="flex:1;min-width:0;"><span class="file-name" id="att-name">点击选择文件（PDF / DOCX / XLSX / XLS / PPTX / 图片，≤' +
        (window.MAX_UPLOAD_MB || 50) + "MB）</span>" +
        '<span class="file-meta">附件仅供下载，不参与在线预览；替换后旧附件进入回收站</span></span></span>' +
        '<input type="file" id="att-input" accept="' + accent + '">' +
        "</div>" +
        '<button class="btn btn-primary mt-4" id="att-submit" style="width:100%;" disabled>' +
        submitLabel + "</button>",
        { title: title, size: "sm" }
      );
      var input = overlay.querySelector("#att-input");
      var drop = overlay.querySelector("#att-drop");
      var nameEl = overlay.querySelector("#att-name");
      var submit = overlay.querySelector("#att-submit");

      input.addEventListener("change", function () {
        var f = (input.files || [])[0];
        if (!f) return;
        nameEl.textContent = f.name + "（" + ui.formatBytes(f.size) + "）";
        drop.classList.add("has-file");
        submit.disabled = false;
      });
      submit.addEventListener("click", async function () {
        var f = (input.files || [])[0];
        if (!f) return;
        var fd = new FormData();
        fd.append("file", f);
        ui.btnLoading(submit, true, "上传中…");
        try {
          var res = await api.upload(
            "/api/v1/documents/" + docId + "/attachment", fd
          );
          ui.toast(
            "附件已保存" + (res.recycled_file_id ? "，旧附件已进入回收站" : ""),
            "success"
          );
          setTimeout(function () { location.reload(); }, 600);
        } catch (e) {
          ui.toast(e.message, "error");
          ui.btnLoading(submit, false);
        }
      });
    }

    var addBtn = document.getElementById("btn-upload-attachment");
    if (addBtn) {
      addBtn.addEventListener("click", function () {
        openAttachmentModal("添加附件", "确认上传");
      });
    }
    var replBtn = document.getElementById("btn-replace-attachment");
    if (replBtn) {
      replBtn.addEventListener("click", function () {
        openAttachmentModal("替换附件", "确认替换");
      });
    }
    var delBtn = document.getElementById("btn-delete-attachment");
    if (delBtn) {
      delBtn.addEventListener("click", async function () {
        var ok = await ui.confirmModal({
          title: "删除附件",
          danger: true,
          message: "附件将进入回收站（可在「管理 → 回收站」恢复），资料本身不受影响。确定删除吗？",
          confirmText: "确认删除",
        });
        if (!ok) return;
        ui.btnLoading(delBtn, true, "删除中…");
        try {
          await api.del("/api/v1/documents/" + docId + "/attachment");
          ui.toast("附件已删除，可在回收站中恢复", "success");
          setTimeout(function () { location.reload(); }, 600);
        } catch (e) {
          ui.toast(e.message, "error");
          ui.btnLoading(delBtn, false);
        }
      });
    }
  }

  /* ---------------- 状态查询 ---------------- */
  function setupStatusQuery() {
    var dateInput = document.getElementById("status-date");
    var result = document.getElementById("status-result");
    var btn = document.getElementById("btn-status-query");
    if (!dateInput || !result || !btn) return;
    btn.addEventListener("click", async function () {
      if (!dateInput.value) {
        ui.toast("请选择查询日期", "warning");
        return;
      }
      ui.btnLoading(btn, true, "查询中");
      try {
        var s = await api.get("/api/v1/documents/" + docId + "/status", {
          date: dateInput.value,
        });
        result.innerHTML =
          ui.statusBadge(s.status) +
          '<span class="text-muted">' + esc(s.effective_from) + " ~ " +
          (s.effective_to ? esc(s.effective_to) : "暂无失效日期") + " · 截至 " + esc(s.as_of_date) + "</span>";
      } catch (e) {
        ui.toast(e.message, "error");
      } finally {
        ui.btnLoading(btn, false);
      }
    });
  }

  /* ---------------- 初始化 ---------------- */
  (async function init() {
    try {
      var d = await api.get("/api/v1/documents/" + docId);
      render(d);
      renderPreview(d);
      setupAdminActions();
      setupStatusQuery();
    } catch (e) {
      root.innerHTML =
        '<div class="card"><div class="card-body empty-state">' +
        '<p class="text-danger">' + esc(e.message) + "</p>" +
        '<a class="btn btn-secondary" href="/documents">返回资料列表</a></div></div>';
    }
  })();
})();