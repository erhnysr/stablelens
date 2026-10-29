"""StableLens: read-only Arc Network tools for Hermes Agent.

Registers six tools that read Arc Network's public JSON-RPC endpoints. Nothing here signs,
sends, stores keys or writes files, and registration itself makes no network calls.
"""

from __future__ import annotations

from typing import Any

from . import schemas, tools

TOOLSET = "stablelens"


def _read_secret(name: str) -> str:
    """A private RPC URL, read through Hermes's profile-aware secret scope (never from a file)."""
    try:
        from agent.secret_scope import get_secret
    except ImportError:  # not running inside Hermes: nothing is configured
        return ""
    return get_secret(name, "") or ""


def register(ctx: Any) -> None:
    def settings(key: str, default: Any) -> Any:
        try:
            value = ctx.get_config(key, default=default)
        except Exception:  # an older host without plugin settings: use the defaults
            return default
        return default if value is None else value

    for schema in schemas.ALL:
        ctx.register_tool(
            name=schema["name"],
            toolset=TOOLSET,
            schema=schema,
            handler=tools.make_handler(schema["name"], settings, secrets=_read_secret),
            description=schema["description"],
            emoji="🔎",
        )
