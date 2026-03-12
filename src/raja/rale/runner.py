from __future__ import annotations

import click

from .console import Console
from .authorize import run_authorize
from .fetch import run_fetch
from .select import run_select
from .state import RunMode, SessionState


def _pause_if_manual(mode: RunMode) -> None:
    if mode == "manual":
        click.prompt("Press Enter to continue", default="", show_default=False)


def run_all(state: SessionState, mode: RunMode, console: Console) -> None:
    run_select(state, mode, console)
    _pause_if_manual(mode)

    run_authorize(state, console)
    _pause_if_manual(mode)

    run_fetch(state, console)
