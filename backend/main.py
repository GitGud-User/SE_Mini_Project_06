"""SPMS FastAPI application entry point.

Run locally with:  uvicorn backend.main:app --reload
Then open http://127.0.0.1:8000  (web UI)  or  http://127.0.0.1:8000/docs  (API docs).

Every module inside backend/ that defines a `router` is added automatically,
so teammates can add new features in their own files without editing this one.
"""
import importlib
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
        if module_info.name in {"main", "seed"}:
            continue
        module = importlib.import_module(f"backend.{module_info.name}")
        router = getattr(module, "router", None)
        if router is not None:
            application.include_router(router)
            loaded.append(module_info.name)
    return loaded


@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    with SessionLocal() as db:
        auth.seed_default_users(db)
    yield


app = FastAPI(title="Smart Parking Management System", version="1.0.0", lifespan=lifespan)
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
