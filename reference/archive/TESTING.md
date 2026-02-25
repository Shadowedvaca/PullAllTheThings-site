# PATT Guild Platform — Testing Strategy

> This document defines the testing conventions for the entire platform.
> Every phase must include tests as a deliverable. Tests must pass before a phase is complete.

---

## Philosophy

This platform is designed to grow. Today it's a voting app; tomorrow it handles raid signups,
book club picks, podcast scheduling, and more. Common services are shared across multiple sites.
If pulling a thread in one place breaks another, we need to know **immediately** — before it
ever hits the server.

Tests are not an afterthought. They are part of the skeleton.

---

## Test Framework

| Tool | Purpose |
|------|---------|
| **pytest** | Test runner and assertion framework |
| **pytest-asyncio** | Async test support (FastAPI and discord.py are async) |
| **httpx** | Async HTTP client for API integration tests |
| **factory_boy** | Test data factories for consistent fixtures |
| **pytest-cov** | Coverage reporting |

---

## Test Categories

### Unit Tests (`tests/unit/`)

Test individual functions and methods in isolation. No database, no network, no external services.

**What to test:**
- Vote scoring calculations (ranked choice point tallying)
- Rank permission checks (can this rank vote? can they view?)
- JWT token creation and validation
- Password hashing and verification
- Invite code generation and validation
- Campaign status transitions (draft → live → closed)
- Time-based logic (is campaign active? has it expired?)
- Data validation (Pydantic models, input sanitization)

**Conventions:**
- Mock all external dependencies (database, Discord, HTTP)
- Each test tests ONE thing
- Test names describe the behavior: `test_veteran_can_vote_when_min_rank_is_veteran`
- Use parametrize for testing multiple inputs: `@pytest.mark.parametrize("rank_level,expected", [...])`

### Integration Tests (`tests/integration/`)

Test complete flows through the API with a real (test) database. No external services (Discord is mocked).

**What to test:**
- Full auth flow: generate invite → register → login → access protected route
- Full vote flow: create campaign → add entries → cast vote → check results
- Campaign lifecycle: draft → live → votes → close → results
- Admin operations: create campaign, manage roster, send invites
- Permission boundaries: initiate tries to vote on veteran-only campaign
- Role sync: mock Discord API response → verify rank updates in DB
- Edge cases: vote twice, vote after deadline, vote on closed campaign

**Conventions:**
- Use a real PostgreSQL test database (created/destroyed per test session)
- Each test gets a clean database state via transactions that roll back
- Use the FastAPI test client (httpx.AsyncClient) for API calls
- Test data created via factory_boy factories

### Regression Tests (`tests/regression/`)

Tests for specific bugs that were found and fixed. These ensure bugs don't come back.

**Conventions:**
- File: `test_known_bugs.py`
- Each test has a comment with: date found, description, and what was fixed
- Example:
  ```python
  def test_mainalt_case_sensitivity():
      """
      Bug: 2025-01-19 — "mainalt" vs "mainAlt" case mismatch broke roster loading.
      Fix: Standardized to snake_case "main_alt" in all models and API responses.
      """
      ...
  ```

---

## Fixtures (`tests/conftest.py`)

Shared fixtures available to all tests:

```python
@pytest.fixture
async def db_session():
    """Provides a database session that rolls back after each test."""

@pytest.fixture
async def client(db_session):
    """Provides an authenticated httpx.AsyncClient for API testing."""

@pytest.fixture
async def admin_member(db_session):
    """Creates a Guild Leader rank member for admin tests."""

@pytest.fixture
async def veteran_member(db_session):
    """Creates a Veteran rank member for standard voting tests."""

@pytest.fixture
async def initiate_member(db_session):
    """Creates an Initiate rank member for permission denial tests."""

@pytest.fixture
async def sample_campaign(db_session, admin_member):
    """Creates a live ranked-choice campaign with entries."""

@pytest.fixture
async def mock_discord_bot():
    """Mocked Discord bot that captures sent DMs and channel messages."""
```

---

## Test Data Factories

Using factory_boy for consistent test data:

```python
class GuildRankFactory(factory.Factory):
    class Meta:
        model = GuildRank
    name = factory.Sequence(lambda n: f"Rank {n}")
    level = factory.Sequence(lambda n: n)

class GuildMemberFactory(factory.Factory):
    class Meta:
        model = GuildMember
    discord_username = factory.Faker("user_name")
    display_name = factory.Faker("first_name")
    discord_id = factory.Sequence(lambda n: str(100000000000000000 + n))

class CampaignFactory(factory.Factory):
    class Meta:
        model = Campaign
    title = factory.Faker("sentence", nb_words=4)
    type = "ranked_choice"
    picks_per_voter = 3
    min_rank_to_vote = 3  # Veteran
    status = "live"
    duration_hours = 168  # 1 week
```

---

## Database Test Setup

Integration tests use a **separate test database** that is created once per test session
and cleaned between tests using savepoints (transaction rollback).

```python
# In conftest.py
TEST_DATABASE_URL = "postgresql+asyncpg://patt_test:testpass@localhost:5432/patt_test_db"

@pytest.fixture(scope="session")
async def test_engine():
    """Create test database tables once per session."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

@pytest.fixture
async def db_session(test_engine):
    """Per-test session with rollback."""
    async with AsyncSession(test_engine) as session:
        async with session.begin():
            yield session
            await session.rollback()
```

---

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run only unit tests
pytest tests/unit/ -v

# Run only integration tests
pytest tests/integration/ -v

# Run with coverage
pytest tests/ --cov=src --cov-report=term-missing

# Run a specific test
pytest tests/unit/test_vote_scoring.py::test_ranked_choice_scoring -v

# Run tests matching a keyword
pytest tests/ -k "campaign" -v
```

---

## Coverage Expectations

| Category | Target |
|----------|--------|
| Vote scoring logic | 100% — this is the core algorithm |
| Auth (JWT, passwords, invite codes) | 95%+ |
| API routes | 90%+ (happy path + key error cases) |
| Campaign lifecycle | 90%+ |
| Rank/permission checks | 95%+ |
| Discord integration | 80%+ (mocked, but cover all message types) |
| Admin operations | 80%+ |
| Templates/HTML rendering | Not unit tested — verified manually |

---

## Test Naming Conventions

Test names should read like sentences describing behavior:

```
✓ test_ranked_choice_first_place_gets_three_points
✓ test_veteran_can_vote_on_veteran_minimum_campaign
✓ test_initiate_cannot_vote_on_veteran_minimum_campaign
✓ test_voter_sees_results_after_casting_vote
✓ test_non_voter_sees_only_results_no_vote_form
✓ test_campaign_closes_early_when_all_eligible_members_voted
✓ test_duplicate_vote_same_rank_rejected
✓ test_expired_invite_code_rejected
✓ test_role_sync_promotes_member_when_discord_role_added
```

---

## Phase-Specific Test Requirements

Each phase plan specifies exactly which tests must be written. At minimum:

- **Phase 0:** pytest runs, conftest loads, a trivial test passes
- **Phase 1:** Schema validation, model creation, seed data tests
- **Phase 2:** Auth flow tests, invite code tests, JWT tests, Discord mock tests
- **Phase 3:** Vote scoring unit tests, campaign lifecycle tests, permission tests
- **Phase 4:** Page rendering tests (status codes, correct template, auth gates)
- **Phase 5:** Migration verification tests (data integrity after import)
- **Phase 6:** Contest agent trigger tests, message generation tests
- **Phase 7:** End-to-end regression suite

---

## What NOT to Test

- Jinja2 template rendering (test the data, not the HTML — verify manually)
- Third-party library internals (trust that SQLAlchemy and FastAPI work)
- CSS/JS (visual testing is manual)
- Discord API itself (mock it; test our code that calls it)
