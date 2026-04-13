"""Hybrid reply engine: keyword rules first, Claude AI as fallback."""

from reply import claude_client, rules


def process_message(text: str) -> dict:
    """
    Return a dict with keys:
      - source: "rules" or "claude"
      - content: the reply string
    """
    reply = rules.match(text)
    if reply is not None:
        return {"source": "rules", "content": reply}

    reply = claude_client.generate(text)
    return {"source": "claude", "content": reply}
