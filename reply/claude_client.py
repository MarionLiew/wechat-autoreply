"""Thin wrapper around the Anthropic SDK."""

import anthropic

from config import settings

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.claude_api_key)
    return _client


def generate(message: str) -> str:
    """Send message to Claude and return the reply text."""
    response = _get_client().messages.create(
        model=settings.claude_model,
        max_tokens=1024,
        system=settings.system_prompt,
        messages=[{"role": "user", "content": message}],
    )
    return response.content[0].text
