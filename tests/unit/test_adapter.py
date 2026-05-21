"""Unit tests for HermesAdapter compatibility shim.

Exercises both the modern kwargs API path and the older positional-only
fallback paths via fake context objects with controlled method signatures.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hermes_skill_guard.hermes.adapter import HermesAdapter


def _noop(*_args: Any, **_kwargs: Any) -> str:
    return ""


class _EmptyCtx:
    """A context object that exposes no registration methods."""


# ---------------------------------------------------------------------------
# register_tool
# ---------------------------------------------------------------------------


class TestRegisterTool:
    def test_modern_kwargs_path(self) -> None:
        captured: dict[str, Any] = {}

        class Ctx:
            def register_tool(
                self,
                *,
                name: str,
                toolset: str,
                schema: dict[str, Any],
                handler: Any,
                description: str,
            ) -> None:
                captured.update(
                    name=name,
                    toolset=toolset,
                    schema=schema,
                    handler=handler,
                    description=description,
                )

        adapter = HermesAdapter(Ctx())
        adapter.register_tool("my_tool", _noop, "desc")
        assert captured["name"] == "my_tool"
        assert captured["toolset"] == "skill-guard"
        assert captured["schema"]["name"] == "my_tool"
        assert captured["description"] == "desc"

    def test_supplied_schema_is_preserved(self) -> None:
        captured: dict[str, Any] = {}

        class Ctx:
            def register_tool(
                self,
                *,
                name: str,
                toolset: str,
                schema: dict[str, Any],
                handler: Any,
                description: str,
            ) -> None:
                captured["schema"] = schema

        adapter = HermesAdapter(Ctx())
        custom = {"name": "x", "description": "y", "parameters": {"type": "object"}}
        adapter.register_tool("x", _noop, "y", schema=custom)
        assert captured["schema"] is custom

    def test_fallback_three_positional_args(self) -> None:
        """Older API only accepts (name, handler, description) positionally."""
        captured: dict[str, Any] = {}

        class Ctx:
            def register_tool(self, name: str, handler: Any, description: str) -> None:
                captured.update(name=name, handler=handler, description=description)

        adapter = HermesAdapter(Ctx())
        adapter.register_tool("my_tool", _noop, "desc")
        assert captured == {"name": "my_tool", "handler": _noop, "description": "desc"}

    def test_fallback_kwargs_only(self) -> None:
        """Even older API accepts only (name=, handler=, description=) by kwarg."""
        captured: dict[str, Any] = {}

        class Ctx:
            def register_tool(self, *, name: str, handler: Any, description: str) -> None:
                captured.update(name=name, handler=handler, description=description)

        adapter = HermesAdapter(Ctx())
        adapter.register_tool("my_tool", _noop, "desc")
        assert captured == {"name": "my_tool", "handler": _noop, "description": "desc"}

    def test_missing_method_is_noop(self) -> None:
        adapter = HermesAdapter(_EmptyCtx())
        # Should not raise
        adapter.register_tool("x", _noop, "y")

    def test_non_callable_attribute_is_noop(self) -> None:
        class Ctx:
            register_tool = "not callable"

        adapter = HermesAdapter(Ctx())
        adapter.register_tool("x", _noop, "y")


# ---------------------------------------------------------------------------
# register_hook
# ---------------------------------------------------------------------------


class TestRegisterHook:
    def test_modern_kwargs_path(self) -> None:
        captured: dict[str, Any] = {}

        class Ctx:
            def register_hook(self, *, name: str, handler: Any) -> None:
                captured.update(name=name, handler=handler)

        adapter = HermesAdapter(Ctx())
        adapter.register_hook("pre_tool_call", _noop)
        assert captured == {"name": "pre_tool_call", "handler": _noop}

    def test_fallback_positional(self) -> None:
        captured: list[tuple[str, Any]] = []

        class Ctx:
            def register_hook(self, name: str, handler: Any) -> None:
                # Force the kwarg path to fail by raising TypeError on the
                # signature mismatch from the adapter trying name=/handler=.
                captured.append((name, handler))

        # Construct an instance whose register_hook only accepts positional
        # to force TypeError. We need to wrap to ensure kwargs path fails.
        class StrictPositionalCtx:
            def register_hook(self, name, handler, /):  # type: ignore[no-untyped-def]
                captured.append((name, handler))

        adapter = HermesAdapter(StrictPositionalCtx())
        adapter.register_hook("pre", _noop)
        assert captured == [("pre", _noop)]

    def test_missing_method_is_noop(self) -> None:
        adapter = HermesAdapter(_EmptyCtx())
        adapter.register_hook("any", _noop)


# ---------------------------------------------------------------------------
# register_slash_command
# ---------------------------------------------------------------------------


class TestRegisterSlashCommand:
    def test_modern_kwargs_path(self) -> None:
        captured: dict[str, Any] = {}

        class Ctx:
            def register_command(
                self,
                *,
                name: str,
                handler: Any,
                description: str,
                args_hint: str,
            ) -> None:
                captured.update(
                    name=name, handler=handler, description=description, args_hint=args_hint
                )

        adapter = HermesAdapter(Ctx())
        adapter.register_slash_command("/My Cmd", _noop, "desc", args_hint="[opts]")
        # Name should be normalized: lower, stripped, leading slash removed, spaces -> dashes
        assert captured["name"] == "my-cmd"
        assert captured["args_hint"] == "[opts]"

    def test_fallback_positional(self) -> None:
        captured: list[tuple[Any, ...]] = []

        class Ctx:
            def register_command(self, name, handler, description, args_hint, /):  # type: ignore[no-untyped-def]
                captured.append((name, handler, description, args_hint))

        adapter = HermesAdapter(Ctx())
        adapter.register_slash_command("cmd", _noop, "desc", args_hint="hint")
        assert captured == [("cmd", _noop, "desc", "hint")]

    def test_fallback_legacy_three_kwargs(self) -> None:
        """Very old API only knows name/handler/description (no args_hint)."""
        captured: dict[str, Any] = {}

        class Ctx:
            def register_command(self, *, name: str, handler: Any, description: str) -> None:
                captured.update(name=name, handler=handler, description=description)

        adapter = HermesAdapter(Ctx())
        adapter.register_slash_command("cmd", _noop, "desc", args_hint="hint")
        assert captured == {"name": "cmd", "handler": _noop, "description": "desc"}

    def test_missing_method_is_noop(self) -> None:
        adapter = HermesAdapter(_EmptyCtx())
        adapter.register_slash_command("cmd", _noop, "desc")


# ---------------------------------------------------------------------------
# register_cli_command
# ---------------------------------------------------------------------------


class TestRegisterCliCommand:
    def test_modern_kwargs_path(self) -> None:
        captured: dict[str, Any] = {}

        class Ctx:
            def register_cli_command(
                self,
                *,
                name: str,
                help: str,
                setup_fn: Any,
                handler_fn: Any,
                description: str,
            ) -> None:
                captured.update(
                    name=name,
                    help=help,
                    setup_fn=setup_fn,
                    handler_fn=handler_fn,
                    description=description,
                )

        adapter = HermesAdapter(Ctx())
        adapter.register_cli_command(
            "skill-guard", "the help", _noop, handler_fn=_noop, description="d"
        )
        assert captured["name"] == "skill-guard"
        assert captured["help"] == "the help"
        assert captured["description"] == "d"

    def test_fallback_positional(self) -> None:
        captured: list[tuple[Any, ...]] = []

        class Ctx:
            def register_cli_command(self, name, help_text, setup_fn, handler_fn, description, /):  # type: ignore[no-untyped-def]
                captured.append((name, help_text, setup_fn, handler_fn, description))

        adapter = HermesAdapter(Ctx())
        adapter.register_cli_command(
            "skill-guard", "help txt", _noop, handler_fn=_noop, description="desc"
        )
        assert captured == [("skill-guard", "help txt", _noop, _noop, "desc")]

    def test_missing_method_is_noop(self) -> None:
        adapter = HermesAdapter(_EmptyCtx())
        adapter.register_cli_command("x", "h", _noop)


# ---------------------------------------------------------------------------
# register_skill
# ---------------------------------------------------------------------------


class TestRegisterSkill:
    def test_modern_kwargs_path(self) -> None:
        captured: dict[str, Any] = {}

        class Ctx:
            def register_skill(self, *, name: str, path: Path) -> None:
                captured.update(name=name, path=path)

        path = Path("/tmp/skill")
        adapter = HermesAdapter(Ctx())
        adapter.register_skill("my-skill", path)
        assert captured == {"name": "my-skill", "path": path}

    def test_fallback_positional(self) -> None:
        captured: list[tuple[str, Path]] = []

        class Ctx:
            def register_skill(self, name, path, /):  # type: ignore[no-untyped-def]
                captured.append((name, path))

        path = Path("/tmp/skill")
        adapter = HermesAdapter(Ctx())
        adapter.register_skill("my-skill", path)
        assert captured == [("my-skill", path)]

    def test_fallback_path_as_string(self) -> None:
        """Oldest API requires `path` as a string, not a Path object."""
        captured: dict[str, Any] = {}
        call_count = {"n": 0}

        class Ctx:
            def register_skill(self, *, name: str, path: str) -> None:
                call_count["n"] += 1
                # First call (with Path) should fail isinstance check via TypeError;
                # we simulate the "older API needs str" path by rejecting Path.
                if isinstance(path, Path):
                    raise TypeError("path must be str")
                captured.update(name=name, path=path)

        adapter = HermesAdapter(Ctx())
        adapter.register_skill("my-skill", Path("/tmp/skill"))
        # First attempt: kwargs with Path -> enters body, raises TypeError (count=1)
        # Second attempt: positional (name, path) -> rejected at signature level
        # before reaching the body (kwargs-only method), count unchanged
        # Third attempt: name=, path=str(path) -> success (count=2)
        assert captured == {"name": "my-skill", "path": "/tmp/skill"}
        assert call_count["n"] == 2

    def test_missing_method_is_noop(self) -> None:
        adapter = HermesAdapter(_EmptyCtx())
        adapter.register_skill("x", Path("/tmp"))
