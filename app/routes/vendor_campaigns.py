"""Vendor campaign management endpoints.

Vendors can create and manage store-specific campaigns.
All operations are scoped to the vendor's own store.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.controllers.vendor_campaigns_controller import (
    create_vendor_campaign,
    list_vendor_campaigns,
    get_vendor_campaign,
    update_vendor_campaign,
    VendorCampaignCreate,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User

router = APIRouter(prefix="/vendor/campaigns", tags=["vendor-campaigns"])


class CreateCampaignRequest(BaseModel):
    """Request to create a store campaign."""

    title: str
    subtitle: str | None = None
    description: str | None = None
    banner_image_url: str | None = None
    starts_at: str | None = None  # ISO format datetime
    ends_at: str | None = None
    product_ids: list[str] | None = None
    eligibility_rules: dict | None = None


@router.get("")
async def list_my_campaigns(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all campaigns you've created for your store.

    **Security:** Only shows your campaigns.
    """
    return await list_vendor_campaigns(db, current_user)


@router.post("")
async def create_my_campaign(
    payload: CreateCampaignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new campaign for your store.

    **How it works:**
    1. You create a campaign
    2. ODOS team reviews it
    3. Once approved, it appears in the app

    **You can set:**
    - Title & description
    - Banner image
    - Which products to feature
    - Start/end dates (for limited-time campaigns)
    - Eligibility rules (e.g., "only for first-time buyers")

    **Security:** Campaign is automatically assigned to YOUR store only.
    You cannot create campaigns for other stores.
    """
    try:
        payload_dict = {
            "title": payload.title,
            "subtitle": payload.subtitle,
            "description": payload.description,
            "banner_image_url": payload.banner_image_url,
            "starts_at": payload.starts_at,
            "ends_at": payload.ends_at,
            "product_ids": payload.product_ids,
            "eligibility_rules": payload.eligibility_rules,
        }
        campaign_create = VendorCampaignCreate()
        for key, value in payload_dict.items():
            setattr(campaign_create, key, value)

        return await create_vendor_campaign(db, current_user, campaign_create)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.get("/{campaign_id}")
async def get_my_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get details of one of your campaigns.

    **Security:** You can only access campaigns from your store.
    """
    return await get_vendor_campaign(db, current_user, campaign_id)


@router.put("/{campaign_id}")
async def update_my_campaign(
    campaign_id: str,
    payload: CreateCampaignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a campaign you created.

    **Restrictions:**
    - Can only edit DRAFT campaigns
    - Once submitted for review, cannot be changed
    - Create a new campaign if you want to try something different

    **Security:** You can only edit your own campaigns.
    """
    try:
        payload_dict = {
            "title": payload.title,
            "subtitle": payload.subtitle,
            "description": payload.description,
            "banner_image_url": payload.banner_image_url,
            "starts_at": payload.starts_at,
            "ends_at": payload.ends_at,
            "product_ids": payload.product_ids,
            "eligibility_rules": payload.eligibility_rules,
        }
        campaign_create = VendorCampaignCreate()
        for key, value in payload_dict.items():
            setattr(campaign_create, key, value)

        return await update_vendor_campaign(db, current_user, campaign_id, campaign_create)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
