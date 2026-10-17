from pydantic import BaseModel


class IceServer(BaseModel):
    urls: str


class IceServersResponse(BaseModel):
    ice_servers: list[IceServer]
