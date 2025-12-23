# core/middleware/audit_context.py
"""
Middleware de contexto de auditoría – ORBION (enterprise)

✔ request_id único
✔ ip
✔ user_agent
✔ disponible vía request.state.audit_ctx
"""

from __future__ import annotations

import uuid
from fastapi import Request
from starlette.responses import Response


async def audit_context_middleware(request: Request, call_next) -> Response:
    request_id = str(uuid.uuid4())

    ip = (
        request.headers.get("x-forwarded-for")
        or request.client.host
        if request.client
        else None
    )

    request.state.audit_ctx = {
        "request_id": request_id,
        "ip": ip,
        "user_agent": request.headers.get("user-agent"),
        "path": request.url.path,
        "method": request.method,
    }

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response
