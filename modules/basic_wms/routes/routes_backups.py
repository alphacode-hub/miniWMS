# routes_backups.py
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from core.backup import backup_sqlite_db
from core.security import require_superadmin_dep

router = APIRouter(
    prefix="/admin/backups",
    tags=["backups"],
)


@router.post("/sqlite")
async def trigger_sqlite_backup(user = Depends(require_superadmin_dep)):
    ok, path = backup_sqlite_db()
    if not ok:
        return JSONResponse(
            status_code=400,
            content={"detail": "No se pudo crear el backup o no aplica para este motor de BD."},
        )

    return {"detail": "Backup creado correctamente.", "path": path}
