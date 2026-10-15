# SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Webhook auto-sync for MCP listings: owner settings and the public GitHub and GitLab receivers."""

import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger as optic
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, get_effective_component_permission, require_role, resolve_listing
from api.ratelimit import _get_real_ip, limiter
from models.mcp import ListingStatus, McpListing
from models.mcp_webhook_sync import McpWebhookSync
from models.user import User, UserRole
from schemas.mcp_webhook_sync import McpWebhookDeliveryResponse, McpWebhookSyncRequest, McpWebhookSyncResponse
from services.dynamic_settings import decrypt_value, encrypt_value
from services.mcp_webhook_sync import (
    EVENT_HEADER,
    GITLAB_EVENT_HEADER,
    GITLAB_TOKEN_HEADER,
    SIGNATURE_HEADER,
    SyncRequest,
    detect_provider,
    enqueue_sync,
    generate_secret,
    gitlab_event_name,
    has_approved_version,
    plan_delivery,
    plan_gitlab_delivery,
    verify_gitlab_token,
    verify_signature,
)

router = APIRouter(prefix="/api/v1/mcps", tags=["mcp-webhook-sync"])
webhook_router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


async def _webhook_url(request: Request, sync: McpWebhookSync) -> str:
    from api.routes.config import derive_endpoints
    from config import settings

    base = (settings.WEBHOOK_PUBLIC_URL or "").strip().rstrip("/")
    if not base:
        base = (await derive_endpoints(request))["api"]
    return f"{base}/api/v1/webhooks/{sync.provider}/mcp/{sync.id}"


async def _response(request: Request, sync: McpWebhookSync | None, secret: str | None = None):
    if sync is None:
        return McpWebhookSyncResponse(enabled=False)
    return McpWebhookSyncResponse(
        enabled=True,
        id=sync.id,
        provider=sync.provider,
        webhook_url=await _webhook_url(request, sync),
        secret=secret,
        sync_on_push=sync.sync_on_push,
        sync_on_release=sync.sync_on_release,
        branch=sync.branch,
        last_delivery_at=sync.last_delivery_at,
        last_event=sync.last_event,
        last_sync_status=sync.last_sync_status,
        last_sync_error=sync.last_sync_error,
        last_synced_at=sync.last_synced_at,
        last_synced_sha=sync.last_synced_sha,
        last_version=sync.last_version,
        created_at=sync.created_at,
    )


async def _owned_listing(listing_id: str, db: AsyncSession, current_user: User) -> McpListing:
    listing = await resolve_listing(McpListing, listing_id, db, current_user=current_user)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if get_effective_component_permission(listing, current_user) != "owner":
        raise HTTPException(status_code=403, detail="Not the listing owner")
    return listing


async def _sync_for(db: AsyncSession, listing: McpListing) -> McpWebhookSync | None:
    return (
        await db.execute(select(McpWebhookSync).where(McpWebhookSync.listing_id == listing.id))
    ).scalar_one_or_none()


async def _queue(db: AsyncSession, sync: McpWebhookSync, request: SyncRequest) -> None:
    """Mark the sync queued, then hand it to the worker.

    "queued" is committed before the job exists, and nothing is written after a
    successful enqueue, so the worker's own status updates always land after it.
    If the job cannot be queued, the sync is marked failed instead of staying
    "queued" with nothing to run it.
    """
    sync.last_sync_status = "queued"
    sync.last_sync_error = None
    await db.commit()
    try:
        await enqueue_sync(sync.id, request)
    except Exception:
        optic.exception("mcp webhook sync could not be queued sync_id={}", sync.id)
        # Only while still "queued": if the job reached the queue before the error,
        # the worker may already have recorded a result, which must not be overwritten.
        await db.execute(
            update(McpWebhookSync)
            .where(McpWebhookSync.id == sync.id, McpWebhookSync.last_sync_status == "queued")
            .values(
                last_sync_status="failed",
                last_sync_error="Could not queue the sync job because the worker queue is unavailable. Try again later.",
            )
        )
        await db.commit()
        raise HTTPException(status_code=503, detail="Could not queue the sync job. Try again later.") from None


@router.get("/{listing_id}/webhook-sync", response_model=McpWebhookSyncResponse)
async def get_webhook_sync(
    listing_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("listing_id={}", listing_id)
    listing = await _owned_listing(listing_id, db, current_user)
    return await _response(request, await _sync_for(db, listing))


@router.put("/{listing_id}/webhook-sync", response_model=McpWebhookSyncResponse)
async def configure_webhook_sync(
    listing_id: str,
    req: McpWebhookSyncRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Turn on webhook sync, or change its triggers. The secret is returned only when sync is first turned on."""
    optic.trace("listing_id={}", listing_id)
    listing = await _owned_listing(listing_id, db, current_user)
    if not listing.git_url:
        raise HTTPException(status_code=400, detail="Webhook sync needs a git repository URL on the listing")
    if listing.status == ListingStatus.archived:
        raise HTTPException(status_code=400, detail="Archived listings cannot be synced")
    if not has_approved_version(listing):
        # Synced versions skip review, so the first version must still go through it.
        raise HTTPException(
            status_code=400, detail="Webhook sync is available once a reviewer has approved this MCP server"
        )

    sync = await _sync_for(db, listing)
    secret = None
    if sync is None:
        secret = generate_secret()
        sync = McpWebhookSync(
            listing_id=listing.id,
            provider=req.provider or detect_provider(listing.git_url),
            secret=encrypt_value(secret),
            enabled_by=current_user.id,
        )
        db.add(sync)
    elif req.provider:
        sync.provider = req.provider
    sync.sync_on_push = req.sync_on_push
    sync.sync_on_release = req.sync_on_release
    sync.branch = req.branch
    # Synced versions publish as whoever last configured sync.
    sync.enabled_by = current_user.id
    await db.commit()
    await db.refresh(sync)
    return await _response(request, sync, secret)


@router.post("/{listing_id}/webhook-sync/rotate-secret", response_model=McpWebhookSyncResponse)
async def rotate_webhook_secret(
    listing_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("listing_id={}", listing_id)
    listing = await _owned_listing(listing_id, db, current_user)
    sync = await _sync_for(db, listing)
    if sync is None:
        raise HTTPException(status_code=404, detail="Webhook sync is not enabled for this listing")
    secret = generate_secret()
    sync.secret = encrypt_value(secret)
    await db.commit()
    await db.refresh(sync)
    return await _response(request, sync, secret)


@router.post("/{listing_id}/webhook-sync/run", response_model=McpWebhookSyncResponse, status_code=202)
async def run_webhook_sync(
    listing_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    """Sync the tracked branch now, without waiting for a push."""
    optic.trace("listing_id={}", listing_id)
    listing = await _owned_listing(listing_id, db, current_user)
    sync = await _sync_for(db, listing)
    if sync is None:
        raise HTTPException(status_code=404, detail="Webhook sync is not enabled for this listing")
    await _queue(db, sync, SyncRequest(trigger="manual", ref=sync.branch))
    await db.refresh(sync)
    return await _response(request, sync)


@router.delete("/{listing_id}/webhook-sync")
async def disable_webhook_sync(
    listing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.user)),
):
    optic.trace("listing_id={}", listing_id)
    listing = await _owned_listing(listing_id, db, current_user)
    sync = await _sync_for(db, listing)
    if sync is None:
        raise HTTPException(status_code=404, detail="Webhook sync is not enabled for this listing")
    await db.delete(sync)
    await db.commit()
    return {"disabled": str(listing.id)}


async def _sync_for_provider(db: AsyncSession, sync_id: uuid.UUID, provider: str) -> McpWebhookSync:
    sync = await db.get(McpWebhookSync, sync_id)
    # A sync set up for the other provider is unknown here, so its secret is only
    # ever checked the way its own provider sends it.
    if sync is None or sync.provider != provider:
        raise HTTPException(status_code=404, detail="Unknown webhook")
    return sync


def _json_payload(body: bytes) -> dict:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(
            status_code=400, detail="Payload must be JSON. Set the webhook content type to application/json."
        ) from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload must be a JSON object")
    return payload


async def _handle_delivery(db: AsyncSession, sync: McpWebhookSync, event: str, payload: dict, planner):
    listing = await db.get(McpListing, sync.listing_id)
    try:
        plan = planner(sync, (listing.git_url if listing else None) or "", event, payload)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None

    sync.last_delivery_at = datetime.now(UTC)
    sync.last_event = event[:20] or None
    if isinstance(plan, str):
        # Ignored deliveries keep the last sync result; the provider shows the reason in its delivery log.
        await db.commit()
        return McpWebhookDeliveryResponse(status="ignored", reason=plan)

    await _queue(db, sync, plan)
    return McpWebhookDeliveryResponse(status="queued", trigger=plan.trigger, ref=plan.ref)


def _webhook_rate_key(request: Request) -> str:
    """One bucket per sync and sender, so junk sent to one webhook URL cannot use up another's.

    Behind the API Gateway webhook endpoint every delivery reaches the app from the VPC
    link's address, so the sync id is what keeps hooks apart there.
    """
    return f"mcp-webhook:{request.path_params.get('sync_id')}:{_get_real_ip(request)}"


@webhook_router.post("/github/mcp/{sync_id}", response_model=McpWebhookDeliveryResponse, status_code=202)
@limiter.limit("60/minute", key_func=_webhook_rate_key)
async def receive_github_webhook(
    sync_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Receive a GitHub webhook delivery. Authenticated by the HMAC signature, not a user token."""
    optic.trace("sync_id={}", sync_id)
    body = await request.body()
    sync = await _sync_for_provider(db, sync_id, "github")
    if not verify_signature(decrypt_value(sync.secret), body, request.headers.get(SIGNATURE_HEADER)):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    event = request.headers.get(EVENT_HEADER, "")
    return await _handle_delivery(db, sync, event, _json_payload(body), plan_delivery)


@webhook_router.post("/gitlab/mcp/{sync_id}", response_model=McpWebhookDeliveryResponse, status_code=202)
@limiter.limit("60/minute", key_func=_webhook_rate_key)
async def receive_gitlab_webhook(
    sync_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Receive a GitLab webhook delivery. Authenticated by the secret token header, not a user token."""
    optic.trace("sync_id={}", sync_id)
    body = await request.body()
    sync = await _sync_for_provider(db, sync_id, "gitlab")
    if not verify_gitlab_token(decrypt_value(sync.secret), request.headers.get(GITLAB_TOKEN_HEADER)):
        raise HTTPException(status_code=401, detail="Invalid webhook token")
    payload = _json_payload(body)
    event = gitlab_event_name(request.headers.get(GITLAB_EVENT_HEADER), payload)
    return await _handle_delivery(db, sync, event, payload, plan_gitlab_delivery)
