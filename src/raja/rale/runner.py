from __future__ import annotations

import click

from .console import Console
from .phase1 import run_phase1
from .phase2 import run_phase2
from .phase3 import run_phase3
from .state import RunMode, SessionState


def _pause_if_manual(mode: RunMode) -> None:
    if mode == "manual":
        click.prompt("Press Enter to continue", default="", show_default=False)


def run_all(state: SessionState, mode: RunMode, console: Console) -> None:
    run_phase1(state, mode, console)
    _pause_if_manual(mode)

    run_phase2(state, console)
    _pause_if_manual(mode)

    run_phase3(state, console)
