from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")

def test_admin_launch_checklist_matches_current_ui():
    text = read("docs/ADMIN_LAUNCH_CHECKLIST.md")
    for stale in ("闭环", "≤3 级", "允许普通用户下载", "Monday", "发第一条需确认的通知"):
        assert stale not in text, stale
    for current in ("最多 4 级", "已查看率", "未查看名单", "发第一条通知"):
        assert current in text, current

def test_employee_guide_points_to_real_menu():
    text = read("docs/EMPLOYEE_GUIDE.md")
    assert "右上角头像" not in text
    assert "左侧栏用户菜单" in text

def test_testing_matrix_has_no_retired_confirm_state():
    text = read("docs/TESTING.md")
    assert "需确认（确认条置顶 sticky）" not in text
    assert "未查看/已查看（无确认条）" in text
    assert "截断时显示默认下载提示" in text

def test_audit_and_stats_labels_are_current():
    audit = read("app/web/static/js/admin/audit_logs.js")
    assert "通知确认（历史）" not in audit
    assert "历史通知动作（兼容旧日志）" in audit
    stats = read("app/web/static/js/admin/notice_stats.js")
    assert "已查看率：" in stats
    assert "确认率" not in stats

def test_document_detail_download_copy_uses_default_download_terms():
    text = read("app/web/static/js/document_detail.js")
    assert "默认下载：" in text
    assert "下载此附件" in text
    assert "下载原文件" not in text

def test_document_form_does_not_expose_internal_rule_codes():
    text = read("app/web/static/js/admin/document_form.js")
    assert "生效日期晚于失效日期（R1）" not in text
    assert "时效覆盖警告（R2）" not in text
    assert "失效日期不能早于生效日期" in text
    assert "这个资料已有生效区间" in text

def test_document_list_range_is_wrapped_and_titled():
    js = read("app/web/static/js/documents.js")
    css = read("app/web/static/css/style.css")
    assert 'class="doc-range"' in js
    assert ".table-fixed .doc-range" in css

def test_admin_notice_view_does_not_show_personal_unread():
    notices = read("app/web/static/js/notices.js")
    dashboard = read("app/web/static/js/dashboard.js")
    template = read("app/web/templates/notices.html")
    assert "badge-need-confirm" not in notices
    assert "if (isAdmin) return \"\";" in notices
    assert "查看进度" in dashboard
    assert "{% if not is_admin %}" in template
    assert "管理员查看不计入阅读统计" in template

def test_form_actions_are_not_sticky_over_fields():
    css = read("app/web/static/css/style.css")
    block = css.split(".form-actions-sticky {", 1)[1].split("}", 1)[0]
    assert "position: static" in block


def test_document_category_back_reloads_right_side_scope():
    js = read("app/web/static/js/documents.js")
    assert "selectCategory(cur.parent_id);" in js
    assert "if (target === null) goAll();" in js
    assert "else selectCategory(target);" in js


def test_advanced_filters_are_always_expanded_for_documents_and_notices():
    documents = read("app/web/templates/documents.html")
    notices = read("app/web/templates/notices.html")
    css = read("app/web/static/css/style.css")
    for template in (documents, notices):
        assert '<details class="adv-filter"' not in template
        assert 'class="adv-filter adv-filter-static"' in template
        assert 'class="adv-rail"' in template
        assert "adv-field" in template
        assert "可按需组合" not in template
    assert 'class="adv-range"' not in documents
    assert 'class="adv-range"' not in notices
    assert ".adv-body-documents" in css
    assert ".adv-body-notices" in css
    assert "@media (max-width: 560px)" in css


def test_login_page_keeps_user_selected_blue_identity():
    css = read("app/web/static/css/style.css")
    assert "linear-gradient(155deg, #1a3c5d 0%, #2b5d8c 62%, #34709f 100%)" in css
    assert ".login-brand::before" in css
    assert ".login-brand::after" in css
    assert ".login-page { background: var(--bg); }" not in css


def test_document_summary_is_low_key_fold_after_linked_notices():
    detail_js = read("app/web/static/js/document_detail.js")
    css = read("app/web/static/css/style.css")
    form = read("app/web/templates/admin/document_form.html")
    assert "summarySection(d)" in detail_js
    assert "doc-summary-disclosure" in detail_js
    assert 'class="text-muted doc-summary"' not in detail_js
    assert ".doc-summary-disclosure > summary" in css
    assert "white-space: pre-wrap" in css
    assert 'maxlength="10000"' in form
    assert "正文关键词或内容摘要" in form
    for template in ("app/web/templates/base.html", "app/web/templates/documents.html",
                     "app/web/templates/index.html"):
        assert "搜索资料：标题、说明、摘要…" in read(template)
