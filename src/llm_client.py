"""OpenRouter-backed client shaped like the Anthropic Python SDK's Messages API.

The rest of the app was written against `anthropic.Anthropic(...)` — `.messages.create()`,
`.messages.stream()`, `system=`, `.content[0].text`, `.stop_reason`, `.usage.input_tokens`.
This wraps OpenRouter's OpenAI-compatible `/chat/completions` endpoint behind that same
shape so call sites didn't need to change when we moved off calling Anthropic directly.
"""

from contextlib import contextmanager
from types import SimpleNamespace

from openai import APIStatusError, OpenAI

from config import OPENROUTER_API_KEY, OPENROUTER_MODEL

__all__ = ["OpenRouterClient", "APIStatusError"]

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _convert_content(content):
    """Anthropic messages content can be a plain string or a list of content
    blocks (text / base64 image). Convert blocks to OpenAI-compatible shape."""
    if isinstance(content, str) or content is None:
        return content
    converted = []
    for block in content:
        if block.get("type") == "image":
            source = block.get("source", {})
            media_type = source.get("media_type", "image/jpeg")
            data = source.get("data", "")
            converted.append({
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{data}"},
            })
        else:
            converted.append(block)
    return converted


def _to_chat_messages(system, messages):
    chat_messages = []
    if system:
        chat_messages.append({"role": "system", "content": system})
    for m in messages:
        chat_messages.append({**m, "content": _convert_content(m.get("content"))})
    return chat_messages


def _usage_namespace(usage):
    return SimpleNamespace(
        input_tokens=usage.prompt_tokens if usage else 0,
        output_tokens=usage.completion_tokens if usage else 0,
    )


def _stop_reason(finish_reason):
    return "max_tokens" if finish_reason == "length" else "end_turn"


class _Messages:
    def __init__(self, client):
        self._client = client

    def create(self, model=None, system=None, messages=None, max_tokens=1024, **kwargs):
        kwargs.pop("metadata", None)  # Anthropic-only concept, no OpenRouter equivalent
        raw = self._client.chat.completions.create(
            model=model or OPENROUTER_MODEL,
            max_tokens=max_tokens,
            messages=_to_chat_messages(system, messages or []),
            **kwargs,
        )
        choice = raw.choices[0]
        return SimpleNamespace(
            content=[SimpleNamespace(text=choice.message.content or "")],
            stop_reason=_stop_reason(choice.finish_reason),
            usage=_usage_namespace(raw.usage),
        )

    @contextmanager
    def stream(self, model=None, system=None, messages=None, max_tokens=1024, **kwargs):
        kwargs.pop("metadata", None)
        chunks = self._client.chat.completions.create(
            model=model or OPENROUTER_MODEL,
            max_tokens=max_tokens,
            messages=_to_chat_messages(system, messages or []),
            stream=True,
            stream_options={"include_usage": True},
            **kwargs,
        )

        class _StreamHandle:
            def get_final_message(self):
                text_parts = []
                finish_reason = None
                usage = None
                for chunk in chunks:
                    if chunk.usage:
                        usage = chunk.usage
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta.content
                    if delta:
                        text_parts.append(delta)
                    if chunk.choices[0].finish_reason:
                        finish_reason = chunk.choices[0].finish_reason
                return SimpleNamespace(
                    content=[SimpleNamespace(text="".join(text_parts))],
                    stop_reason=_stop_reason(finish_reason),
                    usage=_usage_namespace(usage),
                )

        yield _StreamHandle()


class OpenRouterClient:
    """Drop-in replacement for anthropic.Anthropic(api_key=...)."""

    def __init__(self, api_key=None, timeout=120.0):
        # Some OpenRouter-routed models (especially smaller/third-party-hosted
        # ones picked via the model-selection feature) can stall mid-stream for
        # minutes. Bound each attempt well below the SDK's 10-minute default so
        # a stalled request fails fast enough for _messages_create_with_retry
        # to actually retry instead of hanging silently.
        self._client = OpenAI(
            base_url=_OPENROUTER_BASE_URL,
            api_key=api_key or OPENROUTER_API_KEY,
            timeout=timeout,
            default_headers={
                "HTTP-Referer": "https://hitpay-content-creator.vercel.app",
                "X-Title": "HitPay Content Creator",
            },
        )
        self.messages = _Messages(self._client)
