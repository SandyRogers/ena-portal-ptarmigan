from pydantic import HttpUrl
from pydantic_settings import BaseSettings
from platformdirs import user_cache_path


class _EnaConfig(BaseSettings):
    api_url_prefix: HttpUrl = "https://www.ebi.ac.uk/ena/portal/api/"
    timeout: int = 30  # Default timeout in seconds


class _CacheConfig(BaseSettings):
    cache_dir: str = str(user_cache_path("ptarmigan"))


class _AppConfig(BaseSettings):
    ena: _EnaConfig = _EnaConfig()
    cache: _CacheConfig = _CacheConfig()

    class Config:
        env_file = '.env'  # Load configuration from a .env file if present


app_config = _AppConfig()
