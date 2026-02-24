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
        ContestAgentLog,
        DiscordConfig,
        DiscordUser,
        GuildRank,
        InviteCode,
        Player,
        PlayerCharacter,
        Role,
        Specialization,
        User,
        Vote,
        WowCharacter,
        WowClass,
    )

    assert GuildRank.__tablename__ == "guild_ranks"
    assert User.__tablename__ == "users"
    assert Player.__tablename__ == "players"
    assert WowCharacter.__tablename__ == "wow_characters"
    assert DiscordUser.__tablename__ == "discord_users"
    assert PlayerCharacter.__tablename__ == "player_characters"
    assert Role.__tablename__ == "roles"
    assert WowClass.__tablename__ == "classes"
    assert Specialization.__tablename__ == "specializations"
    assert DiscordConfig.__tablename__ == "discord_config"
    assert InviteCode.__tablename__ == "invite_codes"
    assert Campaign.__tablename__ == "campaigns"
    assert CampaignEntry.__tablename__ == "campaign_entries"
    assert Vote.__tablename__ == "votes"
    assert CampaignResult.__tablename__ == "campaign_results"
    assert ContestAgentLog.__tablename__ == "contest_agent_log"


def test_model_schemas():
    """Verify models are assigned to correct database schemas."""
    from sv_common.db.models import (
        Campaign,
        GuildRank,
        Player,
        Role,
        WowCharacter,
        WowClass,
    )

    assert GuildRank.__table_args__["schema"] == "common"
    assert Campaign.__table_args__["schema"] == "patt"
    assert Player.__table_args__["schema"] == "guild_identity"
    assert WowCharacter.__table_args__[1]["schema"] == "guild_identity"
    assert Role.__table_args__["schema"] == "guild_identity"
    assert WowClass.__table_args__["schema"] == "guild_identity"


def test_player_has_required_fields():
    """Verify Player model has all Phase 2.7 fields."""
    from sv_common.db.models import Player

    columns = {c.name for c in Player.__table__.columns}
    required = {
        "id", "display_name", "discord_user_id", "website_user_id",
        "guild_rank_id", "guild_rank_source",
        "main_character_id", "main_spec_id",
        "offspec_character_id", "offspec_spec_id",
        "is_active", "notes", "created_at", "updated_at",
    }
    assert required.issubset(columns), f"Missing columns: {required - columns}"


def test_wow_character_has_fk_columns():
    """Verify WowCharacter has class_id/active_spec_id/guild_rank_id (no more text fields)."""
    from sv_common.db.models import WowCharacter

    columns = {c.name for c in WowCharacter.__table__.columns}
    assert "class_id" in columns
    assert "active_spec_id" in columns
    assert "guild_rank_id" in columns
    # Text fields should be gone
    assert "character_class" not in columns
    assert "active_spec" not in columns
    assert "guild_rank" not in columns
    assert "guild_rank_name" not in columns
    assert "person_id" not in columns
    assert "is_main" not in columns
    assert "role_category" not in columns


def test_invite_code_uses_player_id():
    """Verify InviteCode uses player_id not member_id."""
    from sv_common.db.models import InviteCode

    columns = {c.name for c in InviteCode.__table__.columns}
    assert "player_id" in columns
    assert "created_by_player_id" in columns
    assert "member_id" not in columns
    assert "created_by" not in columns


def test_vote_uses_player_id():
    """Verify Vote uses player_id not member_id."""
    from sv_common.db.models import Vote

    columns = {c.name for c in Vote.__table__.columns}
    assert "player_id" in columns
    assert "member_id" not in columns
