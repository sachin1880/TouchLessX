import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.services.captures import cleanup_expired_captures
from app.services.videos import cleanup_expired_videos

settings = get_settings()
configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Smart Home Camera API starting up: environment=%s", settings.environment)
    db = SessionLocal()
    try:
        cleanup_expired_captures(db)
        cleanup_expired_videos(db)
    finally:
        db.close()
    yield


app = FastAPI(
    title="Smart Home Camera API",
    version="0.1.0",
    description="Backend API for registering, monitoring, and controlling home camera devices.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")
