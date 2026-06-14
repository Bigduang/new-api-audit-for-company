from token_audit.llm_classifier import parse_llm_json_object


def test_parse_llm_json_object_accepts_plain_json():
    parsed = parse_llm_json_object(
        '{"category":"编码实现","work_verdict":"work","confidence":0.9,"reason":"开发任务","evidence":["实现"]}'
    )
    assert parsed is not None
    assert parsed["work_verdict"] == "work"


def test_parse_llm_json_object_extracts_markdown_fenced_json():
    parsed = parse_llm_json_object(
        """
```json
{
  "category": "疑似非工作",
  "work_verdict": "non_work",
  "confidence": 0.88,
  "reason": "闲聊娱乐",
  "evidence": ["讲个笑话"]
}
```
"""
    )
    assert parsed is not None
    assert parsed["category"] == "疑似非工作"
    assert parsed["work_verdict"] == "non_work"


def test_parse_llm_json_object_extracts_first_balanced_object_from_text():
    parsed = parse_llm_json_object(
        '分类结果如下：{"category":"其他","work_verdict":"uncertain","confidence":0.4,"reason":"证据不足","evidence":["?"]} 请参考。'
    )
    assert parsed is not None
    assert parsed["work_verdict"] == "uncertain"


def test_parse_llm_json_object_handles_braces_inside_strings():
    parsed = parse_llm_json_object(
        '```json\n{"category":"调试修复","work_verdict":"work","confidence":0.91,"reason":"日志包含 {error}","evidence":["{error}"]}\n```'
    )
    assert parsed is not None
    assert parsed["reason"] == "日志包含 {error}"


def test_parse_llm_json_object_returns_none_for_unparseable_output():
    assert parse_llm_json_object("我认为这是工作请求，但没有 JSON。") is None
