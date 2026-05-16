from __future__ import annotations

from pathlib import Path


PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def render_prompt(template_name: str, **values: object) -> str:
    template = (PROMPTS_DIR / template_name).read_text(encoding="utf-8")
    return template.format(**{key: str(value) for key, value in values.items()}).strip()

