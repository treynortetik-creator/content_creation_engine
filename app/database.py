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

            -- Swipe file for saving favorite outputs
            CREATE TABLE IF NOT EXISTS swipe_file (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                output_id INTEGER,
                content_type TEXT NOT NULL,
                content TEXT NOT NULL,
                source_title TEXT,
                notes TEXT,
                patterns_extracted JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (output_id) REFERENCES outputs(id)
            );
            CREATE INDEX IF NOT EXISTS idx_swipe_file_user_id ON swipe_file(user_id);
            CREATE INDEX IF NOT EXISTS idx_swipe_file_content_type ON swipe_file(content_type);

            -- Feedback on outputs (thumbs up/down)
            CREATE TABLE IF NOT EXISTS output_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                output_id INTEGER NOT NULL,
                feedback_type TEXT NOT NULL CHECK (feedback_type IN ('up', 'down')),
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (output_id) REFERENCES outputs(id),
                UNIQUE(user_id, output_id)
            );
            CREATE INDEX IF NOT EXISTS idx_output_feedback_user_id ON output_feedback(user_id);
            CREATE INDEX IF NOT EXISTS idx_output_feedback_output_id ON output_feedback(output_id);

            -- Zapier webhooks for integrations
            CREATE TABLE IF NOT EXISTS zapier_webhooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                webhook_url TEXT NOT NULL,
                webhook_name TEXT,
                trigger_event TEXT NOT NULL,
                enabled BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_triggered_at TIMESTAMP,
                last_error TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_zapier_webhooks_user_id ON zapier_webhooks(user_id);

            -- Content calendar scheduling
            CREATE TABLE IF NOT EXISTS content_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                output_id INTEGER NOT NULL,
                scheduled_date TEXT NOT NULL,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (output_id) REFERENCES outputs(id),
                UNIQUE(output_id)
            );
            CREATE INDEX IF NOT EXISTS idx_content_schedule_user_id ON content_schedule(user_id);
            CREATE INDEX IF NOT EXISTS idx_content_schedule_date ON content_schedule(scheduled_date);

            -- Batches for batch processing
            CREATE TABLE IF NOT EXISTS batches (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                total_jobs INTEGER NOT NULL,
                completed_jobs INTEGER DEFAULT 0,
                failed_jobs INTEGER DEFAULT 0,
                status TEXT DEFAULT 'processing',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_batches_user_id ON batches(user_id);

            -- Custom personas created by users
            CREATE TABLE IF NOT EXISTS custom_personas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                persona_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                company_size TEXT,
                pain_points JSON,
                priorities JSON,
                language_level TEXT DEFAULT 'Professional',
                content_preferences JSON,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                UNIQUE(user_id, persona_id)
            );
            CREATE INDEX IF NOT EXISTS idx_custom_personas_user_id ON custom_personas(user_id);

            -- AI model configuration per task
            CREATE TABLE IF NOT EXISTS ai_model_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_name TEXT NOT NULL UNIQUE,
                model_id TEXT NOT NULL,
                description TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- Deep analysis results for individual swipe entries
            CREATE TABLE IF NOT EXISTS swipe_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                swipe_id INTEGER NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                analysis_data JSON NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (swipe_id) REFERENCES swipe_file(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_swipe_analysis_swipe_id ON swipe_analysis(swipe_id);
            CREATE INDEX IF NOT EXISTS idx_swipe_analysis_user_id ON swipe_analysis(user_id);

            -- Collection-level analysis for aggregated swipe patterns
            CREATE TABLE IF NOT EXISTS swipe_collection_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                content_type TEXT,
                analysis_data JSON NOT NULL,
                swipe_count INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_swipe_collection_user_id ON swipe_collection_analysis(user_id);
            CREATE INDEX IF NOT EXISTS idx_swipe_collection_content_type ON swipe_collection_analysis(content_type);
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

        # Migration: Add email sequence columns to outputs if missing
        try:
            await db.execute("SELECT subject_line FROM outputs LIMIT 1")
        except aiosqlite.OperationalError:
            await db.execute("ALTER TABLE outputs ADD COLUMN subject_line TEXT")
            await db.execute("ALTER TABLE outputs ADD COLUMN preview_text TEXT")
            await db.execute("ALTER TABLE outputs ADD COLUMN send_day INTEGER")
            await db.execute("ALTER TABLE outputs ADD COLUMN email_type TEXT")
            await db.execute("ALTER TABLE outputs ADD COLUMN cta_text TEXT")
            await db.commit()

        # Migration: Add batch_id column to jobs if missing
        try:
            await db.execute("SELECT batch_id FROM jobs LIMIT 1")
        except aiosqlite.OperationalError:
            await db.execute("ALTER TABLE jobs ADD COLUMN batch_id TEXT")
            await db.commit()

        # Migration: Seed default AI model configurations
        cursor = await db.execute("SELECT COUNT(*) FROM ai_model_config")
        count = (await cursor.fetchone())[0]
        if count == 0:
            default_models = [
                ('transcription', 'google/gemini-flash-1.5', 'Audio/video transcription'),
                ('atomization', 'google/gemini-flash-1.5', 'Content atom extraction'),
                ('drafting', 'anthropic/claude-sonnet-4', 'Content drafting - quality matters'),
                ('editing', 'google/gemini-flash-1.5', 'Content editing and refinement'),
                ('fact_checking', 'google/gemini-flash-1.5', 'Fact verification'),
                ('scoring', 'google/gemini-flash-1.5', 'Quality scoring'),
                ('brand_voice_analysis', 'anthropic/claude-sonnet-4', 'Brand voice extraction from samples'),
                ('hook_generation', 'anthropic/claude-sonnet-4', 'Hook variation generation'),
                ('swipe_analysis', 'google/gemini-flash-1.5', 'Swipe file pattern extraction'),
            ]
            await db.executemany(
                "INSERT INTO ai_model_config (task_name, model_id, description) VALUES (?, ?, ?)",
                default_models
            )
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
