"""SPMS FastAPI application entry point.

Run locally with:  uvicorn backend.main:app --reload
"""
from fastapi import FastAPI

app = FastAPI(title="Smart Parking Management System", version="0.1.0")


@app.get("/health")
def health():
    """Simple check that the API is running."""
    return {"status": "ok", "service": "SPMS"}
