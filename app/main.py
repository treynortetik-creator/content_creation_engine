"""Main FastAPI application for ContentMultiplier."""
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.config import get_settings
from app.database import init_db
from app.api import upload, jobs, library, admin, auth, personas, brand_voice, memory, swipe, feedback, analytics, export, integrations, batch, remix, calendar, autopilot, content_edit, image_prompts
from app.api import admin_views
from app.services.autopilot.scheduler import scheduler

settings = get_settings()

# Configure rate limiter
def get_user_id_or_ip(request: Request) -> str:
    """Get user ID from auth header or fall back to IP address."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        # Use token hash as identifier (not the full token for security)
        token = auth_header[7:]
        return f"user:{hash(token) % 1000000}"
    return get_remote_address(request)

limiter = Limiter(key_func=get_user_id_or_ip)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    print("Starting ContentMultiplier...")

    # Ensure directories exist
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    settings.prompts_dir.mkdir(parents=True, exist_ok=True)
    settings.clients_dir.mkdir(parents=True, exist_ok=True)
    settings.database_dir.mkdir(parents=True, exist_ok=True)

    # Initialize database
    await init_db()
    print("Database initialized")

    # Initialize prompt templates from files
    from app.services.prompt_manager import init_prompts_from_files
    await init_prompts_from_files()
    print("Prompt templates loaded")

    # Start autopilot scheduler
    asyncio.create_task(scheduler.start())
    print("Autopilot scheduler started")

    yield

    # Shutdown
    scheduler.stop()
    print("Shutting down ContentMultiplier...")


app = FastAPI(
    title="ContentMultiplier",
    description="AI-powered content repurposing platform",
    version="0.1.0",
    lifespan=lifespan,
)

# Add rate limiter to app state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# Global exception handler for unhandled errors
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle all uncaught exceptions with a proper JSON response."""
    import traceback

    # Log the error (in production, use proper logging)
    print(f"Unhandled error: {type(exc).__name__}: {exc}")
    print(traceback.format_exc())

    # Return a user-friendly error response
    return JSONResponse(
        status_code=500,
        content={
            "error": "An internal error occurred",
            "type": type(exc).__name__,
            "detail": str(exc) if settings.debug else "Please try again later",
        }
    )


# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(upload.router, prefix="/api", tags=["upload"])
app.include_router(jobs.router, prefix="/api", tags=["jobs"])
app.include_router(library.router, prefix="/api", tags=["library"])
app.include_router(personas.router, prefix="/api", tags=["personas"])
app.include_router(brand_voice.router, prefix="/api", tags=["brand-voice"])
app.include_router(memory.router, prefix="/api", tags=["memory"])
app.include_router(swipe.router, prefix="/api", tags=["swipe-file"])
app.include_router(feedback.router, prefix="/api", tags=["feedback"])
app.include_router(analytics.router, prefix="/api", tags=["analytics"])
app.include_router(export.router, prefix="/api", tags=["export"])
app.include_router(integrations.router, prefix="/api", tags=["integrations"])
app.include_router(batch.router, prefix="/api", tags=["batch"])
app.include_router(remix.router, prefix="/api", tags=["remix"])
app.include_router(calendar.router, prefix="/api", tags=["calendar"])
app.include_router(autopilot.router, prefix="/api", tags=["autopilot"])
app.include_router(content_edit.router, prefix="/api", tags=["content-edit"])
app.include_router(image_prompts.router, prefix="/api", tags=["image-prompts"])
# Register admin views FIRST so HTML pages take priority over API responses
app.include_router(admin_views.router, prefix="/admin", tags=["admin-views"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin-api"])

# Frontend path
frontend_path = Path(__file__).parent.parent / "frontend"

# Mount static files for frontend
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=frontend_path / "static"), name="static")


# Serve frontend HTML files
@app.get("/login.html")
async def serve_login():
    """Serve login page."""
    return FileResponse(frontend_path / "login.html")


@app.get("/register.html")
async def serve_register():
    """Serve register page."""
    return FileResponse(frontend_path / "register.html")


@app.get("/upload.html")
async def serve_upload():
    """Serve upload page."""
    return FileResponse(frontend_path / "upload.html")


@app.get("/status.html")
async def serve_status():
    """Serve status page."""
    return FileResponse(frontend_path / "status.html")


@app.get("/results.html")
async def serve_results():
    """Serve results page."""
    return FileResponse(frontend_path / "results.html")


@app.get("/library.html")
async def serve_library():
    """Serve library page."""
    return FileResponse(frontend_path / "library.html")


@app.get("/settings.html")
async def serve_settings():
    """Serve settings page."""
    return FileResponse(frontend_path / "settings.html")


@app.get("/brand-voice.html")
async def serve_brand_voice():
    """Serve brand voice configuration page."""
    return FileResponse(frontend_path / "brand-voice.html")


@app.get("/remix.html")
async def serve_remix():
    """Serve remix page."""
    return FileResponse(frontend_path / "remix.html")


@app.get("/calendar.html")
async def serve_calendar():
    """Serve calendar page."""
    return FileResponse(frontend_path / "calendar.html")


@app.get("/autopilot.html")
async def serve_autopilot():
    """Serve autopilot page."""
    return FileResponse(frontend_path / "autopilot.html")


@app.get("/swipe.html")
async def serve_swipe():
    """Serve swipe file page."""
    return FileResponse(frontend_path / "swipe.html")


@app.get("/analytics.html")
async def serve_analytics():
    """Serve analytics page."""
    return FileResponse(frontend_path / "analytics.html")


@app.get("/")
async def root():
    """Serve homepage."""
    return FileResponse(frontend_path / "index.html")


@app.get("/api")
async def api_info():
    """API info endpoint."""
    return {
        "name": "ContentMultiplier API",
        "version": "0.1.0",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
