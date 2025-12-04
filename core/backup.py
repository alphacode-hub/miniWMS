# backup.py
from datetime import datetime
from pathlib import Path
import shutil

from core.config import settings
from core.logging_config import logger


BACKUP_DIR = Path("backups")
BACKUP_DIR.mkdir(exist_ok=True)

# Cuántos backups mantener (los más recientes)
MAX_BACKUPS = 2


def _is_sqlite_url(url: str) -> bool:
    return url.startswith("sqlite:///") or url.startswith("sqlite:///")


def backup_sqlite_db() -> tuple[bool, str | None]:
    """
    Crea un backup de la BD SQLite si DATABASE_URL es de tipo sqlite.
    Devuelve (ok, path_str) donde ok indica si se realizó el backup
    y path_str es la ruta del archivo creado (o None si no aplica).
    """
    db_url = settings.DATABASE_URL

    if not _is_sqlite_url(db_url):
        logger.warning(
            "[BACKUP] backup_sqlite_db() llamado pero DATABASE_URL no es SQLite "
            f"({db_url}). Se omite backup a nivel aplicación."
        )
        return False, None

    # Extraer la ruta del archivo a partir del URL sqlite:///...
    db_path_str = db_url.replace("sqlite:///", "", 1)
    db_path = Path(db_path_str).resolve()

    if not db_path.exists():
        logger.error(f"[BACKUP] Archivo de BD SQLite no existe: {db_path}")
        return False, None

    # Nombre del backup: miniWMS_YYYYMMDD_HHMMSS.db
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"{db_path.stem}_{timestamp}{db_path.suffix}"
    backup_path = BACKUP_DIR / backup_filename

    try:
        shutil.copy2(db_path, backup_path)
        logger.info(f"[BACKUP] Backup SQLite creado: {backup_path}")

        _cleanup_old_backups()

        return True, str(backup_path)
    except Exception as e:
        logger.error(f"[BACKUP] Error al crear backup SQLite: {e}")
        return False, None


def _cleanup_old_backups():
    """
    Mantiene solo los últimos MAX_BACKUPS archivos en BACKUP_DIR.
    """
    backups = sorted(
        BACKUP_DIR.glob("*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if len(backups) <= MAX_BACKUPS:
        return

    for old_backup in backups[MAX_BACKUPS:]:
        try:
            old_backup.unlink()
            logger.info(f"[BACKUP] Backup antiguo eliminado: {old_backup}")
        except Exception as e:
            logger.warning(f"[BACKUP] No se pudo eliminar backup antiguo {old_backup}: {e}")
