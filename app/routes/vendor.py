from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.orm import Session

from app.controllers.vendor_controller import (
    create_vendor_product,
    delete_vendor_product,
    fetch_my_vendor_application,
    fetch_vendor_dashboard,
    fetch_vendor_profile,
    fetch_vendor_store,
    list_vendor_orders,
    list_vendor_products,
    submit_vendor_application,
    update_vendor_order_status,
    update_vendor_product,
    update_vendor_store,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.user import MessageResponse
from app.schemas.vendor import (
    VendorApplicationRead,
    VendorDashboardRead,
    VendorOrderRead,
    VendorOrderStatusUpdate,
    VendorProductCreate,
    VendorProductRead,
    VendorProductUpdate,
    VendorProfileRead,
    VendorStoreRead,
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


@router.get("/products", response_model=list[VendorProductRead])
def get_vendor_products(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_vendor_products(db, current_user)


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
    subcategory: Annotated[str | None, Form(max_length=120)] = None,
    image_key: Annotated[str, Form(min_length=1, max_length=100)] = "bag",
    image_url: Annotated[str | None, Form(max_length=500)] = None,
    placement_tags: Annotated[str | None, Form(max_length=255)] = None,
    color_options: Annotated[str | None, Form(max_length=255)] = None,
    size_options: Annotated[str | None, Form(max_length=255)] = None,
    specifications: Annotated[str | None, Form(max_length=3000)] = None,
    images: list[UploadFile] | None = File(default=None),
):
    payload = VendorProductCreate(
        name=name,
        description=description,
        category=category,
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
    )
    return await create_vendor_product(db, current_user, payload, images)


@router.patch("/products/{product_id}", response_model=VendorProductRead)
async def patch_vendor_product(
    product_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    name: Annotated[str | None, Form(min_length=1, max_length=255)] = None,
    description: Annotated[str | None, Form(min_length=1, max_length=1000)] = None,
    category: Annotated[str | None, Form(min_length=1, max_length=120)] = None,
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
    status_value: Annotated[str | None, Form(alias="status", max_length=30)] = None,
    images: list[UploadFile] | None = File(default=None),
):
    payload = VendorProductUpdate(
        name=name,
        description=description,
        category=category,
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


@router.get("/orders", response_model=list[VendorOrderRead])
def get_vendor_orders(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_vendor_orders(db, current_user)


@router.patch("/orders/{order_id}/status", response_model=VendorOrderRead)
def patch_vendor_order_status(
    order_id: str,
    payload: VendorOrderStatusUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return update_vendor_order_status(db, current_user, order_id, payload)


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
    logo_image: UploadFile | None = File(default=None),
    banner_image: UploadFile | None = File(default=None),
):
    return await update_vendor_store(
        db,
        current_user,
        name=name,
        description=description,
        category=category,
        audience_slugs=_split_csv_values(audience_slugs),
        market_id=market_id,
        location=location,
        region=region,
        city=city,
        logo_image=logo_image,
        banner_image=banner_image,
    )
