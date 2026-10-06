"""Reno PM app API + installable web app (see docs/APP_PLAN.md).

One process: /api/* is the JSON API; everything else is the built React
app (web/dist), with unknown paths falling back to index.html so client-side
routes like /schedule work on reload.
"""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from api.routers import admin, auth, budget, journal, properties, public, schedule, sow

app = FastAPI(title="Reno PM", docs_url=None, redoc_url=None, openapi_url=None)

for module in (auth, properties, schedule, budget, journal, sow, admin, public):
    app.include_router(module.router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    # Log the real error server-side; never leak internals to the client.
    print(f"ERROR {request.method} {request.url.path}: {exc!r}")
    return JSONResponse({"detail": "Something went wrong on our side"}, status_code=500)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


WEB_DIST = Path(os.environ.get("WEB_DIST", Path(__file__).resolve().parent.parent / "web" / "dist"))


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "Not found")
    if not WEB_DIST.exists():
        return JSONResponse({"detail": "Front end not built"}, status_code=503)
    candidate = (WEB_DIST / path).resolve()
    if path and candidate.is_file() and WEB_DIST.resolve() in candidate.parents:
        headers = {}
        if path in ("sw.js", "manifest.webmanifest"):
            headers["Cache-Control"] = "no-cache"
        elif path.startswith("assets/"):
            headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return FileResponse(candidate, headers=headers)
    return FileResponse(WEB_DIST / "index.html", headers={"Cache-Control": "no-cache"})
