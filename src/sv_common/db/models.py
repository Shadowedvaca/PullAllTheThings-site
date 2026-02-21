"""SQLAlchemy ORM models for the PATT platform.

common schema: guild_ranks, users, guild_members, characters, discord_config, invite_codes
patt schema: campaigns, campaign_entries, votes, campaign_results, contest_agent_log
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import TIMESTAMP


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
