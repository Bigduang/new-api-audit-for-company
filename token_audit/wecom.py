import requests

from .config import Settings

DEFAULT_MAX_TEXT_BYTES = 1800


def push_wecom_text(settings: Settings, content: str, max_text_bytes: int = DEFAULT_MAX_TEXT_BYTES) -> dict:
    access_token = get_wecom_access_token(settings)
    chunks = split_text_by_utf8_bytes(content, max_text_bytes)
    results = []
    for index, chunk in enumerate(chunks, start=1):
        message = chunk
        if len(chunks) > 1:
            prefix = f"[{index}/{len(chunks)}]\n"
            message = prefix + chunk
            while len(message.encode("utf-8")) > max_text_bytes and chunk:
                chunk = chunk[:-1]
                message = prefix + chunk
        result = _send_wecom_text(settings, access_token, message)
        results.append(result)
        if result.get("errcode") != 0:
            raise RuntimeError(f"failed to send wecom message: {result}")
    if len(results) == 1:
        return results[0]
    return {"errcode": 0, "errmsg": "ok", "message_count": len(results), "results": results}


def push_wecom_textcard(settings: Settings, title: str, description: str, url: str, btntxt: str = "查看详情") -> dict:
    access_token = get_wecom_access_token(settings)
    send_resp = requests.post(
        "https://qyapi.weixin.qq.com/cgi-bin/message/send",
        params={"access_token": access_token},
        json={
            "touser": "@all",
            "msgtype": "textcard",
            "agentid": settings.wecom_agent_id,
            "textcard": {
                "title": title,
                "description": description,
                "url": url,
                "btntxt": btntxt,
            },
        },
        timeout=15,
    )
    send_resp.raise_for_status()
    result = send_resp.json()
    if result.get("errcode") != 0:
        raise RuntimeError(f"failed to send wecom textcard: {result}")
    return result


def get_wecom_access_token(settings: Settings) -> str:
    if not settings.wecom_corpid or not settings.wecom_appsecret or not settings.wecom_agent_id:
        raise ValueError("WX_CORPID, WX_APPSECRET, and WX_AGENT_ID are required")
    token_resp = requests.get(
        "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
        params={"corpid": settings.wecom_corpid, "corpsecret": settings.wecom_appsecret},
        timeout=15,
    )
    token_resp.raise_for_status()
    token_data = token_resp.json()
    if token_data.get("errcode") != 0:
        raise RuntimeError(f"failed to get wecom access token: {token_data}")
    return token_data["access_token"]


def _send_wecom_text(settings: Settings, access_token: str, content: str) -> dict:
    send_resp = requests.post(
        "https://qyapi.weixin.qq.com/cgi-bin/message/send",
        params={"access_token": access_token},
        json={
            "touser": "@all",
            "msgtype": "text",
            "agentid": settings.wecom_agent_id,
            "text": {"content": content},
        },
        timeout=15,
    )
    send_resp.raise_for_status()
    return send_resp.json()


def split_text_by_utf8_bytes(content: str, max_bytes: int) -> list[str]:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if content == "":
        return [""]
    chunks: list[str] = []
    current: list[str] = []
    current_bytes = 0
    for char in content:
        char_bytes = len(char.encode("utf-8"))
        if current and current_bytes + char_bytes > max_bytes:
            chunks.append("".join(current))
            current = []
            current_bytes = 0
        if char_bytes > max_bytes:
            chunks.append(char)
            continue
        current.append(char)
        current_bytes += char_bytes
    if current:
        chunks.append("".join(current))
    return chunks
