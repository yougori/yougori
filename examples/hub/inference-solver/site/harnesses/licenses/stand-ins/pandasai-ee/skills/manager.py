"""Yougori's stand-in for PandasAI's enterprise skills manager (see __init__.py): PandasAI's core
asks it for the skills to give the model, and gets none."""
from typing import Any, List, Optional

from pandasai.ee.skills import UNAVAILABLE


class SkillsManager:
    """No skills: they are part of PandasAI's Enterprise edition."""

    @classmethod
    def add_skills(cls, *skills: Any) -> None:
        raise RuntimeError(UNAVAILABLE)

    @classmethod
    def skill_exists(cls, name: str) -> bool:
        return False

    @classmethod
    def has_skills(cls) -> bool:
        return False

    @classmethod
    def get_skill_by_func_name(cls, name: str) -> Optional[Any]:
        return None

    @classmethod
    def get_skills(cls) -> List[Any]:
        return []

    @classmethod
    def clear_skills(cls) -> None:
        return None

    def __str__(self) -> str:
        return ""
