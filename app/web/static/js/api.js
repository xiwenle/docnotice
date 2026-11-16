/**
 * api.js —— REST 统一封装（docs/API.md §0）
 *
 * - 同源 Cookie 自动携带（HttpOnly，SameSite=Lax），无需手动加 Authorization
 * - 统一错误解析：{error:{code,message,detail}} → ApiError，并按错误码映射中文提示
 * - 401 自动跳转登录页（携带 next 回跳参数）
 *
 * 用法：
 *   api.get("/api/v1/documents", {page:1})   → GET /api/v1/documents?page=1
 *   api.post("/api/v1/auth/login", {..})      → JSON body
 *   api.put("/api/v1/documents/1", {..})
 *   api.del("/api/v1/tags/3")
 *   api.upload("/api/v1/documents/1/file", formData)
 *   api.download 由 <a href> 直接承担（同源 Cookie）
 */
(function (global) {
  "use strict";

  /** 错误码 → 中文可读提示（服务端 message 优先，此为兜底映射） */
  var ERROR_MESSAGES = {
    UNAUTHORIZED: "未登录或登录已失效，请重新登录",
    FORBIDDEN: "没有权限执行此操作",
    NOT_FOUND: "请求的资源不存在",
    VALIDATION_ERROR: "提交的数据有误，请检查后重试",
    VALIDITY_CONFLICT: "时效区间冲突：系统默认阻止保存；如确需覆盖请勾选“确认覆盖”并填写修改原因",
    NOTICE_MODIFY_REQUIRES_REASON:
      "通知存在阅读记录，修改必须勾选确认并填写修改原因",
    FILE_TYPE_NOT_ALLOWED: "不支持的文件类型（仅支持 PDF / DOCX / XLSX / XLS / 图片）",
    FILE_TYPE_MISMATCH: "文件内容与扩展名不符，已拒绝上传",
    FILE_TOO_LARGE: "文件超过大小限制，上传失败",
    DUPLICATE: "名称已存在，请更换后重试",
    IN_USE: "该对象仍被引用，无法删除",
    CONFLICT: "操作与当前状态冲突",
    BAD_REQUEST: "请求无效，请检查后重试",
    PASSWORD_CHANGE_REQUIRED: "首次登录或密码重置后必须修改密码",
    INTERNAL_ERROR: "服务器内部错误，请稍后重试",
    HTTP_ERROR: "请求失败，请稍后重试",
  };

  /** 业务错误对象 */
  function ApiError(code, message, detail, status) {
    this.name = "ApiError";
    this.code = code || "HTTP_ERROR";
    this.message = message || ERROR_MESSAGES[this.code] || "请求失败";
    this.detail = detail || null;
    this.status = status || 0;
  }
  ApiError.prototype = Object.create(Error.prototype);
  ApiError.prototype.constructor = ApiError;

  function friendly(code) {
    return ERROR_MESSAGES[code] || "请求失败，请稍后重试";
  }

  /** 查询参数序列化（跳过 null/undefined/空串） */
  function buildQuery(params) {
    if (!params) return "";
    var parts = [];
    Object.keys(params).forEach(function (k) {
      var v = params[k];
      if (v === null || v === undefined || v === "") return;
      if (Array.isArray(v)) {
        // API 契约中数组参数均为 JSON 字符串（如 document_ids），此处保留原样
        parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(v.join(",")));
      } else {
        parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(v));
      }
    });
    return parts.length ? "?" + parts.join("&") : "";
  }

  /** 自动跳转登录页（仅浏览器页面环境） */
  function redirectToLogin() {
    // 2026-09-29 修复：原先用 pathname+search 拼出的 next 与 "/login" 比较，
    // 当 URL 已是 /login?next=/documents 时二者永不相等 → 登录失败（服务端按契约
    // 返回 401）会被再次重定向到 /login?next=%2Flogin%3Fnext%3D...，页面重载，
    // 错误提示一闪而过、用户看不到。判据只应看「是否已在登录页」。
    if (location.pathname === "/login") return;
    var next = location.pathname + location.search;
    location.href = "/login?next=" + encodeURIComponent(next);
  }

  /** 核心 fetch 封装 */
  async function request(path, options) {
    var opts = {
      credentials: "same-origin",
      method: "GET",
      headers: {},
      ...options,
    };
    // JSON body 自动设置 Content-Type；FormData/文件上传不设（浏览器带 boundary）
    if (opts.body && !(opts.body instanceof FormData) && typeof opts.body === "object") {
      opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers);
      opts.body = JSON.stringify(opts.body);
    }
    if (opts.query) {
      path = path + buildQuery(opts.query);
    }

    var resp;
    try {
      resp = await fetch(path, opts);
    } catch (e) {
      throw new ApiError("NETWORK", "网络连接失败，请检查网络后重试", null, 0);
    }

    var data = null;
    var text = await resp.text();
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (e) {
        data = null; // 非 JSON（如文件流被错误读取），按 HTTP 状态处理
      }
    }

    if (resp.status === 401) {
      // 页面会话失效：跳转登录页。
      // 2026-09-14 缺陷修复：少数接口用 401 表达「业务性拒绝」而非会话失效
      // （如改密时原密码错误 → 服务端按契约返回 401 UNAUTHORIZED）。这类调用
      // 传 noAuthRedirect:true，由页面就地提示，不能把用户踢到登录页。
      if (!opts.noAuthRedirect) {
        redirectToLogin();
      }
      throw new ApiError(
        data && data.error ? data.error.code : "UNAUTHORIZED",
        (data && data.error && data.error.message) || ERROR_MESSAGES.UNAUTHORIZED,
        data && data.error ? data.error.detail : null,
        resp.status
      );
    }

    if (!resp.ok) {
      var err = data && data.error ? data.error : {};
      // 2026-10-16 P0 修复联动：服务端改密闸门命中（长开页面里管理员重置了密码），
      // 就地跳转改密页，而不是把用户晾在 raw 403 提示上
      if (err.code === "PASSWORD_CHANGE_REQUIRED" && location.pathname !== "/change-password") {
        location.href = "/change-password";
      }
      throw new ApiError(
        err.code || "HTTP_ERROR",
        err.message || friendly(err.code),
        err.detail || null,
        resp.status
      );
    }
    return data === null ? { ok: true } : data;
  }

  var api = {
    ERROR_MESSAGES: ERROR_MESSAGES,
    ApiError: ApiError,
    buildQuery: buildQuery,
    get: function (path, query) {
      return request(path, { method: "GET", query: query });
    },
    post: function (path, body, query, options) {
      // 注意：path 必须显式透传。曾因 Object.assign 把 path 吞掉，
      // 导致全站 POST 变成 fetch([object Object])（2026-09-14 回归，已修）。
      return request(path, Object.assign(
        { method: "POST", body: body || {}, query: query }, options || {}));
    },
    put: function (path, body, query) {
      return request(path, { method: "PUT", body: body || {}, query: query });
    },
    del: function (path, body) {
      // 部分 DELETE 带请求体（如文档归档 reason、回收站 confirm）
      return request(path, {
        method: "DELETE",
        body: body === undefined ? undefined : body || {},
      });
    },
    upload: function (path, formData) {
      return request(path, { method: "POST", body: formData });
    },
    uploadPut: function (path, formData) {
      return request(path, { method: "PUT", body: formData });
    },
  };

  global.api = api;
})(window);