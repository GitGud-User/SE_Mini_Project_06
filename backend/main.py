"""SPMS FastAPI application entry point.

Run locally with:  uvicorn backend.main:app --reload
Then open http://127.0.0.1:8000  (web UI)  or  http://127.0.0.1:8000/docs  (API docs).

Every module inside backend/ that defines a `router` is added automatically,
so teammates can add new features in their own files without editing this one.
"""
import asyncio
import importlib
import logging
import os
import pkgutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

import backend
from backend import auth
from backend.db import SessionLocal, init_db

STATIC_DIR = Path(__file__).parent / "static"


def load_routers(application: FastAPI) -> list[str]:
    """Import every backend module and include its `router` if it has one."""
    loaded = []
    for module_info in pkgutil.iter_modules(backend.__path__):
        if module_info.name in {"main", "seed", "volume_seed"}:
            continue
        module = importlib.import_module(f"backend.{module_info.name}")
        router = getattr(module, "router", None)
        if router is not None:
            application.include_router(router)
            loaded.append(module_info.name)
    return loaded


def run_sweep() -> None:
    """Every minute: release no-show reservations (F-033) and send 15-minute reminders (F-072)."""
    from backend import notifications, reservation_changes
    with SessionLocal() as db:
        reservation_changes.release_no_shows(db)
        notifications.send_due_reminders(db)


async def sweeper() -> None:
    interval = float(os.getenv("SPMS_SWEEP_SECONDS", "60"))
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(run_sweep)
        except Exception:  # keep the loop alive; the next minute retries
            logging.getLogger("spms").exception("background sweep failed")


@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    with SessionLocal() as db:
        auth.seed_default_users(db)
    task = asyncio.create_task(sweeper())
    yield
    task.cancel()


app = FastAPI(title="Smart Parking Management System", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request, call_next):
    """SR-004: basic browser hardening headers on every response."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response

LOADED_MODULES = load_routers(app)

if STATIC_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")


@app.get("/", include_in_schema=False)
def root():
    if STATIC_DIR.is_dir():
        return RedirectResponse("/ui/")
    return {"service": "SPMS", "docs": "/docs"}


@app.get("/health")
def health():
    """Simple check that the API is running."""
    return {"status": "ok", "service": "SPMS"}
