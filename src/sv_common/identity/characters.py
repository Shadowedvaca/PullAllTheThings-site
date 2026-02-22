"""Character management service functions."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import Character

VALID_ROLES = {"tank", "healer", "melee_dps", "ranged_dps"}
VALID_MAIN_ALT = {"main", "alt"}


def build_armory_url(name: str, realm: str) -> str:
    """Build Blizzard armory URL. Handle special characters in names and realms."""
    clean_realm = realm.lower().replace("'", "").replace(" ", "-")
    return (
        f"https://worldofwarcraft.blizzard.com/en-us/character/us"
        f"/{clean_realm}/{name.lower()}"
    )


async def get_characters_for_member(
    db: AsyncSession, member_id: int
) -> list[Character]:
    result = await db.execute(
        select(Character)
        .where(Character.member_id == member_id)
        .order_by(Character.main_alt, Character.name)
    )
    return list(result.scalars().all())


async def get_main_character(db: AsyncSession, member_id: int) -> Character | None:
    result = await db.execute(
        select(Character)
        .where(Character.member_id == member_id)
        .where(Character.main_alt == "main")
    )
    return result.scalars().first()


async def create_character(
    db: AsyncSession,
    member_id: int,
    name: str,
    realm: str,
    wow_class: str,
    spec: str | None = None,
    role: str | None = None,
    main_alt: str = "main",
) -> Character:
    if role is not None and role not in VALID_ROLES:
        raise ValueError(
            f"Invalid role '{role}'. Must be one of: {', '.join(sorted(VALID_ROLES))}"
        )
    if main_alt not in VALID_MAIN_ALT:
        raise ValueError(f"Invalid main_alt '{main_alt}'. Must be 'main' or 'alt'")

    armory_url = build_armory_url(name, realm)
    char = Character(
        member_id=member_id,
        name=name,
        realm=realm,
        class_=wow_class,
        spec=spec,
        role=role or "ranged_dps",
        main_alt=main_alt,
        armory_url=armory_url,
    )
    db.add(char)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ValueError(
            f"Character '{name}' on '{realm}' already exists"
        ) from exc
    await db.refresh(char)
    return char


async def update_character(db: AsyncSession, char_id: int, **kwargs) -> Character:
    result = await db.execute(select(Character).where(Character.id == char_id))
    char = result.scalar_one_or_none()
    if char is None:
        raise ValueError(f"Character {char_id} not found")
    if "role" in kwargs and kwargs["role"] not in VALID_ROLES:
        raise ValueError(f"Invalid role '{kwargs['role']}'")
    if "main_alt" in kwargs and kwargs["main_alt"] not in VALID_MAIN_ALT:
        raise ValueError(f"Invalid main_alt '{kwargs['main_alt']}'")
    allowed = {"name", "realm", "class_", "spec", "role", "main_alt", "armory_url"}
    for key, value in kwargs.items():
        if key in allowed:
            setattr(char, key, value)
    await db.flush()
    await db.refresh(char)
    return char


async def delete_character(db: AsyncSession, char_id: int) -> bool:
    result = await db.execute(select(Character).where(Character.id == char_id))
    char = result.scalar_one_or_none()
    if char is None:
        return False
    await db.delete(char)
    await db.flush()
    return True
