from typing import Annotated
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.controllers.admin_metrics_controller import (
    get_sales_chart_timeseries,
    get_top_vendors,
    get_kpi_metrics,
)
from app.controllers.admin_controller import (
    archive_admin_voucher,
    archive_admin_promo_banner,
    archive_admin_flash_sale_event,
    bootstrap_first_admin,
    bulk_generate_admin_vouchers,
    create_admin_category,
    create_admin_product,
    create_admin_market,
    create_admin_promo_banner,
    create_admin_flash_sale_event,
    create_admin_store,
    create_admin_voucher,
    duplicate_admin_voucher,
    pause_admin_voucher,
    resume_admin_voucher,
    delete_admin_category,
    delete_admin_market,
    get_admin_bootstrap_status,
    get_admin_dashboard,
    get_admin_me,
    get_admin_finance_overview_payload,
    get_admin_promotion_analytics,
    get_admin_order,
    get_admin_product,
    get_admin_promo_banner,
    get_admin_return_request,
    get_admin_store,
    get_admin_user,
    get_admin_vendor,
    list_admin_categories,
    list_admin_markets,
    list_admin_promo_banners,
    list_admin_flash_sale_events,
    list_admin_delivery_ops,
    list_admin_notifications,
    list_admin_orders,
    list_admin_payment_transactions_payload,
    list_admin_platform_ledger_entries_payload,
    list_admin_products,
    list_admin_return_requests,
    list_admin_reviews,
    list_admin_stores,
    list_admin_users,
    list_admin_vendors,
    list_admin_vouchers,
    login_admin_user,
    mark_admin_notification_read,
    moderate_admin_review,
    update_admin_category,
    update_admin_market,
    update_admin_promo_banner,
    update_admin_flash_sale_event,
    update_admin_order_status,
    update_admin_profile,
    update_admin_product,
    update_admin_product_status,
    update_admin_return_request,
    update_admin_store_status,
    update_admin_user_status,
    create_admin_staff,
    list_admin_staff,
    update_admin_user_permission,
    update_admin_vendor_status,
    update_admin_voucher,
    review_admin_voucher,
)
from app.controllers.event_log_controller import (
    admin_event_log_list_dependency,
    get_admin_event_log_stats,
)
from app.core.admin_permissions import (
    require_admin_feature,
    require_audit_access,
    require_super_admin,
)
from app.core.rate_limit import limit_login
from app.schemas.event_log import EventLogPageRead, EventLogStatsRead
from app.controllers.delivery_settings_controller import (
    get_admin_delivery_settings,
    update_admin_delivery_settings,
)
from app.controllers.campaign_controller import (
    archive_admin_campaign,
    create_admin_campaign,
    duplicate_admin_campaign,
    get_admin_campaign,
    list_admin_campaign_opt_ins,
    list_admin_campaigns,
    review_admin_campaign_opt_in,
    update_admin_campaign,
)
from app.controllers.flash_sale_nominations_controller import (
    list_admin_flash_sale_nominations,
    review_admin_flash_sale_nomination,
)
from app.schemas.payment import (
    AdminFinanceOverviewRead,
    AdminPaymentTransactionRead,
    AdminPlatformLedgerEntryRead,
)
from app.controllers.vendor_controller import (
    approve_vendor_application,
    list_vendor_applications,
    reject_vendor_application,
)
from app.controllers.wallet_controller import (
    list_admin_vendor_withdrawal_requests,
    update_admin_vendor_withdrawal_request,
)
from app.core.auth import get_current_user
from app.services.promo_analytics_service import (
    ENTITY_TYPES as PROMO_ENTITY_TYPES,
    build_leaderboard as build_promo_leaderboard,
    build_overview as build_promo_overview,
    build_timeseries as build_promo_timeseries,
)
from app.schemas.promo_analytics import (
    PromoAnalyticsLeaderboardRead,
    PromoAnalyticsOverviewRead,
    PromoAnalyticsTimeseriesRead,
)
from app.core.database import get_db
from app.routes.admin_list_params import AdminListParams
from app.schemas.delivery_settings import (
    AdminDeliverySettingsRead,
    AdminDeliverySettingsUpdate,
)
from app.schemas.pagination import AdminPageRead
from app.models import User

def _validate_promo_entity_type(entity_type: str) -> None:
    if entity_type not in PROMO_ENTITY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"entity_type must be one of: {', '.join(PROMO_ENTITY_TYPES)}.",
        )


RequirePromotionsAdmin = Annotated[User, Depends(require_admin_feature("promotions"))]
RequireUsersAdmin = Annotated[User, Depends(require_admin_feature("users"))]
RequireVendorsAdmin = Annotated[User, Depends(require_admin_feature("vendors"))]
RequireFinanceAdmin = Annotated[User, Depends(require_admin_feature("finance"))]
RequirePayoutsAdmin = Annotated[User, Depends(require_admin_feature("payouts"))]
RequireOrdersAdmin = Annotated[User, Depends(require_admin_feature("orders"))]
RequireDashboardAdmin = Annotated[User, Depends(require_admin_feature("dashboard"))]
RequireReturnsAdmin = Annotated[User, Depends(require_admin_feature("returns"))]
RequireProductsAdmin = Annotated[User, Depends(require_admin_feature("products"))]
RequireStoresAdmin = Annotated[User, Depends(require_admin_feature("stores"))]
RequireMarketsAdmin = Annotated[User, Depends(require_admin_feature("markets"))]
RequireCategoriesAdmin = Annotated[User, Depends(require_admin_feature("categories"))]
RequireReviewsAdmin = Annotated[User, Depends(require_admin_feature("reviews"))]
RequireNotificationsAdmin = Annotated[User, Depends(require_admin_feature("notifications"))]
RequireDeliveryAdmin = Annotated[User, Depends(require_admin_feature("delivery"))]

from app.schemas.admin import (
    AdminBootstrapStatusRead,
    AdminCategoryRead,
    AdminCategoryUpsert,
    AdminDashboardRead,
    AdminMarketRead,
    AdminMarketUpsert,
    AdminNotificationRead,
    AdminPromoBannerRead,
    AdminPromoBannerUpsert,
    AdminFlashSaleEventRead,
    AdminFlashSaleEventUpsert,
    AdminMerchandisingCampaignOptInRead,
    AdminMerchandisingCampaignRead,
    AdminMerchandisingCampaignUpsert,
    AdminDeliveryOpsRead,
    AdminOrderDetailRead,
    AdminOrderRead,
    AdminOrderStatusUpdate,
    AdminProductCreate,
    AdminProductRead,
    AdminProductStatusUpdate,
    AdminReturnRequestRead,
    AdminReturnRequestUpdate,
    AdminReviewModerationUpdate,
    AdminReviewRead,
    AdminStoreDetailRead,
    AdminStoreRead,
    AdminStoreUpsert,
    AdminStoreStatusUpdate,
    AdminUserDetailRead,
    AdminUserRead,
    AdminUserStatusUpdate,
    AdminPermissionUpdate,
    AdminStaffCreate,
    AdminVendorWithdrawalRequestRead,
    AdminVendorWithdrawalUpdate,
    AdminVendorRead,
    AdminVendorStatusUpdate,
    AdminVoucherRead,
    AdminVoucherReview,
    AdminVoucherUpsert,
    AdminVoucherBulkGenerate,
    AdminPromotionAnalyticsRead,
    AdminFlashSaleNominationRead,
    AdminFlashSaleNominationReview,
    NotificationMarkReadResponse,
)
from app.schemas.user import AuthToken, UserCreate, UserLogin, UserRead
from app.schemas.vendor import (
    VendorApplicationListItem,
    VendorApplicationRead,
    VendorApplicationReviewPayload,
)

router = APIRouter(prefix="/admin", tags=["admin"])


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


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return datetime.fromisoformat(cleaned.replace("Z", "+00:00"))


@router.get("/auth/bootstrap-status", response_model=AdminBootstrapStatusRead)
def admin_bootstrap_status(db: Session = Depends(get_db)):
    return get_admin_bootstrap_status(db)


@router.post("/auth/bootstrap-signup", response_model=AuthToken, status_code=status.HTTP_201_CREATED)
def admin_bootstrap_signup(payload: UserCreate, db: Session = Depends(get_db)):
    return bootstrap_first_admin(db, payload)


@router.post("/auth/login", response_model=AuthToken)
async def admin_login(request: Request, db: Session = Depends(get_db)):
    content_type = request.headers.get("content-type", "").lower()

    try:
        if (
            "application/x-www-form-urlencoded" in content_type
            or "multipart/form-data" in content_type
        ):
            form_data = await request.form()
            credentials = UserLogin(
                email=str(form_data.get("username", "")),
                password=str(form_data.get("password", "")),
            )
        else:
            payload = await request.json()
            credentials = UserLogin.model_validate(payload)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc

    limit_login(request, credentials.email)
    return login_admin_user(db, credentials, request=request)


@router.get("/auth/me", response_model=UserRead)
def admin_me(current_user: Annotated[User, Depends(get_current_user)]):
    return get_admin_me(current_user)


@router.patch("/auth/me", response_model=UserRead)
async def patch_admin_me(
    full_name: Annotated[str | None, Form()] = None,
    phone_number: Annotated[str | None, Form()] = None,
    avatar_image: UploadFile | None = File(default=None),
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Session = Depends(get_db),
):
    return await update_admin_profile(
        db,
        current_user,
        full_name=full_name,
        phone_number=phone_number,
        avatar_image=avatar_image,
    )


@router.get("/dashboard", response_model=AdminDashboardRead)
def admin_dashboard(
    current_user: RequireDashboardAdmin,
    db: Session = Depends(get_db),
):
    return get_admin_dashboard(db, current_user)


@router.get("/dashboard/sales-chart")
def admin_sales_chart(
    current_user: RequireDashboardAdmin,
    db: Session = Depends(get_db),
    days: int = Query(default=7, ge=7, le=90),
):
    """Get sales timeseries data for chart visualization."""
    return get_sales_chart_timeseries(db, current_user, days=days)


@router.get("/dashboard/top-vendors")
def admin_top_vendors(
    current_user: RequireDashboardAdmin,
    db: Session = Depends(get_db),
    limit: int = Query(default=10, ge=1, le=50),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get top vendors by GMV (Gross Merchandise Value)."""
    return get_top_vendors(db, current_user, limit=limit, days=days)


@router.get("/dashboard/kpi-metrics")
def admin_kpi_metrics(
    current_user: RequireDashboardAdmin,
    db: Session = Depends(get_db),
):
    """Get key performance indicator metrics."""
    return get_kpi_metrics(db, current_user)


@router.get("/users", response_model=AdminPageRead[AdminUserRead])
def get_users(
    current_user: RequireUsersAdmin,
    list_params: AdminListParams,
    db: Session = Depends(get_db),
):
    limit, offset = list_params
    return list_admin_users(db, current_user, limit=limit, offset=offset)


@router.get("/users/{user_id}", response_model=AdminUserDetailRead)
def get_user(
    user_id: str,
    current_user: RequireUsersAdmin,
    db: Session = Depends(get_db),
):
    return get_admin_user(db, current_user, user_id)


@router.patch("/users/{user_id}/status", response_model=AdminUserRead)
def patch_user_status(
    user_id: str,
    payload: AdminUserStatusUpdate,
    current_user: RequireUsersAdmin,
    db: Session = Depends(get_db),
):
    return update_admin_user_status(db, current_user, user_id, payload)


@router.patch("/users/{user_id}/permission", response_model=AdminUserRead)
def patch_user_permission(
    user_id: str,
    payload: AdminPermissionUpdate,
    current_user: Annotated[User, Depends(require_super_admin)],
    db: Session = Depends(get_db),
):
    return update_admin_user_permission(db, current_user, user_id, payload)


@router.get("/staff", response_model=AdminPageRead[AdminUserRead])
def get_admin_staff(
    current_user: Annotated[User, Depends(require_super_admin)],
    list_params: AdminListParams,
    db: Session = Depends(get_db),
):
    limit, offset = list_params
    return list_admin_staff(db, current_user, limit=limit, offset=offset)


@router.post("/staff", response_model=AdminUserRead, status_code=status.HTTP_201_CREATED)
def post_admin_staff(
    payload: AdminStaffCreate,
    current_user: Annotated[User, Depends(require_super_admin)],
    db: Session = Depends(get_db),
):
    return create_admin_staff(db, current_user, payload)


@router.get("/vendors", response_model=AdminPageRead[AdminVendorRead])
def get_vendors(
    current_user: RequireVendorsAdmin,
    list_params: AdminListParams,
    db: Session = Depends(get_db),
):
    limit, offset = list_params
    return list_admin_vendors(db, current_user, limit=limit, offset=offset)


@router.get("/vendors/{vendor_id}", response_model=AdminVendorRead)
def get_vendor(
    vendor_id: str,
    current_user: RequireVendorsAdmin,
    db: Session = Depends(get_db),
):
    return get_admin_vendor(db, current_user, vendor_id)


@router.patch("/vendors/{vendor_id}/status", response_model=AdminVendorRead)
def patch_vendor_status(
    vendor_id: str,
    payload: AdminVendorStatusUpdate,
    current_user: RequireVendorsAdmin,
    db: Session = Depends(get_db),
):
    return update_admin_vendor_status(db, current_user, vendor_id, payload)


@router.get("/vendor-applications", response_model=AdminPageRead[VendorApplicationListItem])
def get_vendor_applications(
    current_user: RequireVendorsAdmin,
    list_params: AdminListParams,
    db: Session = Depends(get_db),
):
    limit, offset = list_params
    return list_vendor_applications(db, current_user, limit=limit, offset=offset)


@router.patch("/vendor-applications/{application_id}/approve", response_model=VendorApplicationRead)
def approve_application(
    application_id: str,
    current_user: RequireVendorsAdmin,
    db: Session = Depends(get_db),
):
    return approve_vendor_application(db, current_user, application_id)


@router.patch("/vendor-applications/{application_id}/reject", response_model=VendorApplicationRead)
def reject_application(
    application_id: str,
    payload: VendorApplicationReviewPayload,
    current_user: RequireVendorsAdmin,
    db: Session = Depends(get_db),
):
    return reject_vendor_application(
        db,
        current_user,
        application_id,
        payload.rejection_reason,
    )


@router.get("/stores", response_model=AdminPageRead[AdminStoreRead])
def get_stores(
    current_user: RequireStoresAdmin,
    list_params: AdminListParams,
    db: Session = Depends(get_db),
):
    limit, offset = list_params
    return list_admin_stores(db, current_user, limit=limit, offset=offset)


@router.get("/stores/{store_id}", response_model=AdminStoreDetailRead)
def get_store(
    store_id: str,
    current_user: RequireStoresAdmin,
    db: Session = Depends(get_db),
):
    return get_admin_store(db, current_user, store_id)


@router.patch("/stores/{store_id}/status", response_model=AdminStoreRead)
def patch_store_status(
    store_id: str,
    payload: AdminStoreStatusUpdate,
    current_user: RequireStoresAdmin,
    db: Session = Depends(get_db),
):
    return update_admin_store_status(db, current_user, store_id, payload)


@router.post("/stores", response_model=AdminStoreRead, status_code=status.HTTP_201_CREATED)
async def post_store(
    name: Annotated[str, Form(min_length=2, max_length=160)],
    category: Annotated[str, Form(min_length=2, max_length=120)],
    region: Annotated[str, Form(min_length=2, max_length=120)],
    city: Annotated[str, Form(min_length=2, max_length=120)],
    current_user: RequireStoresAdmin,
    db: Session = Depends(get_db),
    description: Annotated[str | None, Form(max_length=255)] = None,
    slug: Annotated[str | None, Form(max_length=80)] = None,
    market_id: Annotated[str | None, Form(max_length=50)] = None,
    location: Annotated[str | None, Form(max_length=255)] = None,
    audience_slugs: Annotated[str | None, Form(max_length=255)] = None,
    status_value: Annotated[str, Form(alias="status", min_length=1, max_length=30)] = "active",
    logo_image: UploadFile | None = File(default=None),
    banner_image: UploadFile | None = File(default=None),
):
    payload = AdminStoreUpsert(
        name=name,
        slug=slug,
        description=description or "",
        category=category,
        audience_slugs=_split_csv_values(audience_slugs),
        market_id=market_id,
        location=location,
        region=region,
        city=city,
        status=status_value,
    )
    return await create_admin_store(db, current_user, payload, logo_image, banner_image)


@router.get("/markets", response_model=AdminPageRead[AdminMarketRead])
def get_markets(
    current_user: RequireMarketsAdmin,
    list_params: AdminListParams,
    db: Session = Depends(get_db),
):
    limit, offset = list_params
    return list_admin_markets(db, current_user, limit=limit, offset=offset)


@router.post("/markets", response_model=AdminMarketRead, status_code=status.HTTP_201_CREATED)
async def post_market(
    name: Annotated[str, Form(min_length=1, max_length=120)],
    current_user: RequireMarketsAdmin,
    db: Session = Depends(get_db),
    slug: Annotated[str | None, Form(max_length=50)] = None,
    image_key: Annotated[str | None, Form(alias="image", max_length=200)] = None,
    status_value: Annotated[str, Form(alias="status", min_length=1, max_length=30)] = "active",
    image_file: UploadFile | None = File(default=None),
):
    payload = AdminMarketUpsert(
        name=name,
        slug=slug,
        image=image_key,
        status=status_value,
    )
    return await create_admin_market(db, current_user, payload, image_file)


@router.patch("/markets/{market_id}", response_model=AdminMarketRead)
async def patch_market(
    market_id: str,
    name: Annotated[str, Form(min_length=1, max_length=120)],
    current_user: RequireMarketsAdmin,
    db: Session = Depends(get_db),
    slug: Annotated[str | None, Form(max_length=50)] = None,
    image_key: Annotated[str | None, Form(alias="image", max_length=200)] = None,
    status_value: Annotated[str, Form(alias="status", min_length=1, max_length=30)] = "active",
    image_file: UploadFile | None = File(default=None),
):
    payload = AdminMarketUpsert(
        name=name,
        slug=slug,
        image=image_key,
        status=status_value,
    )
    return await update_admin_market(db, current_user, market_id, payload, image_file)


@router.delete("/markets/{market_id}")
def remove_market(
    market_id: str,
    current_user: RequireMarketsAdmin,
    db: Session = Depends(get_db),
):
    delete_admin_market(db, current_user, market_id)
    return {"success": True}


@router.get("/promo-banners", response_model=AdminPageRead[AdminPromoBannerRead])
def get_promo_banners(
    current_user: RequirePromotionsAdmin,
    list_params: AdminListParams,
    db: Session = Depends(get_db),
):
    limit, offset = list_params
    return list_admin_promo_banners(db, current_user, limit=limit, offset=offset)


@router.get("/promo-banners/{banner_id}", response_model=AdminPromoBannerRead)
def get_promo_banner(
    banner_id: str,
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
):
    return get_admin_promo_banner(db, current_user, banner_id)


@router.post("/promo-banners", response_model=AdminPromoBannerRead, status_code=status.HTTP_201_CREATED)
async def post_promo_banner(
    title: Annotated[str, Form(min_length=2, max_length=120)],
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
    subtitle: Annotated[str | None, Form(max_length=255)] = None,
    cta_label: Annotated[str, Form(min_length=2, max_length=80)] = "Shop now",
    cta_link: Annotated[str | None, Form(max_length=500)] = None,
    accent: Annotated[str | None, Form(max_length=20)] = None,
    sort_order: Annotated[int | None, Form()] = None,
    status_value: Annotated[str, Form(alias="status", min_length=1, max_length=30)] = "active",
    link_type: Annotated[str, Form(min_length=1, max_length=30)] = "deals",
    campaign_tag: Annotated[str | None, Form(max_length=50)] = None,
    placement: Annotated[str, Form(min_length=1, max_length=30)] = "home",
    starts_at: Annotated[str | None, Form()] = None,
    ends_at: Annotated[str | None, Form()] = None,
    image_file: UploadFile | None = File(default=None),
):
    payload = AdminPromoBannerUpsert(
        title=title,
        subtitle=subtitle,
        cta_label=cta_label,
        cta_link=cta_link,
        accent=accent,
        sort_order=sort_order,
        status=status_value,
        link_type=link_type,
        campaign_tag=campaign_tag,
        placement=placement,
        starts_at=_parse_optional_datetime(starts_at),
        ends_at=_parse_optional_datetime(ends_at),
    )
    return await create_admin_promo_banner(db, current_user, payload, image_file)


@router.patch("/promo-banners/{banner_id}", response_model=AdminPromoBannerRead)
async def patch_promo_banner(
    banner_id: str,
    title: Annotated[str, Form(min_length=2, max_length=120)],
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
    subtitle: Annotated[str | None, Form(max_length=255)] = None,
    cta_label: Annotated[str, Form(min_length=2, max_length=80)] = "Shop now",
    cta_link: Annotated[str | None, Form(max_length=500)] = None,
    accent: Annotated[str | None, Form(max_length=20)] = None,
    sort_order: Annotated[int | None, Form()] = None,
    status_value: Annotated[str, Form(alias="status", min_length=1, max_length=30)] = "active",
    link_type: Annotated[str, Form(min_length=1, max_length=30)] = "deals",
    campaign_tag: Annotated[str | None, Form(max_length=50)] = None,
    placement: Annotated[str, Form(min_length=1, max_length=30)] = "home",
    starts_at: Annotated[str | None, Form()] = None,
    ends_at: Annotated[str | None, Form()] = None,
    image_file: UploadFile | None = File(default=None),
):
    payload = AdminPromoBannerUpsert(
        title=title,
        subtitle=subtitle,
        cta_label=cta_label,
        cta_link=cta_link,
        accent=accent,
        sort_order=sort_order,
        status=status_value,
        link_type=link_type,
        campaign_tag=campaign_tag,
        placement=placement,
        starts_at=_parse_optional_datetime(starts_at),
        ends_at=_parse_optional_datetime(ends_at),
    )
    return await update_admin_promo_banner(db, current_user, banner_id, payload, image_file)


@router.delete("/promo-banners/{banner_id}")
def remove_promo_banner(
    banner_id: str,
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
):
    archive_admin_promo_banner(db, current_user, banner_id)
    return {"success": True}


@router.get("/flash-sale-events", response_model=AdminPageRead[AdminFlashSaleEventRead])
def get_flash_sale_events(
    current_user: RequirePromotionsAdmin,
    list_params: AdminListParams,
    db: Session = Depends(get_db),
):
    limit, offset = list_params
    return list_admin_flash_sale_events(db, current_user, limit=limit, offset=offset)


@router.post("/flash-sale-events", response_model=AdminFlashSaleEventRead, status_code=status.HTTP_201_CREATED)
def post_flash_sale_event(
    payload: AdminFlashSaleEventUpsert,
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
):
    return create_admin_flash_sale_event(db, current_user, payload)


@router.patch("/flash-sale-events/{event_id}", response_model=AdminFlashSaleEventRead)
def patch_flash_sale_event(
    event_id: str,
    payload: AdminFlashSaleEventUpsert,
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
):
    return update_admin_flash_sale_event(db, current_user, event_id, payload)


@router.delete("/flash-sale-events/{event_id}")
def remove_flash_sale_event(
    event_id: str,
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
):
    archive_admin_flash_sale_event(db, current_user, event_id)
    return {"success": True}


@router.get(
    "/merchandising-campaigns",
    response_model=AdminPageRead[AdminMerchandisingCampaignRead],
)
def get_merchandising_campaigns(
    current_user: RequirePromotionsAdmin,
    list_params: AdminListParams,
    db: Session = Depends(get_db),
    search: str | None = Query(default=None, max_length=120),
    status_filter: str | None = Query(default=None, alias="status", max_length=30),
):
    limit, offset = list_params
    return list_admin_campaigns(
        db,
        current_user,
        limit=limit,
        offset=offset,
        search=search,
        status_filter=status_filter,
    )


@router.get(
    "/merchandising-campaigns/{campaign_id}",
    response_model=AdminMerchandisingCampaignRead,
)
def get_merchandising_campaign(
    campaign_id: UUID,
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
):
    return get_admin_campaign(db, current_user, campaign_id)


@router.post(
    "/merchandising-campaigns",
    response_model=AdminMerchandisingCampaignRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_merchandising_campaign(
    payload: AdminMerchandisingCampaignUpsert,
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
):
    return await create_admin_campaign(db, current_user, payload)


@router.patch(
    "/merchandising-campaigns/{campaign_id}",
    response_model=AdminMerchandisingCampaignRead,
)
async def patch_merchandising_campaign(
    campaign_id: UUID,
    payload: AdminMerchandisingCampaignUpsert,
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
):
    return await update_admin_campaign(db, current_user, campaign_id, payload)


@router.delete("/merchandising-campaigns/{campaign_id}")
def remove_merchandising_campaign(
    campaign_id: UUID,
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
):
    archive_admin_campaign(db, current_user, campaign_id)
    return {"success": True}


@router.post(
    "/merchandising-campaigns/{campaign_id}/duplicate",
    response_model=AdminMerchandisingCampaignRead,
    status_code=status.HTTP_201_CREATED,
)
def post_duplicate_merchandising_campaign(
    campaign_id: UUID,
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
):
    return duplicate_admin_campaign(db, current_user, campaign_id)


@router.get(
    "/merchandising-campaign-opt-ins",
    response_model=AdminPageRead[AdminMerchandisingCampaignOptInRead],
)
def get_merchandising_campaign_opt_ins(
    current_user: RequirePromotionsAdmin,
    list_params: AdminListParams,
    db: Session = Depends(get_db),
    campaign_id: UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status", max_length=30),
):
    limit, offset = list_params
    return list_admin_campaign_opt_ins(
        db,
        current_user,
        campaign_id=campaign_id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/merchandising-campaign-opt-ins/{opt_in_id}/review",
    response_model=AdminMerchandisingCampaignOptInRead,
)
def post_review_merchandising_campaign_opt_in(
    opt_in_id: UUID,
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
    status_value: str = Query(alias="status", min_length=1, max_length=30),
    review_notes: str | None = Query(default=None, max_length=255),
):
    return review_admin_campaign_opt_in(
        db,
        current_user,
        opt_in_id,
        status_value=status_value,
        review_notes=review_notes,
    )


@router.get("/categories", response_model=AdminPageRead[AdminCategoryRead])
def get_categories(
    current_user: RequireCategoriesAdmin,
    list_params: AdminListParams,
    db: Session = Depends(get_db),
):
    limit, offset = list_params
    return list_admin_categories(db, current_user, limit=limit, offset=offset)


@router.post("/categories", response_model=AdminCategoryRead, status_code=status.HTTP_201_CREATED)
async def post_category(
    name: Annotated[str, Form(min_length=1, max_length=120)],
    current_user: RequireCategoriesAdmin,
    db: Session = Depends(get_db),
    slug: Annotated[str | None, Form(max_length=50)] = None,
    description: Annotated[str | None, Form(max_length=160)] = None,
    image_key: Annotated[str | None, Form(alias="image", max_length=200)] = None,
    subcategories: Annotated[str | None, Form(max_length=4000)] = None,
    status_value: Annotated[str, Form(alias="status", min_length=1, max_length=30)] = "active",
    image_file: UploadFile | None = File(default=None),
):
    payload = AdminCategoryUpsert(
        name=name,
        slug=slug,
        description=description or "",
        image=image_key,
        subcategories=_split_multiline_values(subcategories),
        status=status_value,
    )
    return await create_admin_category(db, current_user, payload, image_file)


@router.patch("/categories/{category_id}", response_model=AdminCategoryRead)
async def patch_category(
    category_id: str,
    current_user: RequireCategoriesAdmin,
    db: Session = Depends(get_db),
    name: Annotated[str, Form(min_length=1, max_length=120)] = "",
    slug: Annotated[str | None, Form(max_length=50)] = None,
    description: Annotated[str | None, Form(max_length=160)] = None,
    image_key: Annotated[str | None, Form(alias="image", max_length=200)] = None,
    subcategories: Annotated[str | None, Form(max_length=4000)] = None,
    status_value: Annotated[str, Form(alias="status", min_length=1, max_length=30)] = "active",
    image_file: UploadFile | None = File(default=None),
):
    payload = AdminCategoryUpsert(
        name=name,
        slug=slug,
        description=description or "",
        image=image_key,
        subcategories=_split_multiline_values(subcategories),
        status=status_value,
    )
    return await update_admin_category(db, current_user, category_id, payload, image_file)


@router.delete("/categories/{category_id}")
def remove_category(
    category_id: str,
    current_user: RequireCategoriesAdmin,
    db: Session = Depends(get_db),
    permanent: bool = False,
):
    delete_admin_category(db, current_user, category_id, permanent=permanent)
    return {"success": True}


@router.get("/products", response_model=AdminPageRead[AdminProductRead])
def get_products(
    current_user: RequireProductsAdmin,
    list_params: AdminListParams,
    db: Session = Depends(get_db),
):
    limit, offset = list_params
    return list_admin_products(db, current_user, limit=limit, offset=offset)


@router.post("/products", response_model=AdminProductRead, status_code=status.HTTP_201_CREATED)
async def post_product(
    name: Annotated[str, Form(min_length=2, max_length=255)],
    description: Annotated[str, Form(min_length=12, max_length=1000)],
    category: Annotated[str, Form(min_length=2, max_length=120)],
    price: Annotated[int, Form(ge=0)],
    stock: Annotated[int, Form(ge=0)],
    current_user: RequireProductsAdmin,
    db: Session = Depends(get_db),
    store_id: Annotated[str | None, Form(max_length=50)] = None,
    audience_slug: Annotated[str | None, Form(max_length=50)] = None,
    section: Annotated[str | None, Form(max_length=50)] = None,
    subcategory: Annotated[str | None, Form(max_length=120)] = None,
    category_slugs: Annotated[str | None, Form(max_length=2000)] = None,
    subcategory_slugs: Annotated[str | None, Form(max_length=4000)] = None,
    old_price: Annotated[int | None, Form(ge=0)] = None,
    rating: Annotated[float | None, Form(ge=0, le=5)] = None,
    reviews: Annotated[str | None, Form(max_length=50)] = None,
    placement_tags: Annotated[str | None, Form(max_length=255)] = None,
    color_options: Annotated[str | None, Form(max_length=255)] = None,
    size_options: Annotated[str | None, Form(max_length=255)] = None,
    specifications: Annotated[str | None, Form(max_length=3000)] = None,
    status_value: Annotated[str, Form(alias="status", min_length=1, max_length=30)] = "active",
    image_key: Annotated[str | None, Form(max_length=100)] = None,
    images: list[UploadFile] | None = File(default=None),
):
    payload = AdminProductCreate(
        name=name,
        description=description,
        category=category,
        subcategory=subcategory,
        category_slugs=_split_csv_values(category_slugs),
        subcategory_slugs=_split_csv_values(subcategory_slugs),
        store_id=store_id,
        audience_slug=audience_slug,
        section=section,
        price=price,
        old_price=old_price,
        stock=stock,
        rating=rating,
        reviews=reviews,
        placement_tags=_split_csv_values(placement_tags),
        color_options=_split_csv_values(color_options),
        size_options=_split_csv_values(size_options),
        specifications=_split_multiline_values(specifications),
        status=status_value,
        image_key=image_key,
    )
    return await create_admin_product(db, current_user, payload, images)


@router.get("/products/{product_id}", response_model=AdminProductRead)
def get_product(
    product_id: str,
    current_user: RequireProductsAdmin,
    db: Session = Depends(get_db),
):
    return get_admin_product(db, current_user, product_id)


@router.patch("/products/{product_id}", response_model=AdminProductRead)
async def patch_product(
    product_id: str,
    name: Annotated[str, Form(min_length=2, max_length=255)],
    description: Annotated[str, Form(min_length=12, max_length=1000)],
    category: Annotated[str, Form(min_length=2, max_length=120)],
    price: Annotated[int, Form(ge=0)],
    stock: Annotated[int, Form(ge=0)],
    current_user: RequireProductsAdmin,
    db: Session = Depends(get_db),
    store_id: Annotated[str | None, Form(max_length=50)] = None,
    audience_slug: Annotated[str | None, Form(max_length=50)] = None,
    section: Annotated[str | None, Form(max_length=50)] = None,
    subcategory: Annotated[str | None, Form(max_length=120)] = None,
    category_slugs: Annotated[str | None, Form(max_length=2000)] = None,
    subcategory_slugs: Annotated[str | None, Form(max_length=4000)] = None,
    old_price: Annotated[int | None, Form(ge=0)] = None,
    rating: Annotated[float | None, Form(ge=0, le=5)] = None,
    reviews: Annotated[str | None, Form(max_length=50)] = None,
    placement_tags: Annotated[str | None, Form(max_length=255)] = None,
    color_options: Annotated[str | None, Form(max_length=255)] = None,
    size_options: Annotated[str | None, Form(max_length=255)] = None,
    specifications: Annotated[str | None, Form(max_length=3000)] = None,
    status_value: Annotated[str, Form(alias="status", min_length=1, max_length=30)] = "active",
    image_key: Annotated[str | None, Form(max_length=100)] = None,
    images: list[UploadFile] | None = File(default=None),
):
    payload = AdminProductCreate(
        name=name,
        description=description,
        category=category,
        subcategory=subcategory,
        category_slugs=_split_csv_values(category_slugs),
        subcategory_slugs=_split_csv_values(subcategory_slugs),
        store_id=store_id,
        audience_slug=audience_slug,
        section=section,
        price=price,
        old_price=old_price,
        stock=stock,
        rating=rating,
        reviews=reviews,
        placement_tags=_split_csv_values(placement_tags),
        color_options=_split_csv_values(color_options),
        size_options=_split_csv_values(size_options),
        specifications=_split_multiline_values(specifications),
        status=status_value,
        image_key=image_key,
    )
    return await update_admin_product(db, current_user, product_id, payload, images)


@router.patch("/products/{product_id}/status", response_model=AdminProductRead)
def patch_product_status(
    product_id: str,
    payload: AdminProductStatusUpdate,
    current_user: RequireProductsAdmin,
    db: Session = Depends(get_db),
):
    return update_admin_product_status(db, current_user, product_id, payload)


@router.get("/vouchers", response_model=AdminPageRead[AdminVoucherRead])
def get_vouchers(
    current_user: RequirePromotionsAdmin,
    list_params: AdminListParams,
    db: Session = Depends(get_db),
    q: str | None = Query(default=None, max_length=120),
    status: str | None = Query(default=None, max_length=40),
    owner_type: str | None = Query(default=None, max_length=20),
    scope: str | None = Query(default=None, max_length=20),
):
    limit, offset = list_params
    return list_admin_vouchers(
        db,
        current_user,
        limit=limit,
        offset=offset,
        q=q,
        status_filter=status,
        owner_type=owner_type,
        scope=scope,
    )


@router.post("/vouchers", response_model=AdminVoucherRead, status_code=status.HTTP_201_CREATED)
def post_voucher(
    payload: AdminVoucherUpsert,
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
):
    return create_admin_voucher(db, current_user, payload)


@router.patch("/vouchers/{voucher_id}", response_model=AdminVoucherRead)
def patch_voucher(
    voucher_id: str,
    payload: AdminVoucherUpsert,
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
):
    return update_admin_voucher(db, current_user, voucher_id, payload)


@router.delete("/vouchers/{voucher_id}")
def remove_voucher(
    voucher_id: str,
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
):
    archive_admin_voucher(db, current_user, voucher_id)
    return {"success": True}


@router.post("/vouchers/{voucher_id}/pause", response_model=AdminVoucherRead)
def post_pause_voucher(
    voucher_id: str,
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
):
    return pause_admin_voucher(db, current_user, voucher_id)


@router.post("/vouchers/{voucher_id}/resume", response_model=AdminVoucherRead)
def post_resume_voucher(
    voucher_id: str,
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
):
    return resume_admin_voucher(db, current_user, voucher_id)


@router.post("/vouchers/{voucher_id}/duplicate", response_model=AdminVoucherRead, status_code=status.HTTP_201_CREATED)
def post_duplicate_voucher(
    voucher_id: str,
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
):
    return duplicate_admin_voucher(db, current_user, voucher_id)


@router.post("/vouchers/{voucher_id}/review", response_model=AdminVoucherRead)
def post_voucher_review(
    voucher_id: str,
    payload: AdminVoucherReview,
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
):
    return review_admin_voucher(db, current_user, voucher_id, payload)


@router.get("/vouchers/analytics", response_model=AdminPromotionAnalyticsRead)
def get_voucher_analytics(
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
):
    return get_admin_promotion_analytics(db, current_user)


# --- Promo performance -------------------------------------------------------
# /vouchers/analytics above answers "what have vouchers cost us in discount?".
# These three answer "are our promotions actually working?" across all three
# promo surfaces — merchandising campaigns, vouchers and home banners — from the
# impression/click/conversion events the apps report to /api/promotions/events/batch.
# Same permission band as the rest of promotions.


@router.get("/promo-analytics/overview", response_model=PromoAnalyticsOverviewRead)
def get_promo_analytics_overview(
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=1, le=365, description="Days to look back"),
):
    """Marketplace-wide promo funnel across campaigns, vouchers and banners."""
    return build_promo_overview(db, days=days)


@router.get("/promo-analytics/timeseries", response_model=PromoAnalyticsTimeseriesRead)
def get_promo_analytics_timeseries(
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
    entity_type: str = Query("campaign", description="campaign|voucher|banner"),
    entity_id: str | None = Query(None, description="Limit to one campaign/voucher/banner"),
    days: int = Query(30, ge=1, le=365),
):
    """Daily impressions/clicks/conversions, with quiet days included as zeroes."""
    _validate_promo_entity_type(entity_type)
    return build_promo_timeseries(
        db, entity_type=entity_type, days=days, entity_id=entity_id
    )


@router.get("/promo-analytics/leaderboard", response_model=PromoAnalyticsLeaderboardRead)
def get_promo_analytics_leaderboard(
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
    entity_type: str = Query("campaign", description="campaign|voucher|banner"),
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(10, ge=1, le=50),
):
    """Best-performing promotions of the chosen type."""
    _validate_promo_entity_type(entity_type)
    return build_promo_leaderboard(db, entity_type=entity_type, days=days, limit=limit)


@router.post("/vouchers/bulk-generate", response_model=list[AdminVoucherRead], status_code=status.HTTP_201_CREATED)
def post_bulk_vouchers(
    payload: AdminVoucherBulkGenerate,
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
):
    return bulk_generate_admin_vouchers(db, current_user, payload)


@router.get("/flash-sale-nominations", response_model=AdminPageRead[AdminFlashSaleNominationRead])
def get_flash_sale_nominations(
    current_user: RequirePromotionsAdmin,
    list_params: AdminListParams,
    db: Session = Depends(get_db),
):
    limit, offset = list_params
    return list_admin_flash_sale_nominations(db, current_user, limit=limit, offset=offset)


@router.post(
    "/flash-sale-nominations/{nomination_id}/review",
    response_model=AdminFlashSaleNominationRead,
)
def post_flash_sale_nomination_review(
    nomination_id: str,
    payload: AdminFlashSaleNominationReview,
    current_user: RequirePromotionsAdmin,
    db: Session = Depends(get_db),
):
    return review_admin_flash_sale_nomination(db, current_user, nomination_id, payload)


@router.get("/reviews", response_model=AdminPageRead[AdminReviewRead])
def get_reviews(
    current_user: RequireReviewsAdmin,
    list_params: AdminListParams,
    db: Session = Depends(get_db),
):
    limit, offset = list_params
    return list_admin_reviews(db, current_user, limit=limit, offset=offset)


@router.patch("/reviews/{review_id}/moderation", response_model=AdminReviewRead)
def patch_review_moderation(
    review_id: str,
    payload: AdminReviewModerationUpdate,
    current_user: RequireReviewsAdmin,
    db: Session = Depends(get_db),
):
    return moderate_admin_review(db, current_user, review_id, payload)


@router.get("/orders", response_model=AdminPageRead[AdminOrderRead])
def get_orders(
    current_user: RequireOrdersAdmin,
    list_params: AdminListParams,
    db: Session = Depends(get_db),
):
    limit, offset = list_params
    return list_admin_orders(db, current_user, limit=limit, offset=offset)


@router.get("/finance/overview", response_model=AdminFinanceOverviewRead)
def get_finance_overview(
    current_user: RequireFinanceAdmin,
    db: Session = Depends(get_db),
):
    return get_admin_finance_overview_payload(db, current_user)


@router.get("/finance/payments", response_model=AdminPageRead[AdminPaymentTransactionRead])
def get_finance_payments(
    current_user: RequireFinanceAdmin,
    list_params: AdminListParams,
    db: Session = Depends(get_db),
):
    limit, offset = list_params
    return list_admin_payment_transactions_payload(db, current_user, limit=limit, offset=offset)


@router.get("/finance/ledger", response_model=AdminPageRead[AdminPlatformLedgerEntryRead])
def get_finance_ledger(
    current_user: RequireFinanceAdmin,
    list_params: AdminListParams,
    db: Session = Depends(get_db),
):
    limit, offset = list_params
    return list_admin_platform_ledger_entries_payload(db, current_user, limit=limit, offset=offset)


@router.get("/orders/delivery-ops", response_model=AdminDeliveryOpsRead)
def get_delivery_ops(
    current_user: RequireOrdersAdmin,
    db: Session = Depends(get_db),
):
    return list_admin_delivery_ops(db, current_user)


@router.get("/orders/{order_id}", response_model=AdminOrderDetailRead)
def get_order(
    order_id: str,
    current_user: RequireOrdersAdmin,
    db: Session = Depends(get_db),
):
    return get_admin_order(db, current_user, order_id)


@router.patch("/orders/{order_id}/status", response_model=AdminOrderRead)
def patch_order_status(
    order_id: str,
    payload: AdminOrderStatusUpdate,
    current_user: RequireOrdersAdmin,
    db: Session = Depends(get_db),
):
    return update_admin_order_status(db, current_user, order_id, payload)


@router.get("/returns", response_model=AdminPageRead[AdminReturnRequestRead])
def get_return_requests(
    current_user: RequireReturnsAdmin,
    list_params: AdminListParams,
    db: Session = Depends(get_db),
):
    limit, offset = list_params
    return list_admin_return_requests(db, current_user, limit=limit, offset=offset)


@router.get("/returns/{request_id}", response_model=AdminReturnRequestRead)
def get_return_request(
    request_id: str,
    current_user: RequireReturnsAdmin,
    db: Session = Depends(get_db),
):
    return get_admin_return_request(db, current_user, request_id)


@router.patch("/returns/{request_id}", response_model=AdminReturnRequestRead)
def patch_return_request(
    request_id: str,
    payload: AdminReturnRequestUpdate,
    current_user: RequireReturnsAdmin,
    db: Session = Depends(get_db),
):
    return update_admin_return_request(db, current_user, request_id, payload)


@router.get(
    "/payouts/withdrawals",
    response_model=AdminPageRead[AdminVendorWithdrawalRequestRead],
)
def get_vendor_withdrawal_requests(
    current_user: RequirePayoutsAdmin,
    list_params: AdminListParams,
    db: Session = Depends(get_db),
):
    limit, offset = list_params
    return list_admin_vendor_withdrawal_requests(db, current_user, limit=limit, offset=offset)


@router.patch(
    "/payouts/withdrawals/{request_id}",
    response_model=AdminVendorWithdrawalRequestRead,
)
def patch_vendor_withdrawal_request(
    request_id: str,
    payload: AdminVendorWithdrawalUpdate,
    current_user: RequirePayoutsAdmin,
    db: Session = Depends(get_db),
):
    return update_admin_vendor_withdrawal_request(
        db,
        current_user,
        request_id,
        payload,
    )


@router.get("/notifications", response_model=AdminPageRead[AdminNotificationRead])
def get_notifications(
    current_user: RequireNotificationsAdmin,
    list_params: AdminListParams,
    db: Session = Depends(get_db),
):
    limit, offset = list_params
    return list_admin_notifications(db, current_user, limit=limit, offset=offset)


@router.patch(
    "/notifications/{notification_id}/read",
    response_model=NotificationMarkReadResponse,
)
def patch_notification_read(
    notification_id: str,
    current_user: RequireNotificationsAdmin,
    db: Session = Depends(get_db),
):
    return mark_admin_notification_read(db, current_user, notification_id)


@router.get("/delivery-settings", response_model=AdminDeliverySettingsRead)
def admin_get_delivery_settings(
    current_user: RequireDeliveryAdmin,
    db: Session = Depends(get_db),
):
    return get_admin_delivery_settings(db, current_user)


@router.patch("/delivery-settings", response_model=AdminDeliverySettingsRead)
def admin_update_delivery_settings(
    payload: AdminDeliverySettingsUpdate,
    current_user: RequireDeliveryAdmin,
    db: Session = Depends(get_db),
):
    return update_admin_delivery_settings(db, current_user, payload)


@router.get("/event-logs", response_model=EventLogPageRead)
def admin_event_logs(
    page: Annotated[EventLogPageRead, Depends(admin_event_log_list_dependency)],
):
    return page


@router.get("/event-logs/stats", response_model=EventLogStatsRead)
def admin_event_log_stats(
    current_user: Annotated[User, Depends(require_audit_access)],
    db: Session = Depends(get_db),
):
    return get_admin_event_log_stats(db, current_user)

