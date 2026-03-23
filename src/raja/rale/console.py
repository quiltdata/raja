from __future__ import annotations

from typing import Any

__all__ = ["Console", "Table"]

try:
    from rich.console import Console
    from rich.table import Table
except ImportError:  # pragma: no cover - exercised only when rich is unavailable

    class Table:  # type: ignore[no-redef]
        def __init__(self, title: str = "") -> None:
            self.title = title
            self._rows: list[list[str]] = []

        def add_column(self, _name: str, justify: str | None = None) -> None:
            _ = justify

        def add_row(self, *values: str) -> None:
            self._rows.append([str(value) for value in values])

        def __str__(self) -> str:
            lines = [self.title] if self.title else []
            for row in self._rows:
                lines.append(" | ".join(row))
            return "\n".join(lines)

    class Console:  # type: ignore[no-redef]
        def rule(self, title: str) -> None:
            print(f"=== {title} ===")

        def print(self, value: Any = "") -> None:
            print(value)
