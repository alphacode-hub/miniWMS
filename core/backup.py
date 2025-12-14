# core/backup.py
"""
Backup de base de datos a nivel aplicación.

✔ Se mantiene SOLO para SQLite (desarrollo / pruebas / demo offline)
✖ NO reemplaza backups de PostgreSQL en producción (eso es responsabilidad
  del proveedor: Railway / Azure / cron / snapshots)

Este archivo es válido y útil en un SaaS enterprise como:
- entorno DEV
- entorno DEMO
- modo offline / single-tenant
"""

from datetime import datetime
from pathlib import Path
import shutil

from core.config import settings
from core.logging_config import logger


# ============================
#   CONFIGURACIÓN
# ============================

BASE_DIR = Path(__file__).resolve().parent.parent
BACKUP_DIR = BASE_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# Cuántos backups SQLite mantener (rotación simple)
MAX_BACKUPS = 3


# ============================
#   HELPERS
# ============================

def _is_sqlite_url(url: str) -> bool:
    """
    Detecta si DATABASE_URL corresponde a SQLite.
    """
    return url.startswith("sqlite:///")


def _get_sqlite_db_path(url: str) -> Path:
    """
    Extrae la ruta del archivo SQLite desde DATABASE_URL.
    """
    return Path(url.replace("sqlite:///", "", 1)).resolve()


# ============================
#   BACKUP SQLITE
# ============================

def backup_sqlite_db() -> tuple[bool, str | None]:
    """
    Crea un backup de la BD SQLite SOLO si DATABASE_URL es SQLite.

    Retorna:
        (True, ruta_backup)  -> backup creado
        (False, None)        -> no aplica o error
    """
    db_url = settings.DATABASE_URL

    if not _is_sqlite_url(db_url):
        logger.debug(
            "[BACKUP] DATABASE_URL no es SQLite. "
            "Backup a nivel aplicación no requerido."
        )
        return False, None

    db_path = _get_sqlite_db_path(db_url)

    if not db_path.exists():
        logger.error(f"[BACKUP] Archivo SQLite no encontrado: {db_path}")
        return False, None

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"{db_path.stem}_{timestamp}{db_path.suffix}"
    backup_path = BACKUP_DIR / backup_filename

    try:
        shutil.copy2(db_path, backup_path)
        logger.info(f"[BACKUP] Backup SQLite creado: {backup_path}")

        _cleanup_old_backups()

        return True, str(backup_path)

    except Exception as exc:
        logger.exception(f"[BACKUP] Error creando backup SQLite: {exc}")
        return False, None


# ============================
#   ROTACIÓN
# ============================

def _cleanup_old_backups() -> None:
    """
    Mantiene solo los últimos MAX_BACKUPS backups SQLite.
    """
    backups = sorted(
        BACKUP_DIR.glob("*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    for old_backup in backups[MAX_BACKUPS:]:
        try:
            old_backup.unlink()
            logger.info(f"[BACKUP] Backup eliminado: {old_backup.name}")
        except Exception as exc:
            logger.warning(
                f"[BACKUP] No se pudo eliminar backup {old_backup.name}: {exc}"
            )
