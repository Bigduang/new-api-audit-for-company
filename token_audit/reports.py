from collections import Counter
from datetime import datetime, timezone
from html import escape
import json

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm import load_only

from .audit_users import display_name_for, enabled_audit_user_map, request_identity_key
from .models import AuditClassification, AuditDailyReport, AuditRequest, AuditUserWorkSummary
from .timeutil import fmt_local

VERDICT_LABELS = {
    "work": "工作",
    "non_work": "非工作",
    "uncertain": "不确定",
    "unclassified": "未分类",
}


def _request_report_load():
    return load_only(
        AuditRequest.request_id,
        AuditRequest.created_at,
        AuditRequest.user_id,
        AuditRequest.username,
        AuditRequest.token_id,
        AuditRequest.token_name,
        AuditRequest.model_name,
        AuditRequest.prompt_preview,
        AuditRequest.prompt_tokens,
        AuditRequest.completion_tokens,
        AuditRequest.quota,
        AuditRequest.usage_collected,
        AuditRequest.prompt_omitted,
    )


def _classification_report_load():
    return load_only(
        AuditClassification.request_id,
        AuditClassification.category,
        AuditClassification.work_verdict,
        AuditClassification.confidence,
        AuditClassification.reason,
        AuditClassification.review_status,
    )


def build_report_context(session: Session, start: datetime, end: datetime, tz_name: str) -> dict:
    all_rows = session.execute(
        select(AuditRequest, AuditClassification)
        .outerjoin(AuditClassification, AuditClassification.request_id == AuditRequest.request_id)
        .options(_request_report_load(), _classification_report_load())
        .where(AuditRequest.created_at >= start, AuditRequest.created_at <= end)
        .order_by(AuditRequest.created_at.asc())
    ).all()
    enabled_users = enabled_audit_user_map(session)
    rows = []
    aggregates: dict[tuple[int | None, str, int | None, str], dict[str, object]] = {}
    verdict_counts: Counter[str] = Counter()
    enabled_identity_keys = set(enabled_users)
    display_names_by_user_id: dict[int, str] = {}
    display_names_by_username: dict[str, str] = {}
    for req, cls in all_rows:
        user = enabled_users.get(request_identity_key(req))
        if user is None or not user.audit_enabled:
            continue
        rows.append((req, cls))
        display_name = display_name_for(user, req.user_id, req.username)
        if req.user_id is not None:
            display_names_by_user_id[req.user_id] = display_name
        elif req.username:
            display_names_by_username[req.username] = display_name
        key = (req.user_id, display_name, req.token_id, req.token_name)
        data = aggregates.setdefault(
            key,
            {
                "request_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "quota": 0,
                "models": Counter(),
                "verdicts": Counter(),
            },
        )
        data["request_count"] = int(data["request_count"]) + 1
        data["prompt_tokens"] = int(data["prompt_tokens"]) + req.prompt_tokens
        data["completion_tokens"] = int(data["completion_tokens"]) + req.completion_tokens
        data["total_tokens"] = int(data["total_tokens"]) + req.prompt_tokens + req.completion_tokens
        data["quota"] = int(data["quota"]) + req.quota
        data["models"][req.model_name or "N/A"] += 1
        verdict = cls.work_verdict if cls is not None else "unclassified"
        data["verdicts"][verdict] += 1
        verdict_counts[verdict] += 1
    usage_collected = sum(1 for req, _cls in rows if req.usage_collected)
    prompt_omitted = sum(1 for req, _cls in rows if req.prompt_omitted)
    user_rows = []
    for (user_id, username, token_id, token_name), data in aggregates.items():
        models = data["models"]
        top_model = models.most_common(1)[0][0] if models else "N/A"
        prompt = int(data["prompt_tokens"])
        completion = int(data["completion_tokens"])
        user_rows.append(
            {
                "user_id": user_id,
                "username": username,
                "token_id": token_id,
                "token_name": token_name,
                "request_count": int(data["request_count"]),
                "top_model": top_model,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
                "quota": int(data["quota"]),
                "verdicts": data["verdicts"],
            }
        )
    user_rows.sort(key=lambda item: item["total_tokens"], reverse=True)
    suspicious_rows = []
    for req, cls in rows:
        if cls is None or cls.work_verdict not in {"non_work", "uncertain"}:
            continue
        user = enabled_users.get(request_identity_key(req))
        display_name = display_name_for(user, req.user_id, req.username)
        suspicious_rows.append(
            {
                "time": fmt_local(req.created_at, tz_name),
                "username": display_name,
                "token": req.token_name or req.token_id or "unknown",
                "model": req.model_name,
                "total_tokens": req.prompt_tokens + req.completion_tokens,
                "verdict": cls.work_verdict,
                "category": cls.category,
                "confidence": cls.confidence,
                "review_status": cls.review_status,
                "reason": cls.reason or "",
                "preview": (req.prompt_preview or "").replace("\n", " ")[:300],
            }
        )
    suspicious_counts = Counter(f"{item['username']} / {item['token']}" for item in suspicious_rows)
    summary_rows = session.scalars(
        select(AuditUserWorkSummary)
        .where(
            AuditUserWorkSummary.period_start == start,
            AuditUserWorkSummary.period_end == end,
        )
        .order_by(AuditUserWorkSummary.total_tokens.desc())
    ).all()
    work_summaries = [
        _work_summary_from_row(row, display_names_by_user_id, display_names_by_username)
        for row in summary_rows
        if _summary_row_enabled(row, enabled_identity_keys, display_names_by_user_id, display_names_by_username)
    ]
    return {
        "rows": rows,
        "start_label": fmt_local(start, tz_name),
        "end_label": fmt_local(end, tz_name),
        "request_count": len(rows),
        "usage_collected": usage_collected,
        "prompt_omitted": prompt_omitted,
        "classified": len(rows) - verdict_counts["unclassified"],
        "verdict_counts": verdict_counts,
        "user_rows": user_rows,
        "suspicious_rows": suspicious_rows,
        "suspicious_counts": suspicious_counts,
        "work_summaries": work_summaries,
    }


def token_usage_report(session: Session, start: datetime, end: datetime, tz_name: str) -> str:
    context = build_report_context(session, start, end, tz_name)
    verdict_counts = context["verdict_counts"]

    lines = [
        f"## Token 使用审计报告",
        "",
        f"时间范围: {context['start_label']} - {context['end_label']}",
        f"请求总数: {context['request_count']}",
        "",
        "### 用户 Token 用量",
        "",
        "| 用户 | Token | 请求数 | 主要模型 | Prompt Tokens | Completion Tokens | 总 Tokens | Quota | 工作 | 非工作 | 不确定 | 未分类 |",
        "|------|-------|--------|---------|---------------|-------------------|-----------|-------|------|----------|-----------|--------|",
    ]
    for data in context["user_rows"]:
        verdicts = data["verdicts"]
        lines.append(
            f"| {data['username'] or data['user_id'] or 'unknown'} | {data['token_name'] or data['token_id'] or 'unknown'} | {data['request_count']} | {data['top_model']} | "
            f"{data['prompt_tokens']:,} | {data['completion_tokens']:,} | {data['total_tokens']:,} | {data['quota']:,} | "
            f"{verdicts['work']} | {verdicts['non_work']} | {verdicts['uncertain']} | {verdicts['unclassified']} |"
        )
    lines.extend(
        [
            "",
            "### 分类概览",
            "",
            f"- 工作: {verdict_counts['work']}",
            f"- 非工作: {verdict_counts['non_work']}",
            f"- 不确定: {verdict_counts['uncertain']}",
            f"- 未分类: {verdict_counts['unclassified']}",
        ]
    )
    return "\n".join(lines)


def suspicious_report(session: Session, start: datetime, end: datetime, tz_name: str) -> str:
    context = build_report_context(session, start, end, tz_name)
    rows = context["suspicious_rows"]
    lines = [
        "## 疑似非工作请求复核清单",
        "",
        f"时间范围: {context['start_label']} - {context['end_label']}",
        f"条目数: {len(rows)}",
        "",
        "| 时间 | 用户 | Token | 模型 | Tokens | 结论 | 类别 | 置信度 | 复核状态 | 原因 | Prompt 预览 |",
        "|------|------|-------|------|--------|------|------|--------|----------|------|-------------|",
    ]
    for item in rows:
        preview = str(item["preview"]).replace("|", "\\|")
        reason = str(item["reason"]).replace("|", "\\|")
        lines.append(
            f"| {item['time']} | {item['username']} | {item['token']} | {item['model']} | "
            f"{item['total_tokens']:,} | {_verdict_label(item['verdict'])} | {item['category']} | {item['confidence']:.2f} | {item['review_status']} | {reason} | {preview} |"
        )
    return "\n".join(lines)


def save_daily_report_snapshot(session: Session, start: datetime, end: datetime, tz_name: str, detail_url: str = "") -> AuditDailyReport:
    context = build_report_context(session, start, end, tz_name)
    title, description = wecom_daily_summary_from_context(context, detail_url)
    html = daily_html_report_from_context(context)
    row = session.scalar(
        select(AuditDailyReport).where(
            AuditDailyReport.period_start == start,
            AuditDailyReport.period_end == end,
        )
    )
    if row is None:
        row = AuditDailyReport(period_start=start, period_end=end)
    row.report_date = context["start_label"][:10]
    row.title = title
    row.summary_json = json.dumps(_daily_report_summary_payload(context), ensure_ascii=False)
    row.html_snapshot = html
    row.wecom_description = description
    row.generated_at = datetime.now(timezone.utc)
    session.add(row)
    return row


def update_daily_report_wecom_result(session: Session, row: AuditDailyReport, result: dict) -> None:
    row.wecom_msgid = str(result.get("msgid") or "")
    row.wecom_response = json.dumps(result, ensure_ascii=False)
    session.add(row)


def _daily_report_summary_payload(context: dict) -> dict:
    verdicts = context["verdict_counts"]
    return {
        "period": {
            "start": context["start_label"],
            "end": context["end_label"],
        },
        "request_count": context["request_count"],
        "usage_collected": context["usage_collected"],
        "classified": context["classified"],
        "prompt_omitted": context["prompt_omitted"],
        "verdict_counts": {
            "work": verdicts["work"],
            "non_work": verdicts["non_work"],
            "uncertain": verdicts["uncertain"],
            "unclassified": verdicts["unclassified"],
        },
        "verdict_labels": VERDICT_LABELS,
        "user_rows": [
            {
                "user_id": item["user_id"],
                "username": item["username"],
                "token_id": item["token_id"],
                "token_name": item["token_name"],
                "request_count": item["request_count"],
                "top_model": item["top_model"],
                "prompt_tokens": item["prompt_tokens"],
                "completion_tokens": item["completion_tokens"],
                "total_tokens": item["total_tokens"],
                "quota": item["quota"],
                "verdicts": {
                    "work": item["verdicts"]["work"],
                    "non_work": item["verdicts"]["non_work"],
                    "uncertain": item["verdicts"]["uncertain"],
                    "unclassified": item["verdicts"]["unclassified"],
                },
            }
            for item in context["user_rows"]
        ],
        "suspicious_count": len(context["suspicious_rows"]),
        "work_summary_count": len(context["work_summaries"]),
    }


def _compact_number(value: int | float) -> str:
    number = float(value)
    abs_number = abs(number)
    if abs_number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.1f}".rstrip("0").rstrip(".") + "B"
    if abs_number >= 1_000_000:
        return f"{number / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if abs_number >= 10_000:
        return f"{number / 1_000:.1f}".rstrip("0").rstrip(".") + "K"
    return f"{int(number):,}"


def _number_title(value: int | float, suffix: str = "") -> str:
    return f"{int(value):,}{suffix}"


def _verdict_label(verdict: object) -> str:
    return VERDICT_LABELS.get(str(verdict), str(verdict))


def _td(label: str, value: object, *, full_value: object | None = None, class_name: str = "") -> str:
    title = f' title="{escape(str(full_value))}"' if full_value is not None else ""
    cls = f' class="{escape(class_name)}"' if class_name else ""
    return f'<td data-label="{escape(label)}"{cls}{title}>{escape(str(value))}</td>'


def _summary_row_enabled(
    row: AuditUserWorkSummary,
    enabled_identity_keys: set[str],
    display_names_by_user_id: dict[int, str],
    display_names_by_username: dict[str, str],
) -> bool:
    if row.user_id is not None:
        return f"uid-{row.user_id}" in enabled_identity_keys
    return row.username in display_names_by_username or row.username in set(display_names_by_username.values())


def _work_summary_from_row(
    row: AuditUserWorkSummary,
    display_names_by_user_id: dict[int, str] | None = None,
    display_names_by_username: dict[str, str] | None = None,
) -> dict:
    display_names_by_user_id = display_names_by_user_id or {}
    display_names_by_username = display_names_by_username or {}
    username = row.username or row.user_id or "unknown"
    if row.user_id is not None and row.user_id in display_names_by_user_id:
        username = display_names_by_user_id[row.user_id]
    elif row.username in display_names_by_username:
        username = display_names_by_username[row.username]
    try:
        data = json.loads(row.summary_json or "{}")
    except json.JSONDecodeError:
        data = {}
    functions = data.get("main_functions")
    if not isinstance(functions, list):
        functions = []
    return {
        "username": username,
        "request_count": row.request_count,
        "total_tokens": row.total_tokens,
        "confidence_overall": str(data.get("confidence_overall") or row.confidence_overall or ""),
        "main_functions": [item for item in functions if isinstance(item, dict)][:6],
        "other_work": data.get("other_work") if isinstance(data.get("other_work"), list) else [],
        "weak_evidence_notes": str(data.get("weak_evidence_notes") or ""),
    }


def _work_summary_cards(work_summaries: list[dict]) -> str:
    if not work_summaries:
        return '<div class="summary-empty">暂无工作成果摘要，请先运行 summarize-work 任务。</div>'
    cards = []
    for summary in work_summaries:
        functions = []
        for item in summary["main_functions"][:5]:
            feature = escape(str(item.get("feature") or "未命名功能"))
            activity = escape(str(item.get("activity_type") or "其他"))
            detail = escape(str(item.get("summary") or ""))
            confidence = escape(str(item.get("confidence") or ""))
            confidence_text = f'<span class="confidence">置信度 {confidence}</span>' if confidence else ""
            functions.append(
                f'<li><div><strong>{feature}</strong><span>{activity}</span>{confidence_text}</div><p>{detail}</p></li>'
            )
        if not functions:
            functions.append("<li><div><strong>证据不足</strong><span>unknown</span></div><p>无法从当日请求稳定归纳具体功能。</p></li>")
        weak = ""
        if summary["weak_evidence_notes"]:
            weak = f'<div class="weak-note">{escape(summary["weak_evidence_notes"])}</div>'
        function_items = "".join(functions)
        cards.append(
            '<article class="work-card">'
            f'<div class="work-card-head"><div><span class="work-user">{escape(str(summary["username"]))}</span>'
            f'<strong>{_compact_number(summary["total_tokens"])} tokens</strong></div>'
            f'<span class="work-confidence">{escape(str(summary["confidence_overall"] or "n/a"))}</span></div>'
            f'<div class="work-meta">请求 {_compact_number(summary["request_count"])} 条</div>'
            f'<ul>{function_items}</ul>{weak}</article>'
        )
    return "\n".join(cards)


def wecom_daily_summary(session: Session, start: datetime, end: datetime, tz_name: str, detail_url: str) -> tuple[str, str]:
    context = build_report_context(session, start, end, tz_name)
    return wecom_daily_summary_from_context(context, detail_url)


def wecom_daily_summary_from_context(context: dict, detail_url: str) -> tuple[str, str]:
    verdicts = context["verdict_counts"]
    title = f"Token 审计日报 {context['start_label'][:10]}"
    lines = [
        f"<div class=\"gray\">{escape(context['start_label'])} - {escape(context['end_label'])}</div>",
        f"<div class=\"normal\">总请求: {context['request_count']} / 已结算: {context['usage_collected']} / 已分类: {context['classified']}</div>",
        f"<div class=\"highlight\">工作: {verdicts['work']}　非工作: {verdicts['non_work']}　不确定: {verdicts['uncertain']}　未分类: {verdicts['unclassified']}</div>",
    ]
    if context["user_rows"]:
        lines.append("<div class=\"normal\">Top 消耗:</div>")
        for index, item in enumerate(context["user_rows"][:5], start=1):
            name = f"{item['username'] or item['user_id'] or 'unknown'} / {item['token_name'] or item['token_id'] or 'unknown'}"
            lines.append(f"<div class=\"normal\">{index}. {escape(str(name))}: {_compact_number(item['total_tokens'])} tokens</div>")
    if context["suspicious_counts"]:
        lines.append("<div class=\"normal\">待复核:</div>")
        for name, count in context["suspicious_counts"].most_common(5):
            lines.append(f"<div class=\"normal\">- {escape(name)}: {count} 条</div>")
    lines.append(f"<div class=\"gray\">详情: {escape(detail_url)}</div>")
    return title, "\n".join(lines)


def daily_html_report(session: Session, start: datetime, end: datetime, tz_name: str) -> str:
    context = build_report_context(session, start, end, tz_name)
    return daily_html_report_from_context(context)


def _daily_report_css() -> str:
    return """
    :root {
      --bg-0: #090b18;
      --text: #f7fbff;
      --text-soft: rgba(247, 251, 255, .76);
      --muted: rgba(224, 232, 255, .58);
      --stroke: rgba(255, 255, 255, .18);
      --shadow: 0 22px 70px rgba(0, 0, 0, .34);
      --shadow-soft: 0 14px 40px rgba(0, 0, 0, .24);
      --cyan: #55f1e6;
      --blue: #7cb7ff;
      --magenta: #ff7ad9;
      --violet: #b59cff;
      --amber: #ffd36a;
      --green: #8df5b4;
      --red: #ff8a9a;
    }
    html { min-height: 100%; background: var(--bg-0); }
    body {
      min-height: 100vh;
      color: var(--text);
      background:
        linear-gradient(135deg, rgba(16, 20, 42, .98), rgba(8, 12, 26, .96) 42%, rgba(12, 25, 34, .97)),
        conic-gradient(from 210deg at 50% -12%, rgba(85, 241, 230, .24), rgba(255, 122, 217, .18), rgba(124, 183, 255, .14), rgba(255, 211, 106, .10), rgba(85, 241, 230, .20));
      overflow-x: hidden;
    }
    body::before,
    body::after {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
    }
    body::before {
      z-index: -2;
      background:
        linear-gradient(110deg, rgba(85, 241, 230, .13), transparent 28%, rgba(255, 122, 217, .11) 58%, transparent 78%),
        linear-gradient(165deg, transparent 8%, rgba(124, 183, 255, .12) 38%, transparent 66%, rgba(255, 211, 106, .08));
      filter: saturate(1.12);
    }
    body::after {
      z-index: -1;
      opacity: .42;
      background-image:
        linear-gradient(rgba(255, 255, 255, .045) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255, 255, 255, .035) 1px, transparent 1px);
      background-size: 34px 34px;
      mask-image: linear-gradient(to bottom, rgba(0, 0, 0, .95), rgba(0, 0, 0, .18));
    }
    header {
      border-bottom: 1px solid rgba(255, 255, 255, .13);
      background: linear-gradient(135deg, rgba(255, 255, 255, .13), rgba(255, 255, 255, .055));
      backdrop-filter: blur(24px) saturate(1.28);
      -webkit-backdrop-filter: blur(24px) saturate(1.28);
      box-shadow: 0 18px 48px rgba(0, 0, 0, .22);
    }
    .hero-inner, main { width: min(1180px, calc(100% - 32px)); }
    h1 { color: #ffffff; font-size: 29px; }
    h2 { color: #ffffff; }
    .kicker { color: var(--cyan); font-weight: 780; }
    .meta { color: var(--muted); }
    .metric,
    .work-card,
    .table-wrap,
    .weak-note,
    .summary-empty {
      position: relative;
      border: 1px solid var(--stroke);
      border-radius: 8px;
      background:
        linear-gradient(140deg, rgba(255, 255, 255, .145), rgba(255, 255, 255, .052) 52%, rgba(255, 255, 255, .09)),
        linear-gradient(180deg, rgba(255, 255, 255, .07), rgba(255, 255, 255, .035));
      box-shadow: var(--shadow-soft), inset 0 1px 0 rgba(255, 255, 255, .18);
      backdrop-filter: blur(22px) saturate(1.26);
      -webkit-backdrop-filter: blur(22px) saturate(1.26);
    }
    .metric::before,
    .work-card::before,
    .table-wrap::before,
    .weak-note::before,
    .summary-empty::before {
      content: "";
      position: absolute;
      inset: 0;
      border-radius: inherit;
      pointer-events: none;
      background:
        linear-gradient(135deg, rgba(255, 255, 255, .22), transparent 36%),
        linear-gradient(315deg, rgba(85, 241, 230, .10), transparent 32%);
      opacity: .7;
    }
    .metric {
      transition: transform .2s ease, border-color .2s ease, box-shadow .2s ease;
    }
    .metric:hover,
    .work-card:hover,
    .table-wrap:hover {
      border-color: rgba(255, 255, 255, .30);
      box-shadow: var(--shadow), 0 0 0 1px rgba(85, 241, 230, .06), inset 0 1px 0 rgba(255, 255, 255, .24);
    }
    .metric:hover { transform: translateY(-2px); }
    .metric span,
    .work-meta,
    .weak-note,
    .summary-empty {
      color: var(--muted);
    }
    .metric strong { color: var(--text); }
    .metric.accent strong {
      color: var(--cyan);
      text-shadow: 0 0 24px rgba(85, 241, 230, .24);
    }
    .metric.warn strong {
      color: var(--amber);
      text-shadow: 0 0 22px rgba(255, 211, 106, .18);
    }
    .section-head::before {
      width: 5px;
      background: linear-gradient(180deg, var(--cyan), var(--magenta));
      box-shadow: 0 0 18px rgba(85, 241, 230, .46);
    }
    .work-card-head {
      border-bottom: 1px solid rgba(255, 255, 255, .12);
    }
    .work-user,
    .work-card li strong,
    td.identity {
      color: #ffffff;
    }
    .work-card-head strong {
      color: var(--cyan);
      text-shadow: 0 0 20px rgba(85, 241, 230, .22);
    }
    .work-confidence,
    .work-card li span {
      border: 1px solid rgba(255, 255, 255, .14);
      background: rgba(255, 255, 255, .08);
      color: var(--text-soft);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, .08);
    }
    .work-card li {
      border-left-color: rgba(85, 241, 230, .58);
    }
    .work-card li p,
    td,
    .long-text {
      color: var(--text-soft);
    }
    table { min-width: 980px; }
    th, td {
      border-bottom: 1px solid rgba(255, 255, 255, .10);
    }
    th {
      background: rgba(16, 20, 40, .82);
      color: rgba(235, 250, 255, .90);
      backdrop-filter: blur(18px) saturate(1.22);
      -webkit-backdrop-filter: blur(18px) saturate(1.22);
    }
    tr.data-row {
      transition: background .16s ease;
    }
    tr.data-row:hover {
      background: rgba(255, 255, 255, .045);
    }
    .badge {
      border: 1px solid rgba(255, 255, 255, .13);
      background: rgba(255, 255, 255, .09);
      color: #e9fbff;
    }
    .badge.work {
      border-color: rgba(141, 245, 180, .38);
      background: rgba(141, 245, 180, .14);
      color: #dfffe9;
    }
    .badge.non_work {
      border-color: rgba(255, 138, 154, .38);
      background: rgba(255, 138, 154, .15);
      color: #ffe0e6;
    }
    .badge.uncertain {
      border-color: rgba(255, 211, 106, .38);
      background: rgba(255, 211, 106, .14);
      color: #ffedbd;
    }
    @media (max-width: 760px) {
      .table-wrap {
        background: transparent;
        backdrop-filter: none;
        -webkit-backdrop-filter: none;
      }
      .table-wrap::before { display: none; }
      tr.data-row {
        border: 1px solid rgba(255, 255, 255, .15);
        background: linear-gradient(140deg, rgba(255, 255, 255, .12), rgba(255, 255, 255, .055));
        box-shadow: var(--shadow-soft), inset 0 1px 0 rgba(255, 255, 255, .14);
        backdrop-filter: blur(18px) saturate(1.2);
        -webkit-backdrop-filter: blur(18px) saturate(1.2);
      }
      td::before { color: var(--muted); }
      .empty {
        border: 1px solid rgba(255, 255, 255, .15);
        background: linear-gradient(140deg, rgba(255, 255, 255, .12), rgba(255, 255, 255, .055));
      }
    }
    """


def daily_html_report_from_context(context: dict) -> str:
    verdicts = context["verdict_counts"]
    work_summary_cards = _work_summary_cards(context["work_summaries"])
    user_rows = "\n".join(
        '<tr class="data-row">'
        + _td("用户", item["username"] or item["user_id"] or "unknown", class_name="identity")
        + _td("Token", item["token_name"] or item["token_id"] or "unknown", class_name="identity")
        + _td("请求数", _compact_number(item["request_count"]), full_value=_number_title(item["request_count"]))
        + _td("主要模型", item["top_model"])
        + _td("Prompt", _compact_number(item["prompt_tokens"]), full_value=_number_title(item["prompt_tokens"]))
        + _td("Completion", _compact_number(item["completion_tokens"]), full_value=_number_title(item["completion_tokens"]))
        + _td("总 Tokens", _compact_number(item["total_tokens"]), full_value=_number_title(item["total_tokens"]))
        + _td("Quota", _compact_number(item["quota"]), full_value=_number_title(item["quota"]))
        + _td("工作", _compact_number(item["verdicts"]["work"]), full_value=_number_title(item["verdicts"]["work"]))
        + _td("非工作", _compact_number(item["verdicts"]["non_work"]), full_value=_number_title(item["verdicts"]["non_work"]))
        + _td("不确定", _compact_number(item["verdicts"]["uncertain"]), full_value=_number_title(item["verdicts"]["uncertain"]))
        + _td("未分类", _compact_number(item["verdicts"]["unclassified"]), full_value=_number_title(item["verdicts"]["unclassified"]))
        + "</tr>"
        for item in context["user_rows"]
    )
    suspicious_rows = "\n".join(
        '<tr class="data-row attention-row">'
        + _td("时间", item["time"])
        + _td("用户", item["username"], class_name="identity")
        + _td("Token", item["token"], class_name="identity")
        + _td("模型", item["model"])
        + _td("Tokens", _compact_number(item["total_tokens"]), full_value=_number_title(item["total_tokens"]))
        + f'<td data-label="结论"><span class="badge {escape(str(item["verdict"]))}">{escape(_verdict_label(item["verdict"]))}</span></td>'
        + _td("类别", item["category"])
        + _td("置信度", f'{item["confidence"]:.2f}')
        + _td("复核状态", item["review_status"])
        + _td("原因", item["reason"], class_name="long-text")
        + _td("Prompt 预览", item["preview"], class_name="long-text")
        + "</tr>"
        for item in context["suspicious_rows"]
    )
    if not suspicious_rows:
        suspicious_rows = '<tr><td colspan="11" class="empty">暂无疑似非工作或不确定请求</td></tr>'
    title = f"Token 审计日报 {context['start_label'][:10]}"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{
      --bg: #071311;
      --panel: #10201d;
      --panel-2: #142723;
      --line: #21413b;
      --line-strong: #29d6bd;
      --text: #e9fff9;
      --muted: #93b7ae;
      --cyan: #28e6cf;
      --amber: #ffcf5a;
      --red: #ff6b6b;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background-color: var(--bg);
      background-image:
        linear-gradient(rgba(40, 230, 207, .06) 1px, transparent 1px),
        linear-gradient(90deg, rgba(40, 230, 207, .05) 1px, transparent 1px);
      background-size: 28px 28px;
      letter-spacing: 0;
    }}
    header {{
      border-bottom: 1px solid rgba(40, 230, 207, .24);
      background: #0b1a17;
      padding: 26px 20px 22px;
    }}
    .hero-inner, main {{ width: min(1180px, calc(100% - 32px)); margin: 0 auto; }}
    h1 {{ margin: 0 0 8px; font-size: 26px; line-height: 1.2; }}
    h2 {{ margin: 26px 0 12px; font-size: 18px; line-height: 1.25; color: #f6fffc; }}
    .kicker {{ color: var(--cyan); font-size: 12px; font-weight: 700; text-transform: uppercase; }}
    .meta {{ color: var(--muted); font-size: 14px; }}
    main {{ padding: 20px 0 30px; }}
    .summary {{ display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; margin-top: 18px; }}
    .metric {{
      min-width: 0;
      background: linear-gradient(180deg, rgba(20, 39, 35, .98), rgba(12, 26, 23, .98));
      border: 1px solid rgba(40, 230, 207, .22);
      border-radius: 8px;
      padding: 13px;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, .04);
    }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; line-height: 1.2; }}
    .metric strong {{ display: block; margin-top: 6px; font-size: 23px; line-height: 1; color: var(--text); overflow-wrap: anywhere; }}
    .metric.accent strong {{ color: var(--cyan); }}
    .metric.warn strong {{ color: var(--amber); }}
    .section-head {{ display: flex; align-items: center; gap: 10px; }}
    .section-head::before {{ content: ""; width: 4px; height: 18px; border-radius: 8px; background: var(--cyan); }}
    .work-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}
    .work-card {{
      min-width: 0;
      background:
        linear-gradient(135deg, rgba(40, 230, 207, .10), transparent 36%),
        linear-gradient(180deg, rgba(20, 39, 35, .98), rgba(11, 26, 23, .98));
      border: 1px solid rgba(40, 230, 207, .22);
      border-radius: 8px;
      padding: 14px;
      box-shadow: 0 12px 28px rgba(0, 0, 0, .22), inset 0 1px 0 rgba(255, 255, 255, .04);
    }}
    .work-card-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
      border-bottom: 1px solid rgba(147, 183, 174, .14);
      padding-bottom: 10px;
    }}
    .work-card-head > div {{ min-width: 0; }}
    .work-user {{ display: block; color: #ffffff; font-weight: 750; line-height: 1.25; overflow-wrap: anywhere; }}
    .work-card-head strong {{ display: block; margin-top: 5px; color: var(--cyan); font-size: 18px; line-height: 1; }}
    .work-confidence {{
      flex: 0 0 auto;
      max-width: 92px;
      padding: 4px 8px;
      border: 1px solid rgba(40, 230, 207, .28);
      border-radius: 999px;
      color: #cafff6;
      background: rgba(40, 230, 207, .08);
      font-size: 12px;
      text-align: center;
      overflow-wrap: anywhere;
    }}
    .work-meta {{ margin-top: 9px; color: var(--muted); font-size: 12px; }}
    .work-card ul {{ list-style: none; margin: 10px 0 0; padding: 0; display: grid; gap: 10px; }}
    .work-card li {{
      border-left: 2px solid rgba(40, 230, 207, .55);
      padding-left: 10px;
      min-width: 0;
    }}
    .work-card li div {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
    .work-card li strong {{ color: #f4fffc; font-size: 14px; line-height: 1.35; overflow-wrap: anywhere; }}
    .work-card li span {{
      color: #aeece1;
      background: rgba(40, 230, 207, .08);
      border: 1px solid rgba(40, 230, 207, .18);
      border-radius: 999px;
      padding: 2px 7px;
      font-size: 11px;
      line-height: 1.35;
    }}
    .work-card li .confidence {{ color: #ffe6a0; background: rgba(255, 207, 90, .12); border-color: rgba(255, 207, 90, .24); }}
    .work-card li p {{ margin: 6px 0 0; color: #d7eee8; font-size: 13px; line-height: 1.55; overflow-wrap: anywhere; }}
    .weak-note, .summary-empty {{
      margin-top: 12px;
      color: var(--muted);
      background: rgba(147, 183, 174, .08);
      border: 1px solid rgba(147, 183, 174, .16);
      border-radius: 8px;
      padding: 10px;
      font-size: 13px;
      line-height: 1.5;
    }}
    .summary-empty {{ grid-column: 1 / -1; text-align: center; }}
    .table-wrap {{
      overflow-x: auto;
      background: rgba(16, 32, 29, .94);
      border: 1px solid rgba(40, 230, 207, .22);
      border-radius: 8px;
      box-shadow: 0 12px 30px rgba(0, 0, 0, .24);
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; min-width: 980px; }}
    th, td {{ padding: 11px 10px; border-bottom: 1px solid rgba(147, 183, 174, .14); text-align: left; vertical-align: top; white-space: nowrap; }}
    th {{ position: sticky; top: 0; background: #142723; color: #bdf9ee; font-weight: 700; z-index: 1; }}
    td {{ color: #eafdf8; }}
    .identity {{ color: #ffffff; font-weight: 650; }}
    .long-text {{ white-space: normal; min-width: 220px; max-width: 420px; color: #d7eee8; }}
    .badge {{ display: inline-block; padding: 3px 8px; border-radius: 999px; background: #29423d; color: #d8fff7; border: 1px solid rgba(255,255,255,.1); }}
    .badge.non_work {{ background: rgba(255, 107, 107, .16); color: #ffd1d1; border-color: rgba(255, 107, 107, .35); }}
    .badge.uncertain {{ background: rgba(255, 207, 90, .15); color: #ffe6a0; border-color: rgba(255, 207, 90, .35); }}
    .empty {{ text-align: center; color: var(--muted); padding: 22px; }}
    @media (max-width: 980px) {{
      .summary {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
      .work-grid {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 760px) {{
      header {{ padding: 20px 14px 18px; }}
      .hero-inner, main {{ width: calc(100% - 24px); }}
      h1 {{ font-size: 23px; }}
      .meta {{ font-size: 13px; }}
      main {{ padding-top: 14px; }}
      .summary {{ grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }}
      .metric {{ padding: 11px; min-height: 74px; }}
      .metric strong {{ font-size: 21px; }}
      .work-grid {{ gap: 10px; }}
      .work-card {{ padding: 12px; }}
      .work-card-head {{ gap: 8px; }}
      .work-card-head strong {{ font-size: 17px; }}
      .work-confidence {{ max-width: 82px; padding: 4px 7px; }}
      .table-wrap {{ overflow: visible; background: transparent; border: 0; box-shadow: none; }}
      table, thead, tbody, tr, td {{ display: block; width: 100%; min-width: 0; }}
      table {{ font-size: 13px; }}
      thead {{ display: none; }}
      tr.data-row {{
        margin: 0 0 12px;
        background: rgba(16, 32, 29, .96);
        border: 1px solid rgba(40, 230, 207, .2);
        border-radius: 8px;
        overflow: hidden;
        box-shadow: 0 8px 20px rgba(0, 0, 0, .22);
      }}
      tr.attention-row {{ border-color: rgba(255, 207, 90, .28); }}
      td {{
        display: grid;
        grid-template-columns: 92px minmax(0, 1fr);
        gap: 10px;
        align-items: start;
        padding: 9px 11px;
        border-bottom: 1px solid rgba(147, 183, 174, .12);
        white-space: normal;
      }}
      td::before {{
        content: attr(data-label);
        color: var(--muted);
        font-size: 12px;
        line-height: 1.35;
      }}
      .long-text {{ min-width: 0; max-width: none; overflow-wrap: anywhere; }}
      .empty {{ display: block; border: 1px solid rgba(40, 230, 207, .2); border-radius: 8px; background: rgba(16, 32, 29, .96); }}
    }}
    @media (max-width: 380px) {{
      .summary {{ grid-template-columns: 1fr; }}
      .work-card-head {{ display: block; }}
      .work-confidence {{ display: inline-block; margin-top: 8px; max-width: 100%; }}
      td {{ grid-template-columns: 82px minmax(0, 1fr); }}
    }}
  </style>
  <style>{_daily_report_css()}</style>
</head>
<body>
  <header>
    <div class="hero-inner">
      <div class="kicker">Token Audit Console</div>
      <h1>{escape(title)}</h1>
      <div class="meta">{escape(context['start_label'])} - {escape(context['end_label'])}</div>
    </div>
  </header>
  <main>
    <section class="summary">
      <div class="metric accent"><span>请求总数</span><strong title="{_number_title(context['request_count'])}">{_compact_number(context['request_count'])}</strong></div>
      <div class="metric"><span>已结算</span><strong title="{_number_title(context['usage_collected'])}">{_compact_number(context['usage_collected'])}</strong></div>
      <div class="metric accent"><span>工作</span><strong title="{_number_title(verdicts['work'])}">{_compact_number(verdicts['work'])}</strong></div>
      <div class="metric"><span>非工作</span><strong title="{_number_title(verdicts['non_work'])}">{_compact_number(verdicts['non_work'])}</strong></div>
      <div class="metric warn"><span>不确定</span><strong title="{_number_title(verdicts['uncertain'])}">{_compact_number(verdicts['uncertain'])}</strong></div>
      <div class="metric"><span>未分类</span><strong title="{_number_title(verdicts['unclassified'])}">{_compact_number(verdicts['unclassified'])}</strong></div>
    </section>
    <h2 class="section-head">每人工作成果摘要</h2>
    <section class="work-grid">{work_summary_cards}</section>
    <h2 class="section-head">用户 Token 用量</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>用户</th><th>Token</th><th>请求数</th><th>主要模型</th><th>Prompt</th><th>Completion</th><th>总 Tokens</th><th>Quota</th><th>工作</th><th>非工作</th><th>不确定</th><th>未分类</th></tr></thead>
        <tbody>{user_rows}</tbody>
      </table>
    </div>
    <h2 class="section-head">疑似非工作 / 不确定请求</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>时间</th><th>用户</th><th>Token</th><th>模型</th><th>Tokens</th><th>结论</th><th>类别</th><th>置信度</th><th>复核状态</th><th>原因</th><th>Prompt 预览</th></tr></thead>
        <tbody>{suspicious_rows}</tbody>
      </table>
    </div>
  </main>
</body>
</html>"""
