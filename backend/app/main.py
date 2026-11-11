"""MineNode – application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import (APP_NAME, BEDROCK_HOST, BEDROCK_PORT, PROXY_HOST, PROXY_PORT, STATIC_DIR, VERSION,
                     VOICE_HOST, VOICE_PORT, ensure_dirs)

ensure_dirs()

from . import api, auth, net  # noqa: E402  (data dirs must exist first)
from .backups import BackupManager  # noqa: E402
from .bedrock import BedrockProxy  # noqa: E402
from .manager import Manager  # noqa: E402
from .proxy import MinecraftProxy  # noqa: E402
from .voice import VoiceProxy  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("minenode")


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.bootstrap()
    manager = Manager()
    manager.backups = BackupManager(manager)
    manager.proxy = MinecraftProxy(manager, PROXY_HOST, PROXY_PORT)
    manager.voice = VoiceProxy(manager, VOICE_HOST, VOICE_PORT)
    manager.bedrock = BedrockProxy(manager, BEDROCK_HOST, BEDROCK_PORT)
    api.manager = manager
    await manager.proxy.start()
    await manager.voice.start()
    await manager.startup()
    manager.bedrock.launch()
    log.info("%s %s ready – %d server(s) configured", APP_NAME, VERSION, len(manager.instances))
    try:
        yield
    finally:
        log.info("Shutting down – stopping servers gracefully…")
        await manager.bedrock.close()
        await manager.shutdown()
        await manager.proxy.stop()
        await manager.voice.stop()
        await net.close()


app = FastAPI(title=APP_NAME, version=VERSION, lifespan=lifespan, docs_url="/api/docs", redoc_url=None,
              openapi_url="/api/openapi.json")


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # CSRF protection: state-changing API calls must carry a custom header, which browsers
    # never attach to cross-site form posts and which CORS blocks for foreign origins.
    if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("x-requested-with") != "minenode":
            return JSONResponse({"detail": "Brak nagłówka X-Requested-With"}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


app.include_router(api.router)
app.include_router(api.public_router)


@app.get("/api/health")
async def health():
    return {"ok": True, "version": VERSION}


# ----------------------------------------------------------------- SPA
if (STATIC_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    candidate = (STATIC_DIR / full_path).resolve()
    if full_path and candidate.is_file() and candidate.is_relative_to(Path(STATIC_DIR).resolve()):
        return FileResponse(candidate)
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"detail": "Frontend not built. Run `npm run build` in /frontend."}, status_code=503)
