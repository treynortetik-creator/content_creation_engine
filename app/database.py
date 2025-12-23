"""Database setup and connection management."""
import aiosqlite
from pathlib import Path
from typing import AsyncGenerator
from contextlib import asynccontextmanager

from app.config import get_settings

settings = get_settings()

# Ensure database directory exists
settings.database_dir.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = settings.database_dir / "contentmultiplier.db"


async def init_db():
    """Initialize the database with schema."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Enable foreign keys
        await db.execute("PRAGMA foreign_keys = ON")

        # Create tables
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                subscription_tier TEXT DEFAULT 'free',
                credits_remaining INTEGER DEFAULT 0,
                total_cost_incurred REAL DEFAULT 0.0,
                byok_enabled BOOLEAN DEFAULT 0,
                api_keys JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS rate_limits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS error_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                job_id TEXT,
                error_type TEXT NOT NULL,
                error_message TEXT,
                stack_trace TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                original_filename TEXT,
                file_type TEXT,
                file_size INTEGER,
                target_persona TEXT,
                asset_types JSON,
                asset_quantities JSON,
                processing_mode TEXT DEFAULT 'autopilot',
                campaign_name TEXT,
                magic_words TEXT,
                current_step TEXT,
                progress INTEGER DEFAULT 0,
                transcript TEXT,
                cleaned_transcript TEXT,
                preview_output TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                cost_incurred REAL DEFAULT 0.0,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS atoms (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                atom_type TEXT NOT NULL,
                content TEXT NOT NULL,
                source_location TEXT,
                source_file TEXT,
                tags JSON,
                persona_relevance JSON,
                quote_attribution TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                times_used INTEGER DEFAULT 0,
                last_used TIMESTAMP,
                FOREIGN KEY (job_id) REFERENCES jobs(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS outputs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                content_type TEXT NOT NULL,
                variation_number INTEGER,
                step1_draft TEXT,
                step2_edited TEXT,
                step3_final TEXT,
                atoms_used JSON,
                citations JSON,
                warnings JSON,
                quality_scores JSON,
                hook_variations JSON,
                user_edits INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            );

            CREATE TABLE IF NOT EXISTS content_library (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                entry_type TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT,
                source_timestamp TEXT,
                speaker TEXT,
                date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                tags JSON,
                persona_relevance JSON,
                times_used INTEGER DEFAULT 0,
                last_used TIMESTAMP,
                user_notes TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS prompt_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                template_name TEXT UNIQUE NOT NULL,
                model TEXT NOT NULL,
                max_tokens INTEGER DEFAULT 4000,
                prompt_content TEXT NOT NULL,
                variables JSON,
                version INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- Create indexes for common queries
            CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_atoms_user_id ON atoms(user_id);
            CREATE INDEX IF NOT EXISTS idx_atoms_job_id ON atoms(job_id);
            CREATE INDEX IF NOT EXISTS idx_atoms_type ON atoms(atom_type);
            CREATE INDEX IF NOT EXISTS idx_content_library_user_id ON content_library(user_id);
            CREATE INDEX IF NOT EXISTS idx_content_library_type ON content_library(entry_type);
            CREATE INDEX IF NOT EXISTS idx_rate_limits_user ON rate_limits(user_id, action_type, timestamp);
            CREATE INDEX IF NOT EXISTS idx_error_logs_user ON error_logs(user_id, created_at);

            -- Brand voice configuration table
            CREATE TABLE IF NOT EXISTS brand_contexts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                company_name TEXT,
                industry TEXT,
                brand_voice_json JSON NOT NULL DEFAULT '{}',
                mission_statement TEXT,
                key_differentiators JSON,
                competitor_names JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_brand_contexts_user_id ON brand_contexts(user_id);

            -- Memory rules for persistent user preferences
            CREATE TABLE IF NOT EXISTS memory_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                rule_type TEXT NOT NULL,
                rule_content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_memory_rules_user_id ON memory_rules(user_id);
        """)

        await db.commit()

        # Migration: Add preview_output column if missing
        try:
            await db.execute("SELECT preview_output FROM jobs LIMIT 1")
        except aiosqlite.OperationalError:
            await db.execute("ALTER TABLE jobs ADD COLUMN preview_output TEXT")
            await db.commit()

        # Migration: Add hook_variations column to outputs if missing
        try:
            await db.execute("SELECT hook_variations FROM outputs LIMIT 1")
        except aiosqlite.OperationalError:
            await db.execute("ALTER TABLE outputs ADD COLUMN hook_variations JSON")
            await db.commit()

        # Create default user if not exists
        cursor = await db.execute("SELECT id FROM users WHERE email = ?", ("default@contentmultiplier.com",))
        if await cursor.fetchone() is None:
            await db.execute(
                "INSERT INTO users (email, subscription_tier) VALUES (?, ?)",
                ("default@contentmultiplier.com", "pro")
            )
            await db.commit()


@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Get database connection as async context manager."""
    db = await aiosqlite.connect(DATABASE_PATH)
    db.row_factory = aiosqlite.Row
    try:
        await db.execute("PRAGMA foreign_keys = ON")
        yield db
    finally:
        await db.close()


async def get_db_connection() -> aiosqlite.Connection:
    """Get a database connection (caller must close)."""
    db = await aiosqlite.connect(DATABASE_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON")
    return db
