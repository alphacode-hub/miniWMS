# core/web.py
from __future__ import annotations

from pathlib import Path
from jinja2 import ChoiceLoader, FileSystemLoader

from core.templates import create_templates
from core.formatting import clp, cl_num, cl_datetime, cl_date  # ✅ NUEVO

BASE_DIR = Path(__file__).resolve().parent.parent  # raíz del repo
templates = create_templates(BASE_DIR)

# ✅ Registrar filtros globales (Chile-first)
templates.env.filters["clp"] = clp
templates.env.filters["cl_num"] = cl_num
templates.env.filters["cl_datetime"] = cl_datetime
templates.env.filters["cl_date"] = cl_date


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
        for l in loaders:
            sp = getattr(l, "searchpath", None)
            if sp and str(path) in list(sp):
                return
        templates.env.loader = ChoiceLoader([*loaders, new_loader])
    else:
        templates.env.loader = ChoiceLoader([loader, new_loader])
