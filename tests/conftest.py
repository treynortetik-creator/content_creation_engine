"""Pytest configuration and fixtures."""
import os
import pytest
import asyncio
from pathlib import Path
from httpx import AsyncClient, ASGITransport

# Set test environment
os.environ["GEMINI_API_KEY"] = "test-key"
os.environ["ANTHROPIC_API_KEY"] = "test-key"
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_USERNAME", "test-admin")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

from app.main import app
from app.database import init_db, DATABASE_PATH


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
async def test_db():
    """Initialize test database."""
    # Remove existing test database
    if DATABASE_PATH.exists():
        DATABASE_PATH.unlink()

    # Initialize fresh database
    await init_db()

    yield DATABASE_PATH

    # Cleanup
    if DATABASE_PATH.exists():
        DATABASE_PATH.unlink()


@pytest.fixture
async def client(test_db):
    """Create async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def sample_transcript():
    """Sample transcript for testing."""
    return """
    Speaker 1: Welcome everyone to today's webinar on improving staff retention in senior care facilities.

    We've seen some incredible results over the past year. Our data shows a 40% reduction in turnover
    when facilities implement our three-step framework.

    The first step is recognition. Staff need to feel valued. One facility, Sunrise Senior Living,
    implemented a peer recognition program and saw turnover drop from 85% to 52% in just six months.

    The second step is training. When staff feel competent, they stay longer. We recommend at least
    20 hours of specialized training in the first month.

    And the third step is flexibility. Offering flexible scheduling has become critical, especially
    post-pandemic. 73% of CNAs cite schedule flexibility as a top factor in job satisfaction.

    The bottom line? Investing in your staff isn't just the right thing to do - it's the profitable
    thing to do. Every 1% reduction in turnover saves approximately $50,000 per year for a 100-bed facility.

    Thank you for joining us today.
    """


@pytest.fixture
def sample_atoms():
    """Sample atoms for testing."""
    return [
        {
            "id": "atom-001",
            "atom_type": "data",
            "content": "40% reduction in turnover",
            "source_location": "intro",
            "persona_relevance": {"ceo_longterm_care": 5},
        },
        {
            "id": "atom-002",
            "atom_type": "story",
            "content": "Sunrise Senior Living implemented a peer recognition program and saw turnover drop from 85% to 52%",
            "source_location": "step 1",
            "persona_relevance": {"ceo_longterm_care": 4},
        },
        {
            "id": "atom-003",
            "atom_type": "insight",
            "content": "Three-step framework: recognition, training, flexibility",
            "source_location": "main body",
            "persona_relevance": {"don_memory_care": 5},
        },
    ]
