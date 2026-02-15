from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://tram:tram_secret@localhost:5432/tram_monitor"
    redis_url: str = "redis://localhost:6379/0"
    ettu_base_url: str = "https://map.ettu.ru"
    poll_interval_seconds: int = 10
    route_refresh_hours: int = 1
    position_retention_days: int = 90
    debug_scalar_prediction: bool = False

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
