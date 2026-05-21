"""Small compatibility adapter around Hermes plugin context objects."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


class HermesAdapter:
    """Register tools, hooks, commands, and skills on a Hermes context.

    Hermes plugin APIs may evolve. The adapter keeps registration defensive:
    if a method is not present, registration is skipped instead of failing the
    whole plugin.
    """

    def __init__(self, ctx: object) -> None:
        self.ctx = ctx

    def register_tool(
        self,
        name: str,
        handler: Callable[..., str],
        description: str,
        schema: dict[str, Any] | None = None,
        toolset: str = "skill-guard",
    ) -> None:
        """Register a tool on the Hermes context.

        Real Hermes ``register_tool`` requires *toolset* and *schema*.
        We build a minimal OpenAI-function schema from *description* when none
        is provided so the plugin works out of the box.
        """
        method = getattr(self.ctx, "register_tool", None)
        if not callable(method):
            return
        if schema is None:
            schema = {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": {}},
            }
        try:
            method(
                name=name,
                toolset=toolset,
                schema=schema,
                handler=handler,
                description=description,
            )
        except TypeError:
            # Fallback for fake contexts or older APIs that don't accept schema/toolset
            try:
                method(name, handler, description)
            except TypeError:
                method(name=name, handler=handler, description=description)

    def register_hook(self, name: str, handler: Callable[..., Any]) -> None:
        method = getattr(self.ctx, "register_hook", None)
        if callable(method):
            try:
                method(name=name, handler=handler)
            except TypeError:
                method(name, handler)

    def register_slash_command(
        self,
        name: str,
        handler: Callable[..., str],
        description: str,
        args_hint: str = "",
    ) -> None:
        """Register an in-session slash command (e.g. ``/skill-guard report``).

        Maps to Hermes ``ctx.register_command()``.
        """
        method = getattr(self.ctx, "register_command", None)
        if not callable(method):
            return
        clean = name.lower().strip().lstrip("/").replace(" ", "-")
        try:
            method(name=clean, handler=handler, description=description, args_hint=args_hint)
        except TypeError:
            try:
                method(clean, handler, description, args_hint)
            except TypeError:
                method(name=clean, handler=handler, description=description)

    def register_cli_command(
        self,
        name: str,
        help_text: str,
        setup_fn: Callable[..., Any],
        handler_fn: Callable[..., Any] | None = None,
        description: str = "",
    ) -> None:
        """Register a CLI subcommand (e.g. ``hermes skill-guard ...``).

        Maps to Hermes ``ctx.register_cli_command()``.
        """
        method = getattr(self.ctx, "register_cli_command", None)
        if not callable(method):
            return
        try:
            method(
                name=name,
                help=help_text,
                setup_fn=setup_fn,
                handler_fn=handler_fn,
                description=description,
            )
        except TypeError:
            method(name, help_text, setup_fn, handler_fn, description)

    def register_skill(self, name: str, path: Path) -> None:
        method = getattr(self.ctx, "register_skill", None)
        if callable(method):
            try:
                method(name=name, path=path)
            except TypeError:
                try:
                    method(name, path)
                except TypeError:
                    method(name=name, path=str(path))
