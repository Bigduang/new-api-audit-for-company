import json
from typing import Any

import requests

from .config import Settings
from .schemas import ClassificationResult


SYSTEM_PROMPT = """你是企业内部 token 使用审计分类器。
判断用户请求是否真实用于工作，尤其是软件开发、调试、架构、配置运维、数据分析、文档等。
只输出 JSON，不要输出 Markdown。
JSON 字段:
category: 编码实现|调试修复|架构设计|配置运维|文档编写|代码审查|数据分析|疑似非工作|其他
work_verdict: work|non_work|uncertain
confidence: 0 到 1 的数字
reason: 简短中文原因
evidence: 命中的短语数组
"""


def classify_with_llm(settings: Settings, prompt_preview: str, prompt_text: str) -> ClassificationResult | None:
    if not settings.llm_enabled:
        return None
    text = prompt_text or prompt_preview
    if not text:
        return None
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text[:12000]},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(
        f"{settings.llm_base_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        json=payload,
        timeout=settings.llm_timeout_seconds,
    )
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    parsed = parse_llm_json_object(content)
    if parsed is None:
        return None
    evidence = parsed.get("evidence") or []
    if not isinstance(evidence, list):
        evidence = [str(evidence)]
    verdict = parsed.get("work_verdict", "uncertain")
    if verdict not in {"work", "non_work", "uncertain"}:
        verdict = "uncertain"
    return ClassificationResult(
        category=str(parsed.get("category") or "其他"),
        work_verdict=verdict,
        confidence=float(parsed.get("confidence") or 0),
        reason=str(parsed.get("reason") or "LLM 分类"),
        evidence=[str(item) for item in evidence],
        classifier_type="llm",
        needs_llm=False,
    )


def parse_llm_json_object(content: str) -> dict[str, Any] | None:
    content = content.strip()
    if not content:
        return None
    parsed = _loads_object(content)
    if parsed is not None:
        return parsed
    for candidate in _json_candidates(content):
        parsed = _loads_object(candidate)
        if parsed is not None:
            return parsed
    return None


def _loads_object(raw: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _json_candidates(content: str) -> list[str]:
    candidates: list[str] = []
    candidates.extend(_fenced_code_blocks(content))
    extracted = _first_balanced_json_object(content)
    if extracted:
        candidates.append(extracted)
    return candidates


def _fenced_code_blocks(content: str) -> list[str]:
    blocks: list[str] = []
    marker = "```"
    index = 0
    while True:
        start = content.find(marker, index)
        if start == -1:
            break
        body_start = content.find("\n", start + len(marker))
        if body_start == -1:
            break
        end = content.find(marker, body_start + 1)
        if end == -1:
            break
        blocks.append(content[body_start + 1 : end].strip())
        index = end + len(marker)
    return blocks


def _first_balanced_json_object(content: str) -> str | None:
    start = content.find("{")
    while start != -1:
        candidate = _balanced_object_from(content, start)
        if candidate is not None:
            return candidate
        start = content.find("{", start + 1)
    return None


def _balanced_object_from(content: str, start: int) -> str | None:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(content)):
        char = content[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return content[start : index + 1]
    return None
