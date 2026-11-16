/**
 * login.js —— 登录页
 * POST /api/v1/auth/login（成功写 HttpOnly Cookie）→ 跳转 next 或 /
 */
(function () {
  "use strict";

  var form = document.getElementById("login-form");
  var username = document.getElementById("username");
  var password = document.getElementById("password");
  var submitBtn = document.getElementById("login-submit");
  var errBox = document.getElementById("login-error");
  var errText = document.getElementById("login-error-text");

  function showError(msg) {
    errText.textContent = msg || "登录失败，请重试";
    errBox.classList.remove("hidden");
  }

  function clearError() {
    errBox.classList.add("hidden");
  }

  function safeNextPath(raw) {
    if (!raw || raw.length > 2048) return "/";
    for (var i = 0; i < raw.length; i += 1) {
      var code = raw.charCodeAt(i);
      if (code < 32 || code === 127) return "/";
    }
    if (raw.charAt(0) !== "/" || raw.charAt(1) === "/") return "/";
    if (raw.indexOf("\\") !== -1 || raw.indexOf("#") !== -1) return "/";

    var rawPath = raw.split("?", 1)[0];
    try {
      var parsed = new URL(raw, window.location.origin);
      if (parsed.origin !== window.location.origin) return "/";
      if (parsed.pathname !== rawPath) return "/";
      if (parsed.pathname !== "/documents" && parsed.pathname !== "/notices") {
        return "/";
      }
      return parsed.pathname + parsed.search;
    } catch (err) {
      return "/";
    }
  }

  function nextPath() {
    var p = new URLSearchParams(location.search).get("next");
    return safeNextPath(p);
  }

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    clearError();

    var u = username.value.trim();
    var pw = password.value;
    if (!u) {
      username.closest(".form-group").classList.add("has-error");
      return;
    }
    if (!pw) {
      password.closest(".form-group").classList.add("has-error");
      return;
    }

    ui.btnLoading(submitBtn, true, "登录中…");
    try {
      // noAuthRedirect：登录接口的 401 是「账号或密码错误」的业务拒绝，不是会话失效。
      // 不做自动跳转，就地展示错误（与 change_password.js 同口径）。
      var res = await api.post(
        "/api/v1/auth/login",
        { username: u, password: pw },
        null,
        { noAuthRedirect: true }
      );
      window.location.href =
        res && res.must_change_password ? "/change-password" : nextPath();
    } catch (err) {
      ui.btnLoading(submitBtn, false);
      if (err.code === "UNAUTHORIZED") {
        showError("用户名或密码错误（或账号已被停用）");
      } else {
        showError(err.message);
      }
    }
  });

  // 输入时清除错误
  [username, password].forEach(function (input) {
    input.addEventListener("input", function () {
      input.closest(".form-group").classList.remove("has-error");
      clearError();
    });
  });

  // 说明：已登录用户访问 /login 由 pages.py 服务端守卫 302 → /，
  // 因此登录页无需（也不应）在未登录时调用 /auth/me —— 避免无意义的 401 控制台噪声。
})();