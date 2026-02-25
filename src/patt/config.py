"""Application settings loaded from environment variables / .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440
    discord_bot_token: str = ""
    discord_guild_id: str = ""
    google_apps_script_url: str = ""
    app_env: str = "development"
    app_port: int = 8100
    app_host: str = "0.0.0.0"

    # Blizzard API (Phase 2.5)
    blizzard_client_id: str = ""
    blizzard_client_secret: str = ""

    # Guild sync config (Phase 2.5)
    patt_guild_realm_slug: str = "senjin"
    patt_guild_name_slug: str = "pull-all-the-things"
    patt_audit_channel_id: str = ""

    # Companion app API key (Phase 2.5)
    patt_api_key: str = ""


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
