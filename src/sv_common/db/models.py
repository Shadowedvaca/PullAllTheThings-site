"""SQLAlchemy ORM models for the PATT platform.

common schema: guild_ranks, users, discord_config, invite_codes
patt schema: campaigns, campaign_entries, votes, campaign_results,
             contest_agent_log, mito_quotes, mito_titles,
             player_availability, raid_seasons, raid_events, raid_attendance
guild_identity schema: roles, classes, specializations, players,
                       wow_characters, discord_users, player_characters,
                       audit_issues, sync_log, onboarding_sessions
"""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# common schema
# ---------------------------------------------------------------------------


class GuildRank(Base):
    __tablename__ = "guild_ranks"
    __table_args__ = {"schema": "common"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    scheduling_weight: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    discord_role_id: Mapped[Optional[str]] = mapped_column(String(20))
    description: Mapped[Optional[str]] = mapped_column(Text)
    wow_rank_index: Mapped[Optional[int]] = mapped_column(Integer, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "common"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    player: Mapped[Optional["Player"]] = relationship(back_populates="website_user")


class DiscordConfig(Base):
    __tablename__ = "discord_config"
    __table_args__ = {"schema": "common"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_discord_id: Mapped[str] = mapped_column(String(20), nullable=False)
    role_sync_interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    default_announcement_channel_id: Mapped[Optional[str]] = mapped_column(String(20))
    last_role_sync_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    bot_dm_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class InviteCode(Base):
    __tablename__ = "invite_codes"
    __table_args__ = {"schema": "common"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    player_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.players.id")
    )
    created_by_player_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.players.id")
    )
    used_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    expires_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    generated_by: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="manual"
    )
    onboarding_session_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.onboarding_sessions.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    player: Mapped[Optional["Player"]] = relationship(
        back_populates="invite_codes", foreign_keys=[player_id]
    )
    created_by_player: Mapped[Optional["Player"]] = relationship(
        back_populates="created_invite_codes", foreign_keys=[created_by_player_id]
    )


# ---------------------------------------------------------------------------
# patt schema
# ---------------------------------------------------------------------------


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = {"schema": "patt"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(20), default="ranked_choice")
    picks_per_voter: Mapped[int] = mapped_column(Integer, default=3)
    min_rank_to_vote: Mapped[int] = mapped_column(Integer, nullable=False)
    min_rank_to_view: Mapped[Optional[int]] = mapped_column(Integer)
    start_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    duration_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    early_close_if_all_voted: Mapped[bool] = mapped_column(Boolean, default=True)
    discord_channel_id: Mapped[Optional[str]] = mapped_column(String(20))
    agent_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    agent_chattiness: Mapped[str] = mapped_column(String(10), default="normal")
    created_by_player_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.players.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    created_by_player: Mapped[Optional["Player"]] = relationship(
        back_populates="campaigns_created"
    )
    entries: Mapped[list["CampaignEntry"]] = relationship(back_populates="campaign")
    votes: Mapped[list["Vote"]] = relationship(back_populates="campaign")
    results: Mapped[list["CampaignResult"]] = relationship(back_populates="campaign")
    agent_log: Mapped[list["ContestAgentLog"]] = relationship(back_populates="campaign")


class CampaignEntry(Base):
    __tablename__ = "campaign_entries"
    __table_args__ = {"schema": "patt"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patt.campaigns.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    image_url: Mapped[Optional[str]] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    player_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.players.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    campaign: Mapped[Campaign] = relationship(back_populates="entries")
    votes: Mapped[list["Vote"]] = relationship(back_populates="entry")
    result: Mapped[Optional["CampaignResult"]] = relationship(back_populates="entry")


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (
        UniqueConstraint("campaign_id", "player_id", "rank"),
        {"schema": "patt"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patt.campaigns.id", ondelete="CASCADE"), nullable=False
    )
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("guild_identity.players.id"), nullable=False
    )
    entry_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patt.campaign_entries.id"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    voted_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    campaign: Mapped[Campaign] = relationship(back_populates="votes")
    player: Mapped["Player"] = relationship(back_populates="votes")
    entry: Mapped[CampaignEntry] = relationship(back_populates="votes")


class CampaignResult(Base):
    __tablename__ = "campaign_results"
    __table_args__ = {"schema": "patt"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patt.campaigns.id", ondelete="CASCADE"), nullable=False
    )
    entry_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patt.campaign_entries.id"), nullable=False
    )
    first_place_count: Mapped[int] = mapped_column(Integer, default=0)
    second_place_count: Mapped[int] = mapped_column(Integer, default=0)
    third_place_count: Mapped[int] = mapped_column(Integer, default=0)
    weighted_score: Mapped[int] = mapped_column(Integer, default=0)
    final_rank: Mapped[Optional[int]] = mapped_column(Integer)
    calculated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    campaign: Mapped[Campaign] = relationship(back_populates="results")
    entry: Mapped[CampaignEntry] = relationship(back_populates="result")


class ContestAgentLog(Base):
    __tablename__ = "contest_agent_log"
    __table_args__ = {"schema": "patt"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patt.campaigns.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    discord_message_id: Mapped[Optional[str]] = mapped_column(String(20))
    posted_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    campaign: Mapped[Campaign] = relationship(back_populates="agent_log")


class MitoQuote(Base):
    __tablename__ = "mito_quotes"
    __table_args__ = {"schema": "patt"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )


class MitoTitle(Base):
    __tablename__ = "mito_titles"
    __table_args__ = {"schema": "patt"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )


class PlayerAvailability(Base):
    """Player raid availability: time windows per day of week (0=Mon … 6=Sun)."""

    __tablename__ = "player_availability"
    __table_args__ = (
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="ck_player_availability_day_range"),
        CheckConstraint(
            "available_hours > 0 AND available_hours <= 16",
            name="ck_player_availability_hours",
        ),
        UniqueConstraint("player_id", "day_of_week", name="uq_player_availability_player_day"),
        {"schema": "patt"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("guild_identity.players.id"), nullable=False
    )
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    earliest_start: Mapped[time] = mapped_column(Time, nullable=False)
    available_hours: Mapped[Decimal] = mapped_column(Numeric(3, 1), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    player: Mapped["Player"] = relationship(back_populates="availability")


class RaidSeason(Base):
    """A WoW content season (e.g. Season 2 – Liberation of Undermine)."""

    __tablename__ = "raid_seasons"
    __table_args__ = {"schema": "patt"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    events: Mapped[list["RaidEvent"]] = relationship(back_populates="season")


class RaidEvent(Base):
    """A single scheduled raid night."""

    __tablename__ = "raid_events"
    __table_args__ = {"schema": "patt"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("patt.raid_seasons.id")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time_utc: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    end_time_utc: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    raid_helper_event_id: Mapped[Optional[str]] = mapped_column(String(30))
    discord_channel_id: Mapped[Optional[str]] = mapped_column(String(25))
    log_url: Mapped[Optional[str]] = mapped_column(String(500))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_by_player_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.players.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    season: Mapped[Optional[RaidSeason]] = relationship(back_populates="events")
    attendance: Mapped[list["RaidAttendance"]] = relationship(back_populates="event")


class RaidAttendance(Base):
    """Who signed up and who actually attended a raid event."""

    __tablename__ = "raid_attendance"
    __table_args__ = (
        UniqueConstraint("event_id", "player_id", name="uq_attendance_event_player"),
        {"schema": "patt"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patt.raid_events.id"), nullable=False
    )
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("guild_identity.players.id"), nullable=False
    )
    signed_up: Mapped[bool] = mapped_column(Boolean, server_default="false")
    attended: Mapped[bool] = mapped_column(Boolean, server_default="false")
    character_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.wow_characters.id")
    )
    noted_absence: Mapped[bool] = mapped_column(Boolean, server_default="false")
    source: Mapped[str] = mapped_column(String(20), server_default="manual")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    event: Mapped[RaidEvent] = relationship(back_populates="attendance")
    player: Mapped["Player"] = relationship()
    character: Mapped[Optional["WowCharacter"]] = relationship()


# ---------------------------------------------------------------------------
# guild_identity schema — reference tables
# ---------------------------------------------------------------------------


class Role(Base):
    """Combat role reference: Tank, Healer, Melee DPS, Ranged DPS."""

    __tablename__ = "roles"
    __table_args__ = {"schema": "guild_identity"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)

    specializations: Mapped[list["Specialization"]] = relationship(
        back_populates="default_role"
    )


class WowClass(Base):
    """WoW class reference: Death Knight, Druid, etc."""

    __tablename__ = "classes"
    __table_args__ = {"schema": "guild_identity"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    color_hex: Mapped[Optional[str]] = mapped_column(String(7))

    specializations: Mapped[list["Specialization"]] = relationship(
        back_populates="wow_class"
    )
    wow_characters: Mapped[list["WowCharacter"]] = relationship(
        back_populates="wow_class"
    )


class Specialization(Base):
    """Class+spec combination: (Druid, Balance), (Death Knight, Frost), etc."""

    __tablename__ = "specializations"
    __table_args__ = (
        UniqueConstraint("class_id", "name"),
        {"schema": "guild_identity"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("guild_identity.classes.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    default_role_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("guild_identity.roles.id"), nullable=False
    )
    wowhead_slug: Mapped[Optional[str]] = mapped_column(String(50))

    wow_class: Mapped[WowClass] = relationship(back_populates="specializations")
    default_role: Mapped[Role] = relationship(back_populates="specializations")


# ---------------------------------------------------------------------------
# guild_identity schema — core entities
# ---------------------------------------------------------------------------


class DiscordUser(Base):
    """Discord server member tracked by the bot."""

    __tablename__ = "discord_users"
    __table_args__ = {"schema": "guild_identity"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    discord_id: Mapped[str] = mapped_column(String(25), nullable=False, unique=True)
    username: Mapped[str] = mapped_column(String(50), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(50))
    highest_guild_role: Mapped[Optional[str]] = mapped_column(String(30))
    all_guild_roles: Mapped[Optional[list]] = mapped_column(ARRAY(Text))
    joined_server_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    last_sync: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    is_present: Mapped[bool] = mapped_column(Boolean, server_default="true")
    removed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    first_seen: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    player: Mapped[Optional["Player"]] = relationship(back_populates="discord_user")


class WowCharacter(Base):
    """WoW character from guild roster (Blizzard API + PATTSync addon)."""

    __tablename__ = "wow_characters"
    __table_args__ = (
        UniqueConstraint("character_name", "realm_slug"),
        {"schema": "guild_identity"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_name: Mapped[str] = mapped_column(String(50), nullable=False)
    realm_slug: Mapped[str] = mapped_column(String(50), nullable=False)
    realm_name: Mapped[Optional[str]] = mapped_column(String(100))
    class_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.classes.id")
    )
    active_spec_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.specializations.id")
    )
    level: Mapped[Optional[int]] = mapped_column(Integer)
    item_level: Mapped[Optional[int]] = mapped_column(Integer)
    guild_rank_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("common.guild_ranks.id")
    )
    last_login_timestamp: Mapped[Optional[int]] = mapped_column(BigInteger)
    guild_note: Mapped[Optional[str]] = mapped_column(Text)
    officer_note: Mapped[Optional[str]] = mapped_column(Text)
    addon_last_seen: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    blizzard_last_sync: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    addon_last_sync: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    first_seen: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    removed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))

    wow_class: Mapped[Optional[WowClass]] = relationship(back_populates="wow_characters")
    active_spec: Mapped[Optional[Specialization]] = relationship()
    guild_rank: Mapped[Optional[GuildRank]] = relationship()
    player_character: Mapped[Optional["PlayerCharacter"]] = relationship(
        back_populates="character"
    )


class Player(Base):
    """The central identity entity — links Discord, website account, and WoW characters."""

    __tablename__ = "players"
    __table_args__ = {"schema": "guild_identity"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    discord_user_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("guild_identity.discord_users.id"),
        unique=True,
    )
    website_user_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("common.users.id"),
        unique=True,
    )
    guild_rank_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("common.guild_ranks.id")
    )
    guild_rank_source: Mapped[Optional[str]] = mapped_column(String(20))
    main_character_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.wow_characters.id")
    )
    main_spec_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.specializations.id")
    )
    offspec_character_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.wow_characters.id")
    )
    offspec_spec_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.specializations.id")
    )
    timezone: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="America/Chicago"
    )
    auto_invite_events: Mapped[bool] = mapped_column(Boolean, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    discord_user: Mapped[Optional[DiscordUser]] = relationship(back_populates="player")
    website_user: Mapped[Optional[User]] = relationship(back_populates="player")
    guild_rank: Mapped[Optional[GuildRank]] = relationship()
    main_character: Mapped[Optional[WowCharacter]] = relationship(
        foreign_keys=[main_character_id]
    )
    main_spec: Mapped[Optional[Specialization]] = relationship(
        foreign_keys=[main_spec_id]
    )
    offspec_character: Mapped[Optional[WowCharacter]] = relationship(
        foreign_keys=[offspec_character_id]
    )
    offspec_spec: Mapped[Optional[Specialization]] = relationship(
        foreign_keys=[offspec_spec_id]
    )
    characters: Mapped[list["PlayerCharacter"]] = relationship(back_populates="player")
    availability: Mapped[list["PlayerAvailability"]] = relationship(back_populates="player")
    invite_codes: Mapped[list[InviteCode]] = relationship(
        back_populates="player", foreign_keys="InviteCode.player_id"
    )
    created_invite_codes: Mapped[list[InviteCode]] = relationship(
        back_populates="created_by_player", foreign_keys="InviteCode.created_by_player_id"
    )
    campaigns_created: Mapped[list[Campaign]] = relationship(
        back_populates="created_by_player"
    )
    votes: Mapped[list[Vote]] = relationship(back_populates="player")


class PlayerCharacter(Base):
    """Bridge: which WoW characters belong to which player."""

    __tablename__ = "player_characters"
    __table_args__ = (
        UniqueConstraint("player_id", "character_id"),
        {"schema": "guild_identity"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("guild_identity.players.id", ondelete="CASCADE"),
        nullable=False,
    )
    character_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("guild_identity.wow_characters.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    player: Mapped[Player] = relationship(back_populates="characters")
    character: Mapped[WowCharacter] = relationship(back_populates="player_character")


# ---------------------------------------------------------------------------
# guild_identity schema — system tables
# ---------------------------------------------------------------------------


class AuditIssue(Base):
    __tablename__ = "audit_issues"
    __table_args__ = (
        UniqueConstraint("issue_hash", "resolved_at", name="uq_audit_issue_hash_resolved"),
        {"schema": "guild_identity"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False, server_default="info")
    wow_character_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.wow_characters.id", ondelete="CASCADE")
    )
    discord_member_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.discord_users.id", ondelete="CASCADE")
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[Optional[dict]] = mapped_column(JSONB)
    issue_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    resolved_by: Mapped[Optional[str]] = mapped_column(String(50))
    notified_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))

    wow_character: Mapped[Optional[WowCharacter]] = relationship()
    discord_member: Mapped[Optional[DiscordUser]] = relationship()


class GuildSyncLog(Base):
    __tablename__ = "sync_log"
    __table_args__ = {"schema": "guild_identity"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    characters_found: Mapped[Optional[int]] = mapped_column(Integer)
    characters_updated: Mapped[Optional[int]] = mapped_column(Integer)
    characters_new: Mapped[Optional[int]] = mapped_column(Integer)
    characters_removed: Mapped[Optional[int]] = mapped_column(Integer)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float)
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))


class OnboardingSession(Base):
    __tablename__ = "onboarding_sessions"
    __table_args__ = (
        UniqueConstraint("discord_id", name="uq_onboarding_discord_id"),
        {"schema": "guild_identity"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    discord_member_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("guild_identity.discord_users.id", ondelete="CASCADE"),
        nullable=False,
    )
    discord_id: Mapped[str] = mapped_column(String(25), nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False, server_default="awaiting_dm")
    reported_main_name: Mapped[Optional[str]] = mapped_column(String(50))
    reported_main_realm: Mapped[Optional[str]] = mapped_column(String(100))
    reported_alt_names: Mapped[Optional[list]] = mapped_column(ARRAY(Text))
    is_in_guild: Mapped[Optional[bool]] = mapped_column(Boolean)
    verification_attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    last_verification_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    verified_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    verified_player_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.players.id")
    )
    website_invite_sent: Mapped[bool] = mapped_column(Boolean, server_default="false")
    website_invite_code: Mapped[Optional[str]] = mapped_column(String(50))
    roster_entries_created: Mapped[bool] = mapped_column(Boolean, server_default="false")
    discord_role_assigned: Mapped[bool] = mapped_column(Boolean, server_default="false")
    dm_sent_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    dm_completed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    deadline_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    escalated_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
