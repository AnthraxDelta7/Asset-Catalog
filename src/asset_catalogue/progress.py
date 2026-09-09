"""Overall job progress, reported by the job rather than guessed from its output.

A long job runs through several phases with unrelated counts of their
own -- "Pack 1/2", "frame 5/24", "Converted 3/6". Reading a percentage
out of those is what made the bar jump backwards: they answer "how far
through this phase", and nothing in the text says how much of the whole
job a phase represents. Only the job knows that, so the job says it.

The helpers below take whatever was passed as `on_progress`. That's
usually a plain `Callable[[str], None]` -- the CLI passes one, tests pass
lambdas -- so a job can call these unconditionally and they degrade to
emitting text (or nothing) when the receiver has no progress model.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SupportsProgress(Protocol):
    def begin(self, total: int) -> None: ...

    def advance(self, text: str, done: int | None = None) -> None: ...


def begin(on_progress, total: int) -> None:
    """Declares how many units of work the whole job will do.

    A "unit" is whatever the job wants the bar to measure, and it's fine
    for one asset to be several units -- a model that gets copied and
    then turned into a scene is genuinely two pieces of work, and
    counting it as two keeps the bar moving through both phases rather
    than sitting at 100% through the second.
    """
    if isinstance(on_progress, SupportsProgress):
        on_progress.begin(total)


def advance(on_progress, text: str, done: int | None = None) -> None:
    """Reports one unit finished, along with the line to show for it.

    Falls back to a plain text report when the receiver has no progress
    model, so a job calls this in place of on_progress(text) rather than
    branching on what it was handed.
    """
    if isinstance(on_progress, SupportsProgress):
        on_progress.advance(text, done)
    elif on_progress is not None:
        on_progress(text)
