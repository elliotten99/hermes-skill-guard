from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class FakeHermesContext:
    """Mimics the real Hermes PluginContext API with invoke_hook tracking.

    Shared across integration tests to exercise plugin registration and
    hook invocation through the same public contract.
    """

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}
        self.hooks: dict[str, Any] = {}
        self.commands: dict[str, Any] = {}
        self.skills: dict[str, Any] = {}
        self._hook_calls: list[dict[str, Any]] = []

    def register_tool(
        self,
        name: str,
        toolset: str,
        schema: dict[str, Any],
        handler: Any,
        check_fn: Any = None,
        requires_env: Sequence[str] | None = None,
        is_async: bool = False,
        description: str = "",
        emoji: str = "",
        override: bool = False,
    ) -> None:
        self.tools[name] = {
            "handler": handler,
            "toolset": toolset,
            "schema": schema,
            "description": description,
        }

    def register_hook(self, name: str, handler: Any) -> None:
        self.hooks[name] = handler

    def register_command(
        self,
        name: str,
        handler: Any,
        description: str = "",
        args_hint: str = "",
    ) -> None:
        self.commands[name] = {
            "handler": handler,
            "description": description,
            "args_hint": args_hint,
        }

    def register_cli_command(
        self,
        name: str,
        help: str,
        setup_fn: Any,
        handler_fn: Any | None = None,
        description: str = "",
    ) -> None:
        self.commands[f"cli:{name}"] = {
            "help": help,
            "setup_fn": setup_fn,
            "handler_fn": handler_fn,
            "description": description,
        }

    def register_skill(self, name: str, path: Path, description: str = "") -> None:
        self.skills[name] = {"path": path, "description": description}

    def invoke_hook(self, name: str, **kwargs: Any) -> Any:
        """Simulate Hermes invoke_hook: record call and trigger handler."""
        self._hook_calls.append({"name": name, "kwargs": dict(kwargs)})
        handler = self.hooks.get(name)
        if handler is not None:
            return handler(**kwargs)
        return None

    def hook_calls(self, name: str) -> list[dict[str, Any]]:
        """Return all recorded invocations of a named hook."""
        return [c for c in self._hook_calls if c["name"] == name]


@pytest.fixture
def fake_ctx() -> FakeHermesContext:
    """Provide a fresh FakeHermesContext for each test."""
    return FakeHermesContext()
