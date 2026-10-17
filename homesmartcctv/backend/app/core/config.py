from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"

    database_url: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24

    cors_origins: str = "http://localhost:19006"

    # Comma-separated STUN/TURN URLs handed to clients for WebRTC NAT traversal.
    # Google's public STUN server is free with no signup; add a TURN server here later
    # (e.g. a self-hosted coturn) without needing an app update.
    stun_servers: str = "stun:stun.l.google.com:19302"

    # A device is considered OFFLINE once this many seconds pass without a heartbeat.
    heartbeat_offline_threshold_seconds: int = 60
    # LOW_BATTERY event fires once when battery drops below this percent, not on every heartbeat.
    low_battery_threshold_percent: int = 15

    # Directory captured event snapshots are written to, and how long they're kept before
    # being deleted for good (both the file and its event record) - this is a personal
    # camera app, not a video-storage service, so retention is deliberately short.
    media_root: str = "media"
    capture_retention_days: int = 3

    # Continuous recording: the camera uploads one ~60s video segment at a time while
    # streaming. Kept much shorter than a real NVR's retention - this is a personal
    # single-camera app, and 2 days of 1-minute clips is already tens of GB.
    video_retention_days: int = 2

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def stun_server_list(self) -> list[str]:
        return [server.strip() for server in self.stun_servers.split(",") if server.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
