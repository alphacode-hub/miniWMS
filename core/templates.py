# core/templates.py
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.templating import Jinja2Templates


def create_templates(base_dir: Path) -> Jinja2Templates:
    """
    Crea y configura el motor de templates Jinja2 para ORBION.
    - Define directory raíz /templates
    - Registra globals enterprise (UTC, nombre app, env, etc.)
    """
    templates = Jinja2Templates(directory=str(base_dir / "templates"))

    # Globals (fuente de verdad: servidor, UTC)
    templates.env.globals.update(
        {
            "app_name": "ORBION",
            "utc_now": lambda: datetime.now(timezone.utc),  # callable
            "app_year": lambda: datetime.now(timezone.utc).year,  # callable
        }
    )

    return templates
