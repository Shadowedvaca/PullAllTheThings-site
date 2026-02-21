"""Smoke tests — verify the app can be imported and configured."""


def test_app_imports():
    """Verify the app module can be imported without errors."""
    from patt.app import create_app

    app = create_app()
    assert app is not None


def test_settings_load():
    """Verify settings can be constructed with defaults."""
    from patt.config import Settings

    settings = Settings(
        database_url="postgresql+asyncpg://test:test@localhost/test",
        jwt_secret_key="test-secret",
    )
    assert settings.app_port == 8100
    assert settings.jwt_algorithm == "HS256"
    assert settings.jwt_expire_minutes == 1440


def test_models_importable():
    """Verify all SQLAlchemy models import cleanly."""
    from sv_common.db.models import (
        Campaign,
        CampaignEntry,
        CampaignResult,
        Character,
        ContestAgentLog,
        DiscordConfig,
        GuildMember,
        GuildRank,
        InviteCode,
        User,
        Vote,
    )

    assert GuildRank.__tablename__ == "guild_ranks"
    assert User.__tablename__ == "users"
    assert GuildMember.__tablename__ == "guild_members"
    assert Character.__tablename__ == "characters"
    assert DiscordConfig.__tablename__ == "discord_config"
    assert InviteCode.__tablename__ == "invite_codes"
    assert Campaign.__tablename__ == "campaigns"
    assert CampaignEntry.__tablename__ == "campaign_entries"
    assert Vote.__tablename__ == "votes"
    assert CampaignResult.__tablename__ == "campaign_results"
    assert ContestAgentLog.__tablename__ == "contest_agent_log"


def test_model_schemas():
    """Verify models are assigned to correct database schemas."""
    from sv_common.db.models import Campaign, GuildRank

    assert GuildRank.__table_args__["schema"] == "common"
    assert Campaign.__table_args__["schema"] == "patt"
