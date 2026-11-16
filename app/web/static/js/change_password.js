/**
 * change_password.js —— /change-password 页面（含首登强制改密场景）
 *
 * 提交 POST /api/v1/auth/change-password；成功后：
 * - 首登强制改密用户 → 跳回工作台
 * - 其他 → 提示成功并留在本页
 */
(function () {
  "use strict";

  var form = document.getElementById("cp-form");
  if (!form) return;
  var oldPw = document.getElementById("cp-old");
  var newPw = document.getElementById("cp-new");
  var confirmPw = document.getElementById("cp-confirm");
  var errBox = document.getElementById("cp-error");
  var errText = document.getElementById("cp-error-text");
  var submitBtn = document.getElementById("cp-submit");

  function fieldError(input, show) {
    var group = input.closest(".form-group");
    if (!group) return;
    group.classList.toggle("has-error", !!show);
    input.classList.toggle("is-invalid", !!show);
  }

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    errBox.classList.add("hidden");
    var ok = true;
    fieldError(oldPw, !oldPw.value);
    fieldError(newPw, !newPw.value);
    fieldError(confirmPw, confirmPw.value !== newPw.value || !confirmPw.value);
    if (!oldPw.value || !newPw.value || confirmPw.value !== newPw.value) return;

    ui.btnLoading(submitBtn, true, "保存中…");
    try {
      await api.post("/api/v1/auth/change-password", {
        old_password: oldPw.value,
        new_password: newPw.value,
      }, null, { noAuthRedirect: true });  // 原密码错误返回 401 属业务拒绝，就地提示
      ui.toast("密码已修改", "success");
      if (window.__MUST_CHANGE_PASSWORD) {
        location.href = "/";
      } else {
        form.reset();
      }
    } catch (err) {
      errText.textContent = err && err.message ? err.message : "修改失败，请重试";
      errBox.classList.remove("hidden");
    } finally {
      ui.btnLoading(submitBtn, false);
    }
  });
})();
