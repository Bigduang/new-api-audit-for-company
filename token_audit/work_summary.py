from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit_users import display_name_for, enabled_audit_user_map, request_identity_key
from .config import Settings
from .llm_classifier import parse_llm_json_object
from .models import AuditClassification, AuditRequest, AuditUserWorkSummary


SYSTEM_PROMPT = (
    "你是企业内部研发工作审计分析器。输入是同一用户一天内调用 AI 的请求摘要。"
    "你的任务是归纳这个用户在实现或推进哪些功能或工作成果。"
    "不要猜项目名，不要根据单个弱提示过度推断。把相似请求合并成少量功能项。"
    "只输出 JSON，不要 Markdown。"
)

GENERIC_PROMPTS = {
    "继续",
    "ok",
    "OK",
    "好的",
    "可以",
    "开始",
    "继续吧",
    "继续执行",
    "嗯",
    "是",
    "不是",
    "很好",
    "合理",
    "确认",
    "检查一下",
    "再试一下",
}


def summarize_user_work(
    session: Session,
    settings: Settings,
    start: datetime,
    end: datetime,
    *,
    force: bool = False,
    max_samples_per_user: int = 70,
) -> int:
    if not settings.llm_enabled:
        return 0
    rows = session.execute(
        select(AuditRequest, AuditClassification)
        .outerjoin(AuditClassification, AuditClassification.request_id == AuditRequest.request_id)
        .where(AuditRequest.created_at >= start, AuditRequest.created_at <= end)
        .order_by(AuditRequest.created_at.asc())
    ).all()
    enabled_users = enabled_audit_user_map(session)
    by_user: dict[str, list[tuple[AuditRequest, AuditClassification | None]]] = defaultdict(list)
    for req, cls in rows:
        user = enabled_users.get(request_identity_key(req))
        if user is None or not user.audit_enabled:
            continue
        username = display_name_for(user, req.user_id, req.username)
        by_user[username].append((req, cls))

    count = 0
    for username, items in sorted(by_user.items(), key=lambda item: _total_tokens(item[1]), reverse=True):
        existing_query = select(AuditUserWorkSummary).where(
            AuditUserWorkSummary.period_start == start,
            AuditUserWorkSummary.period_end == end,
        )
        if items[0][0].user_id is not None:
            existing_query = existing_query.where(AuditUserWorkSummary.user_id == items[0][0].user_id)
        else:
            existing_query = existing_query.where(AuditUserWorkSummary.username == username)
        existing = session.scalar(existing_query)
        if existing is not None and not force:
            continue
        payload = _user_payload(username, items, start, end, max_samples_per_user=max_samples_per_user)
        result = _summarize_with_llm(settings, payload)
        row = existing or AuditUserWorkSummary(period_start=start, period_end=end, username=username)
        row.user_id = payload["user_id"]
        row.username = username
        row.request_count = payload["request_count"]
        row.total_tokens = payload["total_tokens"]
        row.sample_count = len(payload["sampled_requests"])
        row.confidence_overall = str(result.get("confidence_overall", ""))
        row.summary_json = json.dumps(result, ensure_ascii=False)
        row.summary_text = _summary_text(result)
        session.add(row)
        count += 1
    return count


def _total_tokens(items: list[tuple[AuditRequest, AuditClassification | None]]) -> int:
    return sum(req.prompt_tokens + req.completion_tokens for req, _cls in items)


def _user_payload(
    username: str,
    items: list[tuple[AuditRequest, AuditClassification | None]],
    start: datetime,
    end: datetime,
    *,
    max_samples_per_user: int,
) -> dict[str, Any]:
    verdicts = Counter((cls.work_verdict if cls else "unclassified") for _req, cls in items)
    categories = Counter((cls.category if cls else "未分类") for _req, cls in items)
    token_names = Counter((req.token_name or str(req.token_id or "unknown")) for req, _cls in items)
    models = Counter((req.model_name or "unknown") for req, _cls in items)
    user_ids = [req.user_id for req, _cls in items if req.user_id is not None]
    samples = _sample_requests(items, max_samples=max_samples_per_user)
    return {
        "user": username,
        "user_id": user_ids[0] if user_ids else None,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "request_count": len(items),
        "total_tokens": _total_tokens(items),
        "top_tokens": token_names.most_common(5),
        "top_models": models.most_common(5),
        "verdict_counts": dict(verdicts),
        "category_counts": dict(categories.most_common(8)),
        "sampled_requests": samples,
    }


def _sample_requests(items: list[tuple[AuditRequest, AuditClassification | None]], *, max_samples: int) -> list[dict[str, Any]]:
    selected = []
    seen = set()
    sorted_items = sorted(
        items,
        key=lambda pair: (pair[0].prompt_tokens + pair[0].completion_tokens, len(pair[0].prompt_preview or "")),
        reverse=True,
    )
    for req, cls in sorted_items:
        preview = _trim(req.prompt_preview, 280)
        if not _useful_preview(preview):
            continue
        key = preview[:120]
        if key in seen:
            continue
        seen.add(key)
        selected.append(
            {
                "time": req.created_at.isoformat(),
                "token": req.token_name,
                "model": req.model_name,
                "tokens": req.prompt_tokens + req.completion_tokens,
                "category": cls.category if cls else "未分类",
                "verdict": cls.work_verdict if cls else "unclassified",
                "prompt_preview": preview,
            }
        )
        if len(selected) >= max_samples:
            break
    return selected


def _useful_preview(text: str) -> bool:
    compact = " ".join((text or "").split())
    return len(compact) >= 8 and compact not in GENERIC_PROMPTS


def _trim(text: str, limit: int) -> str:
    compact = " ".join((text or "").split())
    return compact[:limit]


def _summarize_with_llm(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    prompt = (
        "请基于以下数据，归纳这个用户当天主要在实现/推进的功能。要求：\n"
        "1. 输出 JSON object。\n"
        "2. 字段：user, confidence_overall, main_functions, other_work, weak_evidence_notes。\n"
        "3. main_functions 是数组，每项包含 feature, activity_type, summary, evidence, confidence。\n"
        "4. activity_type 从 后端开发|前端开发|UI页面优化|调试排错|部署运维|数据分析|文档整理|代码审查|图片/物料设计|需求/方案梳理|其他 中选择。\n"
        "5. feature 用中文短语描述具体功能，不要写项目名。\n"
        "6. evidence 最多 3 条，引用输入中的短短语即可。\n"
        "7. 对“继续/检查一下/优化这个页面”等弱上下文，不要强行猜，放到 weak_evidence_notes。\n\n"
        "数据：\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    resp = requests.post(
        f"{settings.llm_base_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        json={
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt[:24000]},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        },
        timeout=max(settings.llm_timeout_seconds, 60),
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    parsed = parse_llm_json_object(content)
    if parsed is None:
        raise RuntimeError("LLM returned no parseable JSON")
    parsed["user"] = str(parsed.get("user") or payload["user"])
    parsed["_stats"] = {
        "request_count": payload["request_count"],
        "total_tokens": payload["total_tokens"],
        "sample_count": len(payload["sampled_requests"]),
        "verdict_counts": payload["verdict_counts"],
        "category_counts": payload["category_counts"],
    }
    return parsed


def _summary_text(result: dict[str, Any]) -> str:
    functions = result.get("main_functions")
    if not isinstance(functions, list):
        return ""
    lines = []
    for item in functions[:8]:
        if not isinstance(item, dict):
            continue
        feature = str(item.get("feature") or "未命名功能")
        activity = str(item.get("activity_type") or "其他")
        summary = str(item.get("summary") or "")
        lines.append(f"{feature}（{activity}）：{summary}")
    return "\n".join(lines)
