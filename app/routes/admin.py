from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.controllers.admin_controller import (
    bootstrap_first_admin,
    create_admin_category,
    create_admin_product,
    create_admin_market,
    create_admin_store,
    delete_admin_category,
    delete_admin_market,
    get_admin_bootstrap_status,
    get_admin_dashboard,
    get_admin_me,
    get_admin_order,
    get_admin_product,
    get_admin_store,
    get_admin_user,
    get_admin_vendor,
    list_admin_categories,
    list_admin_markets,
    list_admin_notifications,
    list_admin_orders,
    list_admin_products,
    list_admin_stores,
    list_admin_users,
    list_admin_vendors,
    login_admin_user,
    mark_admin_notification_read,
    update_admin_category,
    update_admin_market,
    update_admin_order_status,
    update_admin_profile,
    update_admin_product_status,
    update_admin_store_status,
    update_admin_user_status,
    update_admin_vendor_status,
)
from app.controllers.vendor_controller import (
    approve_vendor_application,
    list_vendor_applications,
    reject_vendor_application,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.admin import (
    AdminBootstrapStatusRead,
    AdminCategoryRead,
    AdminCategoryUpsert,
    AdminDashboardRead,
    AdminMarketRead,
    AdminMarketUpsert,
    AdminNotificationRead,
    AdminOrderRead,
    AdminOrderStatusUpdate,
    AdminProductCreate,
    AdminProductRead,
    AdminProductStatusUpdate,
    AdminStoreRead,
    AdminStoreUpsert,
    AdminStoreStatusUpdate,
    AdminUserRead,
    AdminUserStatusUpdate,
    AdminVendorRead,
    AdminVendorStatusUpdate,
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

    return login_admin_user(db, credentials)


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
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return get_admin_dashboard(db, current_user)


@router.get("/users", response_model=list[AdminUserRead])
def get_users(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_admin_users(db, current_user)


@router.get("/users/{user_id}", response_model=AdminUserRead)
def get_user(
    user_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return get_admin_user(db, current_user, user_id)


@router.patch("/users/{user_id}/status", response_model=AdminUserRead)
def patch_user_status(
    user_id: str,
    payload: AdminUserStatusUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return update_admin_user_status(db, current_user, user_id, payload)


@router.get("/vendors", response_model=list[AdminVendorRead])
def get_vendors(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_admin_vendors(db, current_user)


@router.get("/vendors/{vendor_id}", response_model=AdminVendorRead)
def get_vendor(
    vendor_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return get_admin_vendor(db, current_user, vendor_id)


@router.patch("/vendors/{vendor_id}/status", response_model=AdminVendorRead)
def patch_vendor_status(
    vendor_id: str,
    payload: AdminVendorStatusUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return update_admin_vendor_status(db, current_user, vendor_id, payload)


@router.get("/vendor-applications", response_model=list[VendorApplicationListItem])
def get_vendor_applications(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_vendor_applications(db, current_user)


@router.patch("/vendor-applications/{application_id}/approve", response_model=VendorApplicationRead)
def approve_application(
    application_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return approve_vendor_application(db, current_user, application_id)


@router.patch("/vendor-applications/{application_id}/reject", response_model=VendorApplicationRead)
def reject_application(
    application_id: str,
    payload: VendorApplicationReviewPayload,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return reject_vendor_application(
        db,
        current_user,
        application_id,
        payload.rejection_reason,
    )


@router.get("/stores", response_model=list[AdminStoreRead])
def get_stores(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_admin_stores(db, current_user)


@router.get("/stores/{store_id}", response_model=AdminStoreRead)
def get_store(
    store_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return get_admin_store(db, current_user, store_id)


@router.patch("/stores/{store_id}/status", response_model=AdminStoreRead)
def patch_store_status(
    store_id: str,
    payload: AdminStoreStatusUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return update_admin_store_status(db, current_user, store_id, payload)


@router.post("/stores", response_model=AdminStoreRead, status_code=status.HTTP_201_CREATED)
async def post_store(
    name: Annotated[str, Form(min_length=2, max_length=160)],
    category: Annotated[str, Form(min_length=2, max_length=120)],
    region: Annotated[str, Form(min_length=2, max_length=120)],
    city: Annotated[str, Form(min_length=2, max_length=120)],
    current_user: Annotated[User, Depends(get_current_user)],
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


@router.get("/markets", response_model=list[AdminMarketRead])
def get_markets(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_admin_markets(db, current_user)


@router.post("/markets", response_model=AdminMarketRead, status_code=status.HTTP_201_CREATED)
async def post_market(
    name: Annotated[str, Form(min_length=1, max_length=120)],
    current_user: Annotated[User, Depends(get_current_user)],
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
    current_user: Annotated[User, Depends(get_current_user)],
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
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    delete_admin_market(db, current_user, market_id)
    return {"success": True}


@router.get("/categories", response_model=list[AdminCategoryRead])
def get_categories(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_admin_categories(db, current_user)


@router.post("/categories", response_model=AdminCategoryRead, status_code=status.HTTP_201_CREATED)
async def post_category(
    name: Annotated[str, Form(min_length=1, max_length=120)],
    current_user: Annotated[User, Depends(get_current_user)],
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
    current_user: Annotated[User, Depends(get_current_user)],
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
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    delete_admin_category(db, current_user, category_id)
    return {"success": True}


@router.get("/products", response_model=list[AdminProductRead])
def get_products(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_admin_products(db, current_user)


@router.post("/products", response_model=AdminProductRead, status_code=status.HTTP_201_CREATED)
async def post_product(
    name: Annotated[str, Form(min_length=2, max_length=255)],
    description: Annotated[str, Form(min_length=12, max_length=1000)],
    category: Annotated[str, Form(min_length=2, max_length=120)],
    price: Annotated[int, Form(ge=0)],
    stock: Annotated[int, Form(ge=0)],
    current_user: Annotated[User, Depends(get_current_user)],
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
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return get_admin_product(db, current_user, product_id)


@router.patch("/products/{product_id}/status", response_model=AdminProductRead)
def patch_product_status(
    product_id: str,
    payload: AdminProductStatusUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return update_admin_product_status(db, current_user, product_id, payload)


@router.get("/orders", response_model=list[AdminOrderRead])
def get_orders(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_admin_orders(db, current_user)


@router.get("/orders/{order_id}", response_model=AdminOrderRead)
def get_order(
    order_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return get_admin_order(db, current_user, order_id)


@router.patch("/orders/{order_id}/status", response_model=AdminOrderRead)
def patch_order_status(
    order_id: str,
    payload: AdminOrderStatusUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return update_admin_order_status(db, current_user, order_id, payload)


@router.get("/notifications", response_model=list[AdminNotificationRead])
def get_notifications(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_admin_notifications(db, current_user)


@router.patch(
    "/notifications/{notification_id}/read",
    response_model=NotificationMarkReadResponse,
)
def patch_notification_read(
    notification_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return mark_admin_notification_read(db, current_user, notification_id)
