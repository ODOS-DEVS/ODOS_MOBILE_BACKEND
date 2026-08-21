"""Vendor campaign creation and management.

Vendors can create store-specific campaigns.
Security: Vendors can ONLY create/edit campaigns for their own store.
"""

from datetime import datetime, timezone
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    User,
    Store,
    MerchandisingCampaign,
    MerchandisingCampaignStore,
)
from app.schemas.catalog import MerchandisingCampaignRead
from app.services.campaign_service import campaign_is_live, derive_campaign_status
from app.services.eligibility_service import EligibilityRules


class VendorCampaignCreate:
    """Request schema for vendor campaign creation."""

    title: str
    subtitle: str | None = None
    description: str | None = None
    banner_image_url: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    product_ids: list[str] | None = None
    eligibility_rules: dict | None = None


async def get_vendor_store(db: Session, vendor_user: User) -> Store:
    """Get vendor's store. Verify vendor ownership.

    Security: Ensures user is a vendor with a store.
    """
    if not vendor_user or vendor_user.role != "vendor":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only vendors can create campaigns.",
        )

    store = db.scalar(select(Store).where(Store.owner_user_id == vendor_user.id))
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor store not found.",
        )

    return store


async def create_vendor_campaign(
    db: Session,
    vendor_user: User,
    payload: VendorCampaignCreate,
) -> dict:
    """Create a campaign for vendor's store.

    Security: Campaign is automatically assigned to vendor's store only.
    Vendor cannot create campaigns for other stores.
    """
    store = await get_vendor_store(db, vendor_user)

    # Validate dates
    if payload.starts_at and payload.ends_at:
        if payload.ends_at < payload.starts_at:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="End time must be after start time.",
            )

    # Create campaign
    campaign = MerchandisingCampaign(
        id=uuid.uuid4(),
        slug=payload.title.lower().replace(" ", "-")[:80],
        title=payload.title,
        subtitle=payload.subtitle,
        description=payload.description,
        banner_image_url=payload.banner_image_url,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        status="draft",
        is_active=True,
        visibility="public",
        product_ids=payload.product_ids or [],
        eligibility_rules=payload.eligibility_rules,
    )

    db.add(campaign)
    db.flush()

    # Assign to vendor's store only (Security: locked to this store)
    campaign_store = MerchandisingCampaignStore(
        campaign_id=campaign.id,
        store_id=store.id,
    )
    db.add(campaign_store)
    db.commit()

    return {
        "id": str(campaign.id),
        "slug": campaign.slug,
        "title": campaign.title,
        "status": campaign.status,
        "store_id": store.id,
        "message": "Campaign created! Once approved by ODOS team, it will be visible to customers.",
    }


async def list_vendor_campaigns(
    db: Session,
    vendor_user: User,
) -> list[dict]:
    """List all campaigns created by this vendor.

    Security: Only shows vendor's own campaigns.
    """
    store = await get_vendor_store(db, vendor_user)

    # Get campaigns assigned to this vendor's store
    campaigns = db.scalars(
        select(MerchandisingCampaign)
        .join(
            MerchandisingCampaignStore,
            MerchandisingCampaign.id == MerchandisingCampaignStore.campaign_id,
        )
        .where(MerchandisingCampaignStore.store_id == store.id)
        .order_by(MerchandisingCampaign.created_at.desc())
    ).all()

    now = datetime.now(timezone.utc)
    return [
        {
            "id": str(c.id),
            "slug": c.slug,
            "title": c.title,
            "status": derive_campaign_status(c, now=now),
            "starts_at": c.starts_at.isoformat() if c.starts_at else None,
            "ends_at": c.ends_at.isoformat() if c.ends_at else None,
            "is_live": campaign_is_live(c, now=now),
            "product_count": len(c.product_ids or []),
        }
        for c in campaigns
    ]


async def get_vendor_campaign(
    db: Session,
    vendor_user: User,
    campaign_id: str,
) -> dict:
    """Get details of a vendor's campaign.

    Security: Vendor can only access their own campaign.
    """
    store = await get_vendor_store(db, vendor_user)

    campaign = db.scalar(
        select(MerchandisingCampaign)
        .join(
            MerchandisingCampaignStore,
            MerchandisingCampaign.id == MerchandisingCampaignStore.campaign_id,
        )
        .where(
            MerchandisingCampaign.id == campaign_id,
            MerchandisingCampaignStore.store_id == store.id,  # Security check
        )
    )

    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found or does not belong to your store.",
        )

    now = datetime.now(timezone.utc)
    return {
        "id": str(campaign.id),
        "slug": campaign.slug,
        "title": campaign.title,
        "subtitle": campaign.subtitle,
        "description": campaign.description,
        "banner_image_url": campaign.banner_image_url,
        "status": derive_campaign_status(campaign, now=now),
        "starts_at": campaign.starts_at.isoformat() if campaign.starts_at else None,
        "ends_at": campaign.ends_at.isoformat() if campaign.ends_at else None,
        "product_ids": campaign.product_ids or [],
        "eligibility_rules": campaign.eligibility_rules,
        "is_live": campaign_is_live(campaign, now=now),
    }


async def update_vendor_campaign(
    db: Session,
    vendor_user: User,
    campaign_id: str,
    payload: VendorCampaignCreate,
) -> dict:
    """Update a vendor's campaign.

    Security: Vendor can only update their own campaign.
    Only draft campaigns can be edited.
    """
    store = await get_vendor_store(db, vendor_user)

    campaign = db.scalar(
        select(MerchandisingCampaign)
        .join(
            MerchandisingCampaignStore,
            MerchandisingCampaign.id == MerchandisingCampaignStore.campaign_id,
        )
        .where(
            MerchandisingCampaign.id == campaign_id,
            MerchandisingCampaignStore.store_id == store.id,  # Security check
        )
    )

    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found.",
        )

    # Only allow editing draft campaigns
    if campaign.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only edit draft campaigns. Once submitted, campaigns cannot be changed.",
        )

    # Update fields
    campaign.title = payload.title
    campaign.subtitle = payload.subtitle
    campaign.description = payload.description
    campaign.banner_image_url = payload.banner_image_url
    campaign.starts_at = payload.starts_at
    campaign.ends_at = payload.ends_at
    campaign.product_ids = payload.product_ids or []
    campaign.eligibility_rules = payload.eligibility_rules

    db.commit()

    return {
        "id": str(campaign.id),
        "title": campaign.title,
        "status": campaign.status,
        "message": "Campaign updated successfully!",
    }
