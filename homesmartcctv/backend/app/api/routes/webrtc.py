from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.models.user import User
from app.schemas.webrtc import IceServer, IceServersResponse

router = APIRouter(prefix="/webrtc", tags=["webrtc"])
settings = get_settings()


@router.get("/ice-servers", response_model=IceServersResponse)
def get_ice_servers(current_user: User = Depends(get_current_user)) -> IceServersResponse:
    return IceServersResponse(ice_servers=[IceServer(urls=url) for url in settings.stun_server_list])
