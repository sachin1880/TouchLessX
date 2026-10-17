from fastapi import APIRouter

from app.api.routes import auth, commands, devices, health, users, webrtc

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(devices.router)
api_router.include_router(webrtc.router)
api_router.include_router(commands.router)
