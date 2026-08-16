# backend/app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.body_limit import BodyLimitMiddleware
from app.api.routes import router as api_router
from app.api.semantic_routes import router as semantic_router
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title="CodeLens AI",
        description="Intelligent Codebase Understanding & Analysis System",
        version=settings.VERSION,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    # Outermost: a body is refused before anything else allocates for it.
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.MAX_REQUEST_BODY_BYTES)

    # Origins come from configuration, not from this line. Hardcoding
    # localhost meant the API worked on exactly one machine and failed with a
    # browser-side CORS error — the kind that looks like a frontend bug — the
    # moment it was served from anywhere else.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)
    app.include_router(semantic_router)

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
