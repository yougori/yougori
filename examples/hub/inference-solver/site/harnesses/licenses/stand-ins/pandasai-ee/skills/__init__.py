"""Yougori's stand-in for PandasAI's enterprise skills (pandasai/ee), which Yougori does not pass on.

PandasAI's core is MIT; its pandasai/ee folder is under PandasAI's Enterprise licence, whose
production use needs paid seats. PandasAI's core imports the skills from there, so this stand-in
takes its place in Yougori's PandasAI pack: PandasAI runs without skills (an enterprise feature),
and everything else in it works as published. Written for Yougori (MIT, like the rest of the
inference-solver template); no PandasAI enterprise code is in it.
"""
from typing import Any, Callable

UNAVAILABLE = ("Skills are part of PandasAI's Enterprise edition, which Yougori's PandasAI does not include. "
               "PandasAI works without them.")


class SkillType:
    """A skill as PandasAI's core reads one; Yougori's PandasAI has none."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError(UNAVAILABLE)


def skill(*args: Any) -> Callable:
    """PandasAI's @skill: not available here, which it says."""
    raise RuntimeError(UNAVAILABLE)
