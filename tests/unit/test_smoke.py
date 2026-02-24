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
        PlayerAvailability,
        PlayerCharacter,
        RaidAttendance,
        RaidEvent,
        RaidSeason,
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
    assert PlayerAvailability.__tablename__ == "player_availability"
    assert RaidSeason.__tablename__ == "raid_seasons"
    assert RaidEvent.__tablename__ == "raid_events"
    assert RaidAttendance.__tablename__ == "raid_attendance"


def test_model_schemas():
    """Verify models are assigned to correct database schemas."""
    from sv_common.db.models import (
        Campaign,
        GuildRank,
        Player,
        PlayerAvailability,
        RaidAttendance,
        RaidEvent,
        RaidSeason,
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
    assert PlayerAvailability.__table_args__[3]["schema"] == "patt"
    assert RaidSeason.__table_args__["schema"] == "patt"
    assert RaidEvent.__table_args__["schema"] == "patt"
    assert RaidAttendance.__table_args__[1]["schema"] == "patt"


def test_player_has_required_fields():
    """Verify Player model has all Phase 2.7 + 2.8 fields."""
    from sv_common.db.models import Player

    columns = {c.name for c in Player.__table__.columns}
    required = {
        "id", "display_name", "discord_user_id", "website_user_id",
        "guild_rank_id", "guild_rank_source",
        "main_character_id", "main_spec_id",
        "offspec_character_id", "offspec_spec_id",
        "timezone", "auto_invite_events",
        "is_active", "notes", "created_at", "updated_at",
    }
    assert required.issubset(columns), f"Missing columns: {required - columns}"


def test_guild_rank_has_scheduling_weight():
    """Verify GuildRank model has scheduling_weight column."""
    from sv_common.db.models import GuildRank

    columns = {c.name for c in GuildRank.__table__.columns}
    assert "scheduling_weight" in columns


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


def test_player_availability_schema():
    """Verify PlayerAvailability has correct fields and schema."""
    from sv_common.db.models import PlayerAvailability

    columns = {c.name for c in PlayerAvailability.__table__.columns}
    assert "player_id" in columns
    assert "day_of_week" in columns
    assert "earliest_start" in columns
    assert "available_hours" in columns
    assert "updated_at" in columns


def test_member_availability_removed():
    """Verify MemberAvailability no longer exists in models."""
    import sv_common.db.models as m
    assert not hasattr(m, "MemberAvailability"), "MemberAvailability should be removed"


def test_guild_sync_modules_importable():
    """Verify all guild_sync modules import without referencing old schema."""
    from sv_common.guild_sync import discord_sync
    from sv_common.guild_sync import db_sync
    from sv_common.guild_sync import identity_engine
    from sv_common.guild_sync import integrity_checker
    from sv_common.guild_sync import reporter
    from sv_common.guild_sync import scheduler

    # Verify key functions exist in each module
    assert callable(discord_sync.sync_discord_members)
    assert callable(db_sync.sync_blizzard_roster)
    assert callable(db_sync.sync_addon_data)
    assert callable(identity_engine.run_matching)
    assert callable(identity_engine.normalize_name)
    assert callable(identity_engine.extract_discord_hints_from_note)
    assert callable(identity_engine.fuzzy_match_score)
    assert callable(integrity_checker.run_integrity_check)
    assert callable(reporter.send_new_issues_report)
    assert hasattr(scheduler, "GuildSyncScheduler")


def test_guild_sync_no_old_schema_references():
    """Verify guild_sync modules do not reference dropped tables/columns."""
    import inspect
    from sv_common.guild_sync import identity_engine, integrity_checker

    engine_src = inspect.getsource(identity_engine)
    checker_src = inspect.getsource(integrity_checker)

    # These table references must not appear in the updated modules
    forbidden_tables = [
        "guild_identity.persons",
        "guild_identity.discord_members",
        "guild_identity.identity_links",
    ]
    for term in forbidden_tables:
        assert term not in engine_src, f"identity_engine.py still references table '{term}'"
        assert term not in checker_src, f"integrity_checker.py still references table '{term}'"

    # These old column accesses must not appear (as actual column references, not aliases)
    forbidden_columns = [
        "wc.guild_rank_name",
        "wc.character_class",
        "wc.is_main",
        "wc.role_category",
        "wc.person_id",
        "dm.person_id",
        "discord_members",
    ]
    for term in forbidden_columns:
        assert term not in engine_src, f"identity_engine.py still uses column '{term}'"
        assert term not in checker_src, f"integrity_checker.py still uses column '{term}'"


def test_onboarding_modules_importable():
    """Verify all onboarding modules import cleanly."""
    from sv_common.guild_sync.onboarding import conversation, provisioner
    from sv_common.guild_sync.onboarding import deadline_checker, commands

    assert hasattr(conversation, "OnboardingConversation")
    assert hasattr(provisioner, "AutoProvisioner")
    assert hasattr(deadline_checker, "OnboardingDeadlineChecker")
    assert callable(commands.register_onboarding_commands)


def test_onboarding_no_old_schema_references():
    """Verify onboarding modules do not reference dropped tables or old column names."""
    import inspect
    from sv_common.guild_sync.onboarding import conversation, provisioner
    from sv_common.guild_sync.onboarding import deadline_checker, commands

    sources = {
        "conversation": inspect.getsource(conversation),
        "provisioner": inspect.getsource(provisioner),
        "deadline_checker": inspect.getsource(deadline_checker),
        "commands": inspect.getsource(commands),
    }

    forbidden = [
        "guild_identity.persons",
        "guild_identity.discord_members",
        "guild_identity.identity_links",
        "common.guild_members",
        "common.characters",
        "verified_person_id",
        "provision_person",
        "characters_created",
        "guild_members",
    ]
    for term in forbidden:
        for mod_name, src in sources.items():
            assert term not in src, (
                f"onboarding/{mod_name}.py still references '{term}'"
            )


def test_discord_config_has_bot_dm_enabled():
    """Verify DiscordConfig has bot_dm_enabled column (Phase 2.6)."""
    from sv_common.db.models import DiscordConfig

    columns = {c.name for c in DiscordConfig.__table__.columns}
    assert "bot_dm_enabled" in columns


def test_bot_module_has_event_handlers():
    """Verify bot.py has on_member_join, on_member_remove, on_member_update."""
    import inspect
    from sv_common.discord import bot as bot_module

    src = inspect.getsource(bot_module)
    assert "on_member_join" in src
    assert "on_member_remove" in src
    assert "on_member_update" in src
    assert "set_db_pool" in src
