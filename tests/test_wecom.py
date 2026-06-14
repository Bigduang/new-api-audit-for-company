from token_audit.config import settings_for_tests
from token_audit.wecom import push_wecom_text, push_wecom_textcard, split_text_by_utf8_bytes


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_split_text_by_utf8_bytes_preserves_characters():
    chunks = split_text_by_utf8_bytes("中文abc", 6)

    assert chunks == ["中文", "abc"]


def test_push_wecom_text_splits_large_content(monkeypatch):
    posts = []

    def fake_get(*args, **kwargs):
        return FakeResponse({"errcode": 0, "access_token": "token"})

    def fake_post(*args, **kwargs):
        posts.append(kwargs["json"]["text"]["content"])
        return FakeResponse({"errcode": 0, "errmsg": "ok", "msgid": f"msg-{len(posts)}"})

    monkeypatch.setattr("token_audit.wecom.requests.get", fake_get)
    monkeypatch.setattr("token_audit.wecom.requests.post", fake_post)
    settings = settings_for_tests()
    settings = settings.__class__(
        **{
            **settings.__dict__,
            "wecom_corpid": "corp",
            "wecom_appsecret": "secret",
            "wecom_agent_id": 1000010,
        }
    )

    result = push_wecom_text(settings, "审计" * 20, max_text_bytes=40)

    assert result["errcode"] == 0
    assert result["message_count"] > 1
    assert len(posts) == result["message_count"]
    assert all(len(item.encode("utf-8")) <= 40 for item in posts)


def test_push_wecom_textcard(monkeypatch):
    posts = []

    def fake_get(*args, **kwargs):
        return FakeResponse({"errcode": 0, "access_token": "token"})

    def fake_post(*args, **kwargs):
        posts.append(kwargs["json"])
        return FakeResponse({"errcode": 0, "errmsg": "ok", "msgid": "msg-card"})

    monkeypatch.setattr("token_audit.wecom.requests.get", fake_get)
    monkeypatch.setattr("token_audit.wecom.requests.post", fake_post)
    settings = settings_for_tests()
    settings = settings.__class__(
        **{
            **settings.__dict__,
            "wecom_corpid": "corp",
            "wecom_appsecret": "secret",
            "wecom_agent_id": 1000010,
        }
    )

    result = push_wecom_textcard(settings, "日报", "摘要", "https://example.com/report")

    assert result["errcode"] == 0
    assert posts[0]["msgtype"] == "textcard"
    assert posts[0]["textcard"]["url"] == "https://example.com/report"
