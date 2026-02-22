"""SQLAlchemy ORM models for the PATT platform.

common schema: guild_ranks, users, guild_members, characters, discord_config, invite_codes
patt schema: campaigns, campaign_entries, votes, campaign_results, contest_agent_log
guild_identity schema: persons, wow_characters, discord_members, identity_links, audit_issues, sync_log
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
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
    discord_role_id: Mapped[Optional[str]] = mapped_column(String(20))
    description: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    members: Mapped[list["GuildMember"]] = relationship(back_populates="rank")


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

    member: Mapped[Optional["GuildMember"]] = relationship(back_populates="user")


class GuildMember(Base):
    __tablename__ = "guild_members"
    __table_args__ = {"schema": "common"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("common.users.id"), unique=True
    )
    discord_id: Mapped[Optional[str]] = mapped_column(String(20), unique=True)
    discord_username: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(100))
    rank_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("common.guild_ranks.id"), nullable=False
    )
    rank_source: Mapped[str] = mapped_column(String(20), default="manual")
    registered_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[Optional[User]] = relationship(back_populates="member")
    rank: Mapped[GuildRank] = relationship(back_populates="members")
    characters: Mapped[list["Character"]] = relationship(back_populates="member")
    invite_codes: Mapped[list["InviteCode"]] = relationship(
        back_populates="member", foreign_keys="InviteCode.member_id"
    )
    created_invite_codes: Mapped[list["InviteCode"]] = relationship(
        back_populates="created_by_member", foreign_keys="InviteCode.created_by"
    )
    campaigns_created: Mapped[list["Campaign"]] = relationship(
        back_populates="created_by_member"
    )
    votes: Mapped[list["Vote"]] = relationship(back_populates="member")


class Character(Base):
    __tablename__ = "characters"
    __table_args__ = (
        UniqueConstraint("name", "realm"),
        {"schema": "common"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("common.guild_members.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    realm: Mapped[str] = mapped_column(String(50), nullable=False)
    class_: Mapped[str] = mapped_column("class", String(30), nullable=False)
    spec: Mapped[Optional[str]] = mapped_column(String(30))
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    main_alt: Mapped[str] = mapped_column(String(10), default="main")
    armory_url: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    member: Mapped[GuildMember] = relationship(back_populates="characters")


class DiscordConfig(Base):
    __tablename__ = "discord_config"
    __table_args__ = {"schema": "common"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_discord_id: Mapped[str] = mapped_column(String(20), nullable=False)
    role_sync_interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    default_announcement_channel_id: Mapped[Optional[str]] = mapped_column(String(20))
    last_role_sync_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
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
    member_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("common.guild_members.id")
    )
    created_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("common.guild_members.id")
    )
    used_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    expires_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    member: Mapped[Optional[GuildMember]] = relationship(
        back_populates="invite_codes", foreign_keys=[member_id]
    )
    created_by_member: Mapped[Optional[GuildMember]] = relationship(
        back_populates="created_invite_codes", foreign_keys=[created_by]
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
    created_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("common.guild_members.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    created_by_member: Mapped[Optional[GuildMember]] = relationship(
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
    associated_member_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("common.guild_members.id")
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
        UniqueConstraint("campaign_id", "member_id", "rank"),
        {"schema": "patt"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patt.campaigns.id", ondelete="CASCADE"), nullable=False
    )
    member_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("common.guild_members.id"), nullable=False
    )
    entry_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patt.campaign_entries.id"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    voted_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    campaign: Mapped[Campaign] = relationship(back_populates="votes")
    member: Mapped[GuildMember] = relationship(back_populates="votes")
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


# ---------------------------------------------------------------------------
# guild_identity schema
# ---------------------------------------------------------------------------


class GuildIdentityPerson(Base):
    __tablename__ = "persons"
    __table_args__ = {"schema": "guild_identity"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    wow_characters: Mapped[list["WowCharacter"]] = relationship(back_populates="person")
    discord_members: Mapped[list["GuildIdentityDiscordMember"]] = relationship(back_populates="person")
    identity_links: Mapped[list["IdentityLink"]] = relationship(back_populates="person")


class WowCharacter(Base):
    __tablename__ = "wow_characters"
    __table_args__ = (
        UniqueConstraint("character_name", "realm_slug"),
        {"schema": "guild_identity"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.persons.id", ondelete="SET NULL")
    )
    character_name: Mapped[str] = mapped_column(String(50), nullable=False)
    realm_slug: Mapped[str] = mapped_column(String(50), nullable=False)
    realm_name: Mapped[Optional[str]] = mapped_column(String(100))
    character_class: Mapped[Optional[str]] = mapped_column(String(30))
    active_spec: Mapped[Optional[str]] = mapped_column(String(50))
    level: Mapped[Optional[int]] = mapped_column(Integer)
    item_level: Mapped[Optional[int]] = mapped_column(Integer)
    guild_rank: Mapped[Optional[int]] = mapped_column(Integer)
    guild_rank_name: Mapped[Optional[str]] = mapped_column(String(50))
    last_login_timestamp: Mapped[Optional[int]] = mapped_column(BigInteger)
    guild_note: Mapped[Optional[str]] = mapped_column(Text)
    officer_note: Mapped[Optional[str]] = mapped_column(Text)
    addon_last_seen: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    is_main: Mapped[bool] = mapped_column(Boolean, server_default="false")
    role_category: Mapped[Optional[str]] = mapped_column(String(10))
    blizzard_last_sync: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    addon_last_sync: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    first_seen: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    removed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))

    person: Mapped[Optional[GuildIdentityPerson]] = relationship(back_populates="wow_characters")
    identity_link: Mapped[Optional["IdentityLink"]] = relationship(
        back_populates="wow_character", foreign_keys="IdentityLink.wow_character_id"
    )


class GuildIdentityDiscordMember(Base):
    __tablename__ = "discord_members"
    __table_args__ = {"schema": "guild_identity"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.persons.id", ondelete="SET NULL")
    )
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

    person: Mapped[Optional[GuildIdentityPerson]] = relationship(back_populates="discord_members")
    identity_link: Mapped[Optional["IdentityLink"]] = relationship(
        back_populates="discord_member", foreign_keys="IdentityLink.discord_member_id"
    )


class IdentityLink(Base):
    __tablename__ = "identity_links"
    __table_args__ = (
        CheckConstraint(
            "wow_character_id IS NOT NULL OR discord_member_id IS NOT NULL",
            name="identity_links_target_not_null",
        ),
        {"schema": "guild_identity"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("guild_identity.persons.id", ondelete="CASCADE"), nullable=False
    )
    wow_character_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.wow_characters.id", ondelete="CASCADE"), unique=True
    )
    discord_member_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.discord_members.id", ondelete="CASCADE"), unique=True
    )
    link_source: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[str] = mapped_column(String(10), nullable=False, server_default="high")
    is_confirmed: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    confirmed_by: Mapped[Optional[str]] = mapped_column(String(50))

    person: Mapped[GuildIdentityPerson] = relationship(back_populates="identity_links")
    wow_character: Mapped[Optional[WowCharacter]] = relationship(
        back_populates="identity_link", foreign_keys=[wow_character_id]
    )
    discord_member: Mapped[Optional[GuildIdentityDiscordMember]] = relationship(
        back_populates="identity_link", foreign_keys=[discord_member_id]
    )


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
        Integer, ForeignKey("guild_identity.discord_members.id", ondelete="CASCADE")
    )
    person_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.persons.id", ondelete="SET NULL")
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[Optional[dict]] = mapped_column(JSONB)
    first_detected: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    last_detected: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    notified_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    resolved_by: Mapped[Optional[str]] = mapped_column(String(50))
    resolution_note: Mapped[Optional[str]] = mapped_column(Text)
    issue_hash: Mapped[str] = mapped_column(String(64), nullable=False)


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
