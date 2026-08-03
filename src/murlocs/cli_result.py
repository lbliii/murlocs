from __future__ import annotations

from typing import Any, Literal


class CommandResult(dict[str, Any]):
    """Structured result with terminal-only presentation and exit metadata."""

    __slots__ = ("exit_code", "terminal_stream", "terminal_text")

    def __init__(
        self,
        payload: dict[str, Any],
        *,
        terminal_text: str,
        exit_code: int = 0,
        terminal_stream: Literal["stdout", "stderr"] = "stdout",
    ) -> None:
        super().__init__(payload)
        self.terminal_text = terminal_text
        self.exit_code = exit_code
        self.terminal_stream = terminal_stream
