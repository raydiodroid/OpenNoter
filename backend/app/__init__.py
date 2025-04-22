from fastapi import FastAPI
from .routers import note, provider


def create_app() -> FastAPI:
    app = FastAPI(title="BiliNote")
    app.include_router(note.router, prefix="/api")
    app.include_router(provider.router, prefix="/api")
    return app
