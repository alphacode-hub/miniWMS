# core/web.py
from __future__ import annotations

from pathlib import Path
from jinja2 import ChoiceLoader, FileSystemLoader

from core.templates import create_templates

BASE_DIR = Path(__file__).resolve().parent.parent  # raíz del repo
templates = create_templates(BASE_DIR)

# ✅ Registrar templates de módulos (sin crear otro Jinja2Templates)
def add_template_dir(path: Path) -> None:
    loader = templates.env.loader

    new_loader = FileSystemLoader(str(path))

    if loader is None:
        templates.env.loader = new_loader
        return

    # Evitar duplicar loaders
    if isinstance(loader, ChoiceLoader):
        loaders = list(loader.loaders)
        if any(getattr(l, "searchpath", None) == [str(path)] for l in loaders):
            return
        templates.env.loader = ChoiceLoader([*loaders, new_loader])
    else:
        templates.env.loader = ChoiceLoader([loader, new_loader])
