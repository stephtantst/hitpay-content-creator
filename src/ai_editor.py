"""AI-powered targeted editing for blog post content."""

import time
from config import OPENROUTER_MODEL
from src.llm_client import APIStatusError, OpenRouterClient


_NON_RETRYABLE_STATUS_CODES = (400, 401, 403, 404, 422)


def _messages_create_with_retry(client, max_retries=4, **kwargs):
    """Call client.messages.create with exponential backoff on transient errors
    (rate-limit/overload HTTP statuses and network-level failures like read
    timeouts). A definite client error fails immediately instead of retrying."""
    for attempt in range(max_retries):
        try:
            return client.messages.create(**kwargs)
        except APIStatusError as e:
            if e.status_code in _NON_RETRYABLE_STATUS_CODES or attempt >= max_retries - 1:
                raise
            time.sleep(2 ** attempt)
        except Exception:
            if attempt >= max_retries - 1:
                raise
            time.sleep(2 ** attempt)

_EDIT_SYSTEM = """You are a precise content editor for HitPay's blog. Apply targeted edits to blog post markdown content.

Rules:
- Apply ONLY the requested change — do not rewrite, restructure, or improve anything else
- Preserve the author's voice, tone, and markdown formatting exactly
- Keep all internal backlinks (markdown link syntax) in place unless explicitly asked to change them
- Never add marketing jargon ("seamlessly", "unlock", "game-changer", etc.)
- Return ONLY the edited content — no preamble, no explanation, no code fences"""


def ai_edit_selection(selection: str, instruction: str) -> str:
    """Apply a targeted edit to a highlighted selection.

    Token-efficient: only sends the selected text to Claude, not the full post.

    Args:
        selection: The highlighted text from the editor
        instruction: What to change (e.g. "remove mention of Maya")
    """
    client = OpenRouterClient()
    response = _messages_create_with_retry(client,
        model=OPENROUTER_MODEL,
        max_tokens=2048,
        system=_EDIT_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Apply this edit to the following text:\n\n"
                f"INSTRUCTION: {instruction}\n\n"
                f"TEXT:\n---\n{selection}\n---\n\n"
                f"Return only the edited text, preserving all markdown formatting."
            )
        }]
    )
    return response.content[0].text.strip()


_SOCIAL_EDIT_SYSTEM = """You are a precise content editor for HitPay's social media posts. Apply targeted edits to a social post's text.

Rules:
- Apply ONLY the requested change — do not rewrite, restructure, or "improve" anything else
- Preserve the post's existing voice, tone, and line breaks exactly
- If the text contains "---" on its own line, that separates individual posts in a thread — KEEP those separators and the number of posts unless the instruction explicitly says to add or remove posts
- Respect platform length limits: X/Twitter posts must stay under 280 characters each; Threads posts under 500 characters each
- Never add hashtags, emoji, or marketing jargon ("seamlessly", "unlock", "game-changer", etc.) unless explicitly asked
- Do not add or invent facts, rates, or claims that are not already present
- Return ONLY the edited text — no preamble, no explanation, no code fences"""


_SOCIAL_EXTRA = {
    "instagram": "This is an Instagram caption. Keep the strong first-line hook, scannable short lines/small paragraphs, and a warm human tone. A few Instagram-native emojis are fine; do not add more than are needed. If a block of #hashtags is present at the end, keep it separate from the caption body and only change it if the instruction asks.",
    "reddit-op": "This is a Reddit OP body written in a real merchant's voice. Keep it casual (lowercase-casual is fine), keep it to 2-3 short paragraphs, put NO HitPay or product branding in it, and use NO em dashes.",
    "reddit-reply": "This is the separate reply from the verified HitPay account. Keep it dry, matter-of-fact, and honest about limitations; no hype, no hashtags, and NO em dashes.",
}


def ai_edit_social(content: str, instruction: str, platform: str = None) -> str:
    """Apply a targeted edit to a single social field (X, Threads, LinkedIn content,
    or a Reddit OP body / reply — platform 'reddit-op' or 'reddit-reply').

    Operates on the raw content string, preserving any '---' thread separators.
    Returns only the edited content.
    """
    plat_line = f"PLATFORM: {platform}\n\n" if platform else ""
    extra = _SOCIAL_EXTRA.get(platform or "")
    if extra:
        plat_line += extra + "\n\n"
    client = OpenRouterClient()
    response = _messages_create_with_retry(client,
        model=OPENROUTER_MODEL,
        max_tokens=2048,
        system=_SOCIAL_EDIT_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Apply this edit to the following social post.\n\n"
                f"{plat_line}"
                f"INSTRUCTION: {instruction}\n\n"
                f"POST TEXT:\n---\n{content}\n---\n\n"
                f"Return only the edited text, preserving line breaks and any '---' thread separators."
            )
        }]
    )
    return response.content[0].text.strip()


def ai_edit_full(content: str, instruction: str) -> str:
    """Apply a targeted edit to the full post content.

    Args:
        content: Full markdown body of the post (no frontmatter)
        instruction: What to change across the post
    """
    client = OpenRouterClient()
    response = _messages_create_with_retry(client,
        model=OPENROUTER_MODEL,
        max_tokens=8192,
        system=_EDIT_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Apply this edit to the following blog post:\n\n"
                f"INSTRUCTION: {instruction}\n\n"
                f"FULL CONTENT:\n---\n{content}\n---\n\n"
                f"Return only the complete edited content, preserving all markdown formatting and internal links."
            )
        }]
    )
    return response.content[0].text.strip()
