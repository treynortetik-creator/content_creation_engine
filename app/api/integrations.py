"""Integrations API - Zapier webhooks and other integrations."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import httpx

from app.database import get_db
from app.api.auth import get_current_user_id

router = APIRouter()


class WebhookCreate(BaseModel):
    webhook_url: str
    webhook_name: str
    trigger_event: str  # 'job_complete' or 'output_generated'


@router.post("/integrations/zapier/webhooks")
async def add_zapier_webhook(
    webhook: WebhookCreate,
    user_id: int = Depends(get_current_user_id),
):
    """
    Add a new Zapier webhook.

    trigger_event options:
    - 'job_complete': Fires when processing completes (recommended)
    - 'output_generated': Fires for each output generated
    """
    # Validate webhook URL
    if not webhook.webhook_url.startswith('https://hooks.zapier.com/'):
        # Also allow make.com and other webhook services
        allowed_prefixes = [
            'https://hooks.zapier.com/',
            'https://hook.us1.make.com/',
            'https://hook.eu1.make.com/',
            'https://n8n.',
            'https://',  # Allow any HTTPS webhook for flexibility
        ]
        if not any(webhook.webhook_url.startswith(p) for p in allowed_prefixes):
            raise HTTPException(
                status_code=400,
                detail="Webhook URL must use HTTPS"
            )

    # Validate trigger event
    valid_events = ['job_complete', 'output_generated']
    if webhook.trigger_event not in valid_events:
        raise HTTPException(
            status_code=400,
            detail=f"trigger_event must be one of: {', '.join(valid_events)}"
        )

    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO zapier_webhooks (user_id, webhook_url, webhook_name, trigger_event)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, webhook.webhook_url, webhook.webhook_name, webhook.trigger_event)
        )
        await db.commit()
        webhook_id = cursor.lastrowid

    return {
        "success": True,
        "webhook_id": webhook_id,
        "message": f"Webhook '{webhook.webhook_name}' added successfully"
    }


@router.get("/integrations/zapier/webhooks")
async def get_zapier_webhooks(
    user_id: int = Depends(get_current_user_id),
):
    """Get all user's Zapier webhooks."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT id, webhook_url, webhook_name, trigger_event,
                   enabled, created_at, last_triggered_at, last_error
            FROM zapier_webhooks
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,)
        )
        rows = await cursor.fetchall()

        webhooks = []
        for row in rows:
            webhooks.append({
                "id": row["id"],
                "webhook_url": row["webhook_url"][:50] + "..." if len(row["webhook_url"]) > 50 else row["webhook_url"],
                "webhook_name": row["webhook_name"],
                "trigger_event": row["trigger_event"],
                "enabled": bool(row["enabled"]),
                "created_at": row["created_at"],
                "last_triggered_at": row["last_triggered_at"],
                "last_error": row["last_error"],
            })

        return {"webhooks": webhooks}


@router.post("/integrations/zapier/webhooks/{webhook_id}/test")
async def test_zapier_webhook(
    webhook_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Send test data to a webhook to verify it works."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id, webhook_url, webhook_name FROM zapier_webhooks WHERE id = ? AND user_id = ?",
            (webhook_id, user_id)
        )
        webhook = await cursor.fetchone()

        if not webhook:
            raise HTTPException(status_code=404, detail="Webhook not found")

    # Send test payload
    test_data = {
        "event": "test",
        "timestamp": "2025-12-23T12:00:00Z",
        "message": "Test webhook from ContentMultiplier",
        "data": {
            "job_id": "test_123",
            "filename": "test_webinar.mp4",
            "outputs_count": 3,
            "outputs": [
                {
                    "content_type": "linkedin",
                    "variation_number": 1,
                    "content": "This is a test LinkedIn post from ContentMultiplier.",
                    "quality_score": 85
                }
            ]
        }
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                webhook["webhook_url"],
                json=test_data,
                timeout=10.0,
                headers={"Content-Type": "application/json"}
            )

        if response.status_code == 200:
            return {
                "success": True,
                "message": "Test webhook sent successfully! Check your Zapier/integration."
            }
        else:
            return {
                "success": False,
                "status_code": response.status_code,
                "message": f"Webhook returned status {response.status_code}"
            }

    except httpx.TimeoutException:
        return {
            "success": False,
            "message": "Webhook request timed out"
        }
    except Exception as e:
        return {
            "success": False,
            "message": str(e)
        }


@router.put("/integrations/zapier/webhooks/{webhook_id}/toggle")
async def toggle_webhook(
    webhook_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Enable or disable a webhook."""
    async with get_db() as db:
        # Verify ownership
        cursor = await db.execute(
            "SELECT id, enabled FROM zapier_webhooks WHERE id = ? AND user_id = ?",
            (webhook_id, user_id)
        )
        webhook = await cursor.fetchone()

        if not webhook:
            raise HTTPException(status_code=404, detail="Webhook not found")

        new_state = not webhook["enabled"]

        await db.execute(
            "UPDATE zapier_webhooks SET enabled = ? WHERE id = ?",
            (new_state, webhook_id)
        )
        await db.commit()

    return {
        "success": True,
        "enabled": new_state,
        "message": f"Webhook {'enabled' if new_state else 'disabled'}"
    }


@router.delete("/integrations/zapier/webhooks/{webhook_id}")
async def delete_zapier_webhook(
    webhook_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Delete a webhook."""
    async with get_db() as db:
        # Verify ownership
        cursor = await db.execute(
            "SELECT id FROM zapier_webhooks WHERE id = ? AND user_id = ?",
            (webhook_id, user_id)
        )
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Webhook not found")

        await db.execute(
            "DELETE FROM zapier_webhooks WHERE id = ?",
            (webhook_id,)
        )
        await db.commit()

    return {"success": True, "message": "Webhook deleted"}


@router.get("/integrations/zapier/templates")
async def get_zapier_templates():
    """Get popular Zapier templates/use cases."""
    return {
        "templates": [
            {
                "name": "Auto-post to LinkedIn",
                "description": "Automatically post generated content to LinkedIn",
                "icon": "linkedin",
                "zap_url": "https://zapier.com/apps/webhook/integrations/linkedin"
            },
            {
                "name": "Save to Google Drive",
                "description": "Save generated content as documents in Google Drive",
                "icon": "google-drive",
                "zap_url": "https://zapier.com/apps/webhook/integrations/google-drive"
            },
            {
                "name": "Add to Notion",
                "description": "Add content to a Notion database for organization",
                "icon": "notion",
                "zap_url": "https://zapier.com/apps/webhook/integrations/notion"
            },
            {
                "name": "Send to Slack",
                "description": "Notify your team when new content is ready",
                "icon": "slack",
                "zap_url": "https://zapier.com/apps/webhook/integrations/slack"
            },
            {
                "name": "Add to Airtable",
                "description": "Track content in an Airtable base",
                "icon": "airtable",
                "zap_url": "https://zapier.com/apps/webhook/integrations/airtable"
            },
            {
                "name": "Send to Mailchimp",
                "description": "Use email content in Mailchimp campaigns",
                "icon": "mailchimp",
                "zap_url": "https://zapier.com/apps/webhook/integrations/mailchimp"
            },
        ]
    }
