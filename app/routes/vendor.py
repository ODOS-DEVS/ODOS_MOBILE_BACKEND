import json
from typing import Annotated
from uuid import UUID

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.controllers.campaign_controller import (
    create_vendor_campaign_opt_in,
    list_vendor_campaign_opt_ins,
    list_vendor_open_campaigns,
)
from app.controllers.flash_sale_nominations_controller import (
    create_vendor_flash_sale_nomination,
    list_vendor_flash_sale_nominations,
)
from app.schemas.admin import AdminMerchandisingCampaignOptInRead
from app.schemas.catalog import MerchandisingCampaignRead
from app.controllers.vendor_controller import (
    archive_vendor_voucher,
    bulk_update_vendor_products,
    create_vendor_voucher,
    create_vendor_product,
    delete_vendor_product,
    fetch_my_vendor_application,
    fetch_vendor_dashboard,
    fetch_vendor_profile,
    fetch_vendor_store,
    gift_vendor_voucher,
    get_vendor_order,
    acknowledge_vendor_order,
    fetch_vendor_analytics,
    get_vendor_return_request,
    list_vendor_customers,
    list_vendor_orders,
    list_vendor_product_inventory_movements,
    list_vendor_products,
    list_vendor_return_requests,
    list_vendor_reviews,
    list_vendor_voucher_redemptions,
    list_vendor_vouchers,
    notify_vendor_order_departure,
    patch_vendor_product_stock,
    patch_vendor_return_request,
    reply_to_vendor_review,
    set_vendor_order_dispatch_photo,
    submit_vendor_application,
    update_vendor_order_status,
    update_vendor_product,
    update_vendor_product_status,
    update_vendor_store,
    update_vendor_voucher,
)
from app.controllers.wallet_controller import (
    create_vendor_withdrawal_request,
    fetch_vendor_wallet,
    list_vendor_payout_institutions,
    update_vendor_payout_details,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.user import MessageResponse
from app.controllers.store_section_controller import (
    add_products_to_section,
    fetch_section_product_ids,
    create_vendor_section,
    delete_vendor_section,
    fetch_starter_suggestions,
    fetch_vendor_sections,
    remove_product_from_section,
    reorder_vendor_sections,
    update_vendor_section,
)
from app.schemas.vendor import (
    VendorStoreSectionCreate,
    VendorStoreSectionProductsUpdate,
    VendorStoreSectionRead,
    VendorStoreSectionReorder,
    VendorStoreSectionSuggestions,
    VendorStoreSectionUpdate,
)
from app.schemas.vendor import (
    VendorAnalyticsRead,
    VendorApplicationRead,
    VendorCustomerRead,
    VendorDashboardRead,
    VendorInventoryMovementRead,
    VendorOrderRead,
    VendorOrderStatusUpdate,
    VendorProductBulkUpdate,
    VendorProductCreate,
    VendorProductRead,
    VendorProductStatusUpdate,
    VendorProductStockUpdate,
    VendorProductUpdate,
    VendorProfileRead,
    VendorReturnRequestRead,
    VendorReturnRequestUpdate,
    VendorReviewReplyUpdate,
    VendorReviewRead,
    VendorStoreRead,
    VendorVoucherGiftPayload,
    VendorVoucherRead,
    VendorVoucherRedemptionRead,
    VendorVoucherUpsert,
    VendorFlashSaleNominationCreate,
    VendorFlashSaleNominationRead,
    VendorPayoutInstitutionRead,
    VendorWalletPayoutDetailsUpdate,
    VendorWalletRead,
    VendorWithdrawalCreate,
    VendorWithdrawalRequestRead,
)

router = APIRouter(prefix="/vendor", tags=["vendor"])


def _split_csv_values(value: str | None) -> list[str] | None:
    if not value:
        return None
    cleaned = [item.strip() for item in value.split(",") if item.strip()]
    return cleaned or None


def _split_multiline_values(value: str | None) -> list[str] | None:
    if not value:
        return None
    cleaned = [item.strip() for item in value.splitlines() if item.strip()]
    return cleaned or None


def _parse_optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return int(cleaned)


def _parse_optional_float(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return float(cleaned)


@router.post(
    "/applications",
    response_model=VendorApplicationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_vendor_application(
    business_name: Annotated[str, Form(min_length=2, max_length=160)],
    business_category: Annotated[str, Form(min_length=2, max_length=120)],
    business_description: Annotated[str, Form(min_length=20, max_length=1000)],
    phone_number: Annotated[str, Form(min_length=7, max_length=30)],
    region: Annotated[str, Form(min_length=2, max_length=120)],
    city: Annotated[str, Form(min_length=2, max_length=120)],
    store_name: Annotated[str, Form(min_length=2, max_length=160)],
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    whatsapp_number: Annotated[str | None, Form(max_length=30)] = None,
    market_id: Annotated[str | None, Form(max_length=50)] = None,
    store_location: Annotated[str | None, Form(max_length=255)] = None,
    store_latitude: Annotated[str | None, Form(max_length=32)] = None,
    store_longitude: Annotated[str | None, Form(max_length=32)] = None,
    store_instagram_url: Annotated[str | None, Form(max_length=255)] = None,
    store_facebook_url: Annotated[str | None, Form(max_length=255)] = None,
    store_tiktok_url: Annotated[str | None, Form(max_length=255)] = None,
    store_twitter_url: Annotated[str | None, Form(max_length=255)] = None,
    store_whatsapp_url: Annotated[str | None, Form(max_length=255)] = None,
    store_website_url: Annotated[str | None, Form(max_length=255)] = None,
    store_description: Annotated[str | None, Form(max_length=1000)] = None,
    ghana_card_number: Annotated[str | None, Form(max_length=60)] = None,
    business_registration_number: Annotated[str | None, Form(max_length=120)] = None,
    logo_image: UploadFile | None = File(default=None),
    banner_image: UploadFile | None = File(default=None),
    shop_image: UploadFile | None = File(default=None),
):
    return await submit_vendor_application(
        db,
        current_user,
        business_name=business_name,
        business_category=business_category,
        business_description=business_description,
        phone_number=phone_number,
        whatsapp_number=whatsapp_number,
        region=region,
        city=city,
        market_id=market_id,
        store_location=store_location,
        store_latitude=_parse_optional_float(store_latitude),
        store_longitude=_parse_optional_float(store_longitude),
        store_instagram_url=store_instagram_url,
        store_facebook_url=store_facebook_url,
        store_tiktok_url=store_tiktok_url,
        store_twitter_url=store_twitter_url,
        store_whatsapp_url=store_whatsapp_url,
        store_website_url=store_website_url,
        store_name=store_name,
        store_description=store_description,
        ghana_card_number=ghana_card_number,
        business_registration_number=business_registration_number,
        logo_image=logo_image,
        banner_image=banner_image,
        shop_image=shop_image,
    )


@router.get("/applications/me", response_model=VendorApplicationRead | None)
def get_my_vendor_application(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return fetch_my_vendor_application(db, current_user)


@router.get("/me", response_model=VendorProfileRead | None)
def get_vendor_profile(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return fetch_vendor_profile(db, current_user)


@router.get("/dashboard", response_model=VendorDashboardRead)
def get_vendor_dashboard(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return fetch_vendor_dashboard(db, current_user)


@router.get("/analytics", response_model=VendorAnalyticsRead)
def get_vendor_analytics(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    period: Annotated[str, Query(max_length=10)] = "30d",
):
    return fetch_vendor_analytics(db, current_user, period=period)


@router.get("/reviews", response_model=list[VendorReviewRead])
def get_vendor_reviews(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    q: Annotated[str | None, Query(max_length=160)] = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    offset: Annotated[int | None, Query(ge=0)] = None,
):
    return list_vendor_reviews(db, current_user, q=q, limit=limit, offset=offset)


@router.patch("/reviews/{review_id}/reply", response_model=VendorReviewRead)
def patch_vendor_review_reply(
    review_id: str,
    payload: VendorReviewReplyUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return reply_to_vendor_review(db, current_user, review_id, payload)


@router.get("/customers", response_model=list[VendorCustomerRead])
def get_vendor_customers(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    q: Annotated[str | None, Query(max_length=160)] = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    offset: Annotated[int | None, Query(ge=0)] = None,
):
    return list_vendor_customers(db, current_user, q=q, limit=limit, offset=offset)


@router.get("/returns", response_model=list[VendorReturnRequestRead])
def get_vendor_returns(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_vendor_return_requests(db, current_user)


@router.get("/returns/{return_request_id}", response_model=VendorReturnRequestRead)
def get_vendor_return_detail(
    return_request_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return get_vendor_return_request(db, current_user, return_request_id)


@router.patch("/returns/{return_request_id}", response_model=VendorReturnRequestRead)
def patch_vendor_return(
    return_request_id: str,
    payload: VendorReturnRequestUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return patch_vendor_return_request(db, current_user, return_request_id, payload)


@router.get("/wallet", response_model=VendorWalletRead)
def get_vendor_wallet(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return fetch_vendor_wallet(db, current_user)


@router.get("/wallet/payout-institutions", response_model=list[VendorPayoutInstitutionRead])
def get_vendor_wallet_payout_institutions(
    payout_method_type: str,
    current_user: Annotated[User, Depends(get_current_user)],
):
    return list_vendor_payout_institutions(current_user, payout_method_type)


@router.patch("/wallet/payout-details", response_model=VendorWalletRead)
def patch_vendor_wallet_payout_details(
    payload: VendorWalletPayoutDetailsUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return update_vendor_payout_details(db, current_user, payload)


@router.post(
    "/wallet/withdrawals",
    response_model=VendorWithdrawalRequestRead,
    status_code=status.HTTP_201_CREATED,
)
def post_vendor_wallet_withdrawal(
    payload: VendorWithdrawalCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return create_vendor_withdrawal_request(db, current_user, payload)


@router.get("/products", response_model=list[VendorProductRead])
def get_vendor_products(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    q: Annotated[str | None, Query(max_length=160)] = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    offset: Annotated[int | None, Query(ge=0)] = None,
):
    return list_vendor_products(db, current_user, q=q, limit=limit, offset=offset)


@router.post("/products", response_model=VendorProductRead, status_code=status.HTTP_201_CREATED)
async def add_vendor_product(
    name: Annotated[str, Form(min_length=1, max_length=255)],
    description: Annotated[str, Form(min_length=1, max_length=1000)],
    category: Annotated[str, Form(min_length=1, max_length=120)],
    price: Annotated[int, Form(ge=0)],
    stock: Annotated[int, Form(ge=0)],
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    old_price: Annotated[str | None, Form()] = None,
    category_slug: Annotated[str | None, Form(max_length=120)] = None,
    subcategory: Annotated[str | None, Form(max_length=120)] = None,
    image_key: Annotated[str, Form(min_length=1, max_length=100)] = "bag",
    image_url: Annotated[str | None, Form(max_length=500)] = None,
    placement_tags: Annotated[str | None, Form(max_length=255)] = None,
    color_options: Annotated[str | None, Form(max_length=255)] = None,
    size_options: Annotated[str | None, Form(max_length=255)] = None,
    specifications: Annotated[str | None, Form(max_length=3000)] = None,
    is_returnable: Annotated[bool, Form()] = True,
    images: list[UploadFile] | None = File(default=None),
):
    payload = VendorProductCreate(
        name=name,
        description=description,
        category=category,
        category_slug=category_slug,
        subcategory=subcategory,
        price=price,
        old_price=_parse_optional_int(old_price),
        stock=stock,
        image_key=image_key,
        image_url=image_url,
        placement_tags=_split_csv_values(placement_tags),
        color_options=_split_csv_values(color_options),
        size_options=_split_csv_values(size_options),
        specifications=_split_multiline_values(specifications),
        is_returnable=is_returnable,
    )
    return await create_vendor_product(db, current_user, payload, images)


@router.patch("/products/bulk", response_model=list[VendorProductRead])
def patch_vendor_products_bulk(
    payload: VendorProductBulkUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return bulk_update_vendor_products(
        db,
        current_user,
        product_ids=payload.product_ids,
        stock=payload.stock,
        status=payload.status,
    )


@router.patch("/products/{product_id}", response_model=VendorProductRead)
async def patch_vendor_product(
    product_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    name: Annotated[str | None, Form(min_length=1, max_length=255)] = None,
    description: Annotated[str | None, Form(min_length=1, max_length=1000)] = None,
    category: Annotated[str | None, Form(min_length=1, max_length=120)] = None,
    category_slug: Annotated[str | None, Form(max_length=120)] = None,
    subcategory: Annotated[str | None, Form(max_length=120)] = None,
    price: Annotated[int | None, Form(ge=0)] = None,
    old_price: Annotated[str | None, Form()] = None,
    stock: Annotated[int | None, Form(ge=0)] = None,
    image_key: Annotated[str | None, Form(min_length=1, max_length=100)] = None,
    image_url: Annotated[str | None, Form(max_length=500)] = None,
    image_urls: Annotated[str | None, Form(max_length=4000)] = None,
    placement_tags: Annotated[str | None, Form(max_length=255)] = None,
    color_options: Annotated[str | None, Form(max_length=255)] = None,
    size_options: Annotated[str | None, Form(max_length=255)] = None,
    specifications: Annotated[str | None, Form(max_length=3000)] = None,
    is_returnable: Annotated[bool | None, Form()] = None,
    status_value: Annotated[str | None, Form(alias="status", max_length=30)] = None,
    images: list[UploadFile] | None = File(default=None),
):
    payload = VendorProductUpdate(
        name=name,
        description=description,
        category=category,
        category_slug=category_slug,
        subcategory=subcategory,
        price=price,
        old_price=_parse_optional_int(old_price),
        stock=stock,
        image_key=image_key,
        image_url=image_url,
        image_urls=_split_multiline_values(image_urls),
        placement_tags=_split_csv_values(placement_tags),
        color_options=_split_csv_values(color_options),
        size_options=_split_csv_values(size_options),
        specifications=_split_multiline_values(specifications),
        is_returnable=is_returnable,
        status=status_value,
    )
    return await update_vendor_product(db, current_user, product_id, payload, images)


@router.delete("/products/{product_id}", response_model=MessageResponse)
def remove_vendor_product(
    product_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    delete_vendor_product(db, current_user, product_id)
    return MessageResponse(message="Product removed successfully.")


@router.patch("/products/{product_id}/status", response_model=VendorProductRead)
def patch_vendor_product_status(
    product_id: str,
    payload: VendorProductStatusUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return update_vendor_product_status(db, current_user, product_id, payload.status)


@router.patch("/products/{product_id}/stock", response_model=VendorProductRead)
def patch_vendor_product_stock_route(
    product_id: str,
    payload: VendorProductStockUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return patch_vendor_product_stock(db, current_user, product_id, payload.stock)


@router.get(
    "/products/{product_id}/inventory-movements",
    response_model=list[VendorInventoryMovementRead],
)
def get_vendor_product_inventory_movements(
    product_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    limit: Annotated[int | None, Query(ge=1, le=100)] = 30,
    offset: Annotated[int | None, Query(ge=0)] = 0,
):
    return list_vendor_product_inventory_movements(
        db,
        current_user,
        product_id,
        limit=limit,
        offset=offset,
    )


@router.get("/orders", response_model=list[VendorOrderRead])
def get_vendor_orders(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    q: Annotated[str | None, Query(max_length=160)] = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    offset: Annotated[int | None, Query(ge=0)] = None,
):
    return list_vendor_orders(db, current_user, q=q, limit=limit, offset=offset)


@router.get("/orders/{order_id}", response_model=VendorOrderRead)
def get_vendor_order_detail(
    order_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return get_vendor_order(db, current_user, order_id)


@router.post("/orders/{order_id}/acknowledge", response_model=VendorOrderRead)
def post_vendor_order_acknowledge(
    order_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return acknowledge_vendor_order(db, current_user, order_id)


@router.get("/vouchers", response_model=list[VendorVoucherRead])
def get_vendor_vouchers(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_vendor_vouchers(db, current_user)


@router.post("/vouchers", response_model=VendorVoucherRead, status_code=status.HTTP_201_CREATED)
def post_vendor_voucher(
    payload: VendorVoucherUpsert,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return create_vendor_voucher(db, current_user, payload)


@router.patch("/vouchers/{voucher_id}", response_model=VendorVoucherRead)
def patch_vendor_voucher(
    voucher_id: str,
    payload: VendorVoucherUpsert,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return update_vendor_voucher(db, current_user, voucher_id, payload)


@router.post("/vouchers/{voucher_id}/gift", response_model=VendorVoucherRead)
def post_vendor_voucher_gift(
    voucher_id: str,
    payload: VendorVoucherGiftPayload,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return gift_vendor_voucher(db, current_user, voucher_id, payload)


@router.delete("/vouchers/{voucher_id}", response_model=MessageResponse)
def delete_vendor_voucher(
    voucher_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    archive_vendor_voucher(db, current_user, voucher_id)
    return MessageResponse(message="Store promotion archived successfully.")


@router.get(
    "/vouchers/{voucher_id}/redemptions",
    response_model=list[VendorVoucherRedemptionRead],
)
def get_vendor_voucher_redemptions(
    voucher_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_vendor_voucher_redemptions(db, current_user, voucher_id)


@router.get("/flash-sale-nominations", response_model=list[VendorFlashSaleNominationRead])
def get_vendor_flash_sale_nominations(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_vendor_flash_sale_nominations(db, current_user)


@router.post(
    "/flash-sale-nominations",
    response_model=VendorFlashSaleNominationRead,
    status_code=status.HTTP_201_CREATED,
)
def post_vendor_flash_sale_nomination(
    payload: VendorFlashSaleNominationCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return create_vendor_flash_sale_nomination(db, current_user, payload)


class VendorCampaignOptInCreate(BaseModel):
    campaign_id: UUID
    product_id: str = Field(min_length=1, max_length=100)


@router.get("/merchandising-campaigns/open", response_model=list[MerchandisingCampaignRead])
def get_vendor_open_merchandising_campaigns(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_vendor_open_campaigns(db, current_user)


@router.get(
    "/merchandising-campaign-opt-ins",
    response_model=list[AdminMerchandisingCampaignOptInRead],
)
def get_vendor_merchandising_campaign_opt_ins(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_vendor_campaign_opt_ins(db, current_user)


@router.post(
    "/merchandising-campaign-opt-ins",
    response_model=AdminMerchandisingCampaignOptInRead,
    status_code=status.HTTP_201_CREATED,
)
def post_vendor_merchandising_campaign_opt_in(
    payload: VendorCampaignOptInCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return create_vendor_campaign_opt_in(
        db,
        current_user,
        campaign_id=payload.campaign_id,
        product_id=payload.product_id,
    )


@router.patch("/orders/{order_id}/status", response_model=VendorOrderRead)
def patch_vendor_order_status(
    order_id: str,
    payload: VendorOrderStatusUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return update_vendor_order_status(db, current_user, order_id, payload)


@router.post("/orders/{order_id}/dispatch-photo", response_model=VendorOrderRead)
async def post_vendor_order_dispatch_photo(
    order_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    photo: UploadFile = File(...),
):
    return await set_vendor_order_dispatch_photo(db, current_user, order_id, photo)


@router.post("/orders/{order_id}/notify-departure", response_model=VendorOrderRead)
def post_vendor_order_notify_departure(
    order_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return notify_vendor_order_departure(db, current_user, order_id)


@router.get("/store", response_model=VendorStoreRead)
def get_vendor_store_profile(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return fetch_vendor_store(db, current_user)


@router.patch("/store", response_model=VendorStoreRead)
async def patch_vendor_store(
    name: Annotated[str, Form(min_length=2, max_length=160)],
    description: Annotated[str, Form(min_length=12, max_length=255)],
    category: Annotated[str, Form(min_length=2, max_length=120)],
    region: Annotated[str, Form(min_length=2, max_length=120)],
    city: Annotated[str, Form(min_length=2, max_length=120)],
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    market_id: Annotated[str | None, Form(max_length=50)] = None,
    audience_slugs: Annotated[str | None, Form(max_length=255)] = None,
    location: Annotated[str | None, Form(max_length=255)] = None,
    phone: Annotated[str | None, Form(max_length=30)] = None,
    latitude: Annotated[str | None, Form(max_length=32)] = None,
    longitude: Annotated[str | None, Form(max_length=32)] = None,
    instagram_url: Annotated[str | None, Form(max_length=255)] = None,
    facebook_url: Annotated[str | None, Form(max_length=255)] = None,
    tiktok_url: Annotated[str | None, Form(max_length=255)] = None,
    twitter_url: Annotated[str | None, Form(max_length=255)] = None,
    whatsapp_url: Annotated[str | None, Form(max_length=255)] = None,
    website_url: Annotated[str | None, Form(max_length=255)] = None,
    is_on_vacation: Annotated[bool | None, Form()] = None,
    vacation_message: Annotated[str | None, Form(max_length=500)] = None,
    business_hours: Annotated[str | None, Form(max_length=4000)] = None,
    logo_image: UploadFile | None = File(default=None),
    banner_image: UploadFile | None = File(default=None),
):
    business_hours_provided = business_hours is not None
    parsed_business_hours: dict | None = None
    if business_hours_provided:
        cleaned_business_hours = business_hours.strip()
        if cleaned_business_hours:
            try:
                parsed_business_hours = json.loads(cleaned_business_hours)
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="business_hours must be valid JSON.",
                ) from exc

    return await update_vendor_store(
        db,
        current_user,
        name=name,
        description=description,
        category=category,
        audience_slugs=_split_csv_values(audience_slugs),
        market_id=market_id,
        location=location,
        phone=phone,
        latitude=_parse_optional_float(latitude),
        longitude=_parse_optional_float(longitude),
        instagram_url=instagram_url,
        facebook_url=facebook_url,
        tiktok_url=tiktok_url,
        twitter_url=twitter_url,
        whatsapp_url=whatsapp_url,
        website_url=website_url,
        region=region,
        city=city,
        is_on_vacation=is_on_vacation,
        vacation_message=vacation_message,
        business_hours=parsed_business_hours,
        business_hours_provided=business_hours_provided,
        logo_image=logo_image,
        banner_image=banner_image,
    )


@router.get("/store/sections", response_model=list[VendorStoreSectionRead])
def get_store_sections(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return fetch_vendor_sections(db, current_user)


@router.get("/store/sections/starter-suggestions", response_model=VendorStoreSectionSuggestions)
def get_store_section_suggestions(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Shelves to offer a vendor whose sections screen is empty."""
    return fetch_starter_suggestions(db, current_user)


@router.post(
    "/store/sections",
    response_model=VendorStoreSectionRead,
    status_code=status.HTTP_201_CREATED,
)
def post_store_section(
    payload: VendorStoreSectionCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return create_vendor_section(db, current_user, payload)


@router.post("/store/sections/reorder", response_model=list[VendorStoreSectionRead])
def post_store_sections_reorder(
    payload: VendorStoreSectionReorder,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return reorder_vendor_sections(db, current_user, payload)


@router.patch("/store/sections/{section_id}", response_model=VendorStoreSectionRead)
def patch_store_section(
    section_id: uuid.UUID,
    payload: VendorStoreSectionUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return update_vendor_section(db, current_user, section_id, payload)


@router.delete("/store/sections/{section_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_store_section(
    section_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    delete_vendor_section(db, current_user, section_id)


@router.get("/store/sections/{section_id}/products", response_model=list[str])
def get_store_section_products(
    section_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Product ids on this shelf, so the picker can show them already ticked."""
    return fetch_section_product_ids(db, current_user, section_id)


@router.post("/store/sections/{section_id}/products", response_model=VendorStoreSectionRead)
def post_store_section_products(
    section_id: uuid.UUID,
    payload: VendorStoreSectionProductsUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return add_products_to_section(db, current_user, section_id, payload)


@router.delete(
    "/store/sections/{section_id}/products/{product_id}",
    response_model=VendorStoreSectionRead,
)
def delete_store_section_product(
    section_id: uuid.UUID,
    product_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return remove_product_from_section(db, current_user, section_id, product_id)
