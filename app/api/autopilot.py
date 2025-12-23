"""Autopilot API - Manage content source monitors."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, HttpUrl

from app.api.auth import get_current_user_id
from app.services.autopilot.monitor import (
    check_monitor,
    create_monitor,
    update_monitor,
    delete_monitor,
    get_user_monitors,
    get_monitor_runs,
    get_monitor_by_id,
)
from app.services.autopilot.fetchers import validate_source_url

router = APIRouter()


class MonitorCreate(BaseModel):
    monitor_name: str
    source_type: str  # youtube_channel, rss, podcast
    source_url: str
    persona_id: str
    magic_words: Optional[str] = None
    check_interval_hours: int = 24


class MonitorUpdate(BaseModel):
    enabled: Optional[bool] = None
    check_interval_hours: Optional[int] = None
    persona_id: Optional[str] = None
    magic_words: Optional[str] = None


@router.post("/autopilot/monitors")
async def create_monitor_endpoint(
    request: MonitorCreate,
    user_id: int = Depends(get_current_user_id),
):
    """
    Create a new autopilot monitor.

    Monitors watch content sources (YouTube channels, RSS feeds, podcasts)
    and automatically create jobs when new content is found.
    """
    if request.source_type not in ["youtube_channel", "rss", "podcast"]:
        raise HTTPException(400, "Invalid source type. Must be: youtube_channel, rss, or podcast")

    if request.check_interval_hours < 1 or request.check_interval_hours > 168:
        raise HTTPException(400, "Check interval must be between 1 and 168 hours")

    # Validate the source URL
    validation = await validate_source_url(request.source_type, request.source_url)
    if not validation["valid"]:
        raise HTTPException(400, f"Invalid source URL: {validation['error']}")

    monitor_id = await create_monitor(
        user_id=user_id,
        monitor_name=request.monitor_name,
        source_type=request.source_type,
        source_url=request.source_url,
        persona_id=request.persona_id,
        magic_words=request.magic_words,
        check_interval_hours=request.check_interval_hours,
    )

    return {
        "success": True,
        "monitor_id": monitor_id,
        "validation": validation,
    }


@router.get("/autopilot/monitors")
async def list_monitors(user_id: int = Depends(get_current_user_id)):
    """List all user's autopilot monitors."""
    monitors = await get_user_monitors(user_id)
    return {"monitors": monitors, "count": len(monitors)}


@router.get("/autopilot/monitors/{monitor_id}")
async def get_monitor(
    monitor_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Get a specific monitor's details."""
    monitor = await get_monitor_by_id(monitor_id)

    if not monitor:
        raise HTTPException(404, "Monitor not found")

    if monitor["user_id"] != user_id:
        raise HTTPException(403, "Not authorized to view this monitor")

    return {"monitor": monitor}


@router.put("/autopilot/monitors/{monitor_id}")
async def update_monitor_endpoint(
    monitor_id: int,
    request: MonitorUpdate,
    user_id: int = Depends(get_current_user_id),
):
    """Update a monitor's settings."""
    # Verify ownership
    monitor = await get_monitor_by_id(monitor_id)
    if not monitor:
        raise HTTPException(404, "Monitor not found")
    if monitor["user_id"] != user_id:
        raise HTTPException(403, "Not authorized to update this monitor")

    if request.check_interval_hours is not None:
        if request.check_interval_hours < 1 or request.check_interval_hours > 168:
            raise HTTPException(400, "Check interval must be between 1 and 168 hours")

    await update_monitor(
        monitor_id=monitor_id,
        user_id=user_id,
        enabled=request.enabled,
        check_interval_hours=request.check_interval_hours,
        persona_id=request.persona_id,
        magic_words=request.magic_words,
    )

    return {"success": True}


@router.delete("/autopilot/monitors/{monitor_id}")
async def delete_monitor_endpoint(
    monitor_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Delete a monitor."""
    # Verify ownership
    monitor = await get_monitor_by_id(monitor_id)
    if not monitor:
        raise HTTPException(404, "Monitor not found")
    if monitor["user_id"] != user_id:
        raise HTTPException(403, "Not authorized to delete this monitor")

    await delete_monitor(monitor_id, user_id)

    return {"success": True}


@router.post("/autopilot/monitors/{monitor_id}/check-now")
async def check_now(
    monitor_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """
    Manually trigger a monitor check.

    This bypasses the normal check interval and immediately checks for new content.
    """
    # Verify ownership
    monitor = await get_monitor_by_id(monitor_id)
    if not monitor:
        raise HTTPException(404, "Monitor not found")
    if monitor["user_id"] != user_id:
        raise HTTPException(403, "Not authorized to check this monitor")

    result = await check_monitor(monitor_id)

    return result


@router.get("/autopilot/monitors/{monitor_id}/runs")
async def get_runs(
    monitor_id: int,
    limit: int = 20,
    user_id: int = Depends(get_current_user_id),
):
    """Get run history for a monitor."""
    # Verify ownership
    monitor = await get_monitor_by_id(monitor_id)
    if not monitor:
        raise HTTPException(404, "Monitor not found")
    if monitor["user_id"] != user_id:
        raise HTTPException(403, "Not authorized to view this monitor")

    runs = await get_monitor_runs(monitor_id, limit)

    return {"runs": runs, "count": len(runs)}


@router.post("/autopilot/validate-url")
async def validate_url(
    source_type: str,
    source_url: str,
    user_id: int = Depends(get_current_user_id),
):
    """
    Validate a source URL before creating a monitor.

    Returns information about the source if valid.
    """
    if source_type not in ["youtube_channel", "rss", "podcast"]:
        raise HTTPException(400, "Invalid source type")

    result = await validate_source_url(source_type, source_url)

    return result
