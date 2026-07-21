# backend/app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title="CodeLens AI",
        description="Intelligent Codebase Understanding & Analysis System",
        version=settings.VERSION,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["System"])
    async def health():
        return {
            "status": "ok",
            "version": settings.VERSION,
            "service": "CodeLens AI",
        }

    @app.get("/", tags=["System"])
    async def root():
        return {"message": "CodeLens AI is running. Visit /api/docs"}

    return app


app = create_app()
