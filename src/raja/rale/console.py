from __future__ import annotations

# mypy: disable-error-code=no-redef
from typing import Any

try:  # pragma: no cover - depends on optional runtime dependency
    from rich.console import Console as RichConsole  # type: ignore[import-not-found]
    from rich.table import Table as RichTable  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - exercised only when rich is unavailable
    RichConsole = None
    RichTable = None


if RichConsole is not None and RichTable is not None:
    Console = RichConsole
    Table = RichTable
else:

    class Table:
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

    class Console:
        def rule(self, title: str) -> None:
            print(f"=== {title} ===")

        def print(self, value: Any = "") -> None:
            print(value)
