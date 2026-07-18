from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.controllers.campaign_controller import (
    get_public_campaign_detail,
    list_public_campaigns,
)
from app.controllers.deals_controller import get_deals_hub, GHANA_CAMPAIGN_TAGS
from app.controllers.catalog_controller import (
    get_catalog_product,
    get_store,
    list_active_flash_sale_events,
    list_flash_sale_event_products,
    list_markets,
    list_catalog_categories,
    list_catalog_products,
    list_promo_banners,
    list_stores,
    serialize_catalog_product,
    serialize_catalog_products,
)
from app.core.cache import (
    TTL_CATEGORIES,
    TTL_FLASH_SALE_EVENTS,
    TTL_MARKETS,
    TTL_PRODUCT_DETAIL,
    TTL_PROMO_BANNERS,
    TTL_STORE_DETAIL,
    TTL_STORES_LIST,
    build_products_cache_key,
    build_stores_cache_key,
    cache_get_json,
    cache_set_json,
    cached_item,
    cached_list,
    products_list_ttl,
    set_cache_control,
)
from app.core.database import get_db
from app.services.deal_catalog_service import list_deal_products
from app.schemas.catalog import (
    CategoryRead,
    FlashSaleEventRead,
    MarketRead,
    DealsHubRead,
    MerchandisingCampaignDetailRead,
    MerchandisingCampaignRead,
    ProductRead,
    PromoBannerRead,
    StoreRead,
)

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/categories", response_model=list[CategoryRead])
def get_categories(response: Response, db: Session = Depends(get_db)):
    return cached_list(
        key="catalog:categories",
        ttl_seconds=TTL_CATEGORIES,
        schema=CategoryRead,
        loader=lambda: list_catalog_categories(db),
        response=response,
    )


@router.get("/products", response_model=list[ProductRead])
def get_products(
    response: Response,
    audience: str | None = Query(default=None, max_length=50),
    section: str | None = Query(default=None, max_length=50),
    placement: str | None = Query(default=None, max_length=50),
    flash_event: str | None = Query(default=None, max_length=80),
    category: str | None = Query(default=None, max_length=120),
    subcategory: str | None = Query(default=None, max_length=120),
    store_id: str | None = Query(default=None, max_length=100),
    limit: int | None = Query(default=None, ge=1, le=100),
    offset: int | None = Query(default=None, ge=0),
    db: Session = Depends(get_db),
):
    cache_key = build_products_cache_key(
        audience=audience,
        section=section,
        placement=placement,
        flash_event=flash_event,
        category=category,
        subcategory=subcategory,
        store_id=store_id,
        limit=limit,
        offset=offset,
    )
    ttl = products_list_ttl(section=section, placement=placement)

    cached = cache_get_json(cache_key)
    if cached is not None:
        set_cache_control(response, ttl, hit=True)
        return [ProductRead.model_validate(item) for item in cached]

    products = list_catalog_products(
        db,
        audience=audience,
        section=section,
        placement=placement,
        flash_event=flash_event,
        category=category,
        subcategory=subcategory,
        store_id=store_id,
        limit=limit,
        offset=offset,
    )
    serialized = serialize_catalog_products(db, products)
    payload = [item.model_dump(mode="json") for item in serialized]
    cache_set_json(cache_key, payload, ttl)
    set_cache_control(response, ttl, hit=False)
    return serialized


@router.get("/products/{product_id}", response_model=ProductRead)
def get_product(product_id: str, response: Response, db: Session = Depends(get_db)):
    cache_key = f"catalog:product:{product_id}"
    ttl = TTL_PRODUCT_DETAIL

    cached = cache_get_json(cache_key)
    if cached is not None:
        set_cache_control(response, ttl, hit=True)
        return ProductRead.model_validate(cached)

    product = get_catalog_product(db, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That product was not found.",
        )

    serialized = serialize_catalog_product(db, product)
    if serialized is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That product was not found.",
        )

    cache_set_json(cache_key, serialized.model_dump(mode="json"), ttl)
    set_cache_control(response, ttl, hit=False)
    return serialized


@router.get("/markets", response_model=list[MarketRead])
def get_markets(response: Response, db: Session = Depends(get_db)):
    return cached_list(
        key="catalog:markets",
        ttl_seconds=TTL_MARKETS,
        schema=MarketRead,
        loader=lambda: list_markets(db),
        response=response,
    )


@router.get("/stores", response_model=list[StoreRead])
def get_stores(
    response: Response,
    market_slug: str | None = Query(default=None, max_length=50),
    category: str | None = Query(default=None, max_length=120),
    audience: str | None = Query(default=None, max_length=50),
    db: Session = Depends(get_db),
):
    cache_key = build_stores_cache_key(
        market_slug=market_slug,
        category=category,
        audience=audience,
    )

    return cached_list(
        key=cache_key,
        ttl_seconds=TTL_STORES_LIST,
        schema=StoreRead,
        loader=lambda: list_stores(
            db,
            market_slug=market_slug,
            category=category,
            audience=audience,
        ),
        response=response,
    )


@router.get("/stores/{store_id}", response_model=StoreRead)
def get_store_by_id(store_id: str, response: Response, db: Session = Depends(get_db)):
    store = cached_item(
        key=f"catalog:store:{store_id}",
        ttl_seconds=TTL_STORE_DETAIL,
        schema=StoreRead,
        loader=lambda: get_store(db, store_id),
        response=response,
    )
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That store was not found.",
        )

    return store


@router.get("/deals-hub", response_model=DealsHubRead)
def get_deals_hub_endpoint(db: Session = Depends(get_db)):
    return get_deals_hub(db)


@router.get("/campaign-tags")
def get_campaign_tags():
    return [{"tag": tag, "label": label} for tag, label in GHANA_CAMPAIGN_TAGS]


@router.get("/campaigns", response_model=list[MerchandisingCampaignRead])
def get_merchandising_campaigns(
    response: Response,
    db: Session = Depends(get_db),
    featured: bool = Query(default=False),
    limit: int = Query(default=40, ge=1, le=100),
):
    cache_key = f"catalog:campaigns:live:featured={int(featured)}:limit={limit}"
    cached = cache_get_json(cache_key)
    if cached is not None:
        set_cache_control(response, TTL_PROMO_BANNERS, hit=True)
        return [MerchandisingCampaignRead.model_validate(item) for item in cached]

    items = list_public_campaigns(db, featured_only=featured, limit=limit)
    cache_set_json(
        cache_key,
        [item.model_dump(mode="json") for item in items],
        TTL_PROMO_BANNERS,
    )
    set_cache_control(response, TTL_PROMO_BANNERS, hit=False)
    return items


@router.get("/campaigns/{slug}", response_model=MerchandisingCampaignDetailRead)
def get_merchandising_campaign_detail(
    slug: str,
    response: Response,
    db: Session = Depends(get_db),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    cache_key = f"catalog:campaigns:{slug}:limit={limit}:offset={offset}"
    cached = cache_get_json(cache_key)
    if cached is not None:
        set_cache_control(response, TTL_PROMO_BANNERS, hit=True)
        return MerchandisingCampaignDetailRead.model_validate(cached)

    detail = get_public_campaign_detail(db, slug, limit=limit, offset=offset)
    cache_set_json(cache_key, detail.model_dump(mode="json"), TTL_PROMO_BANNERS)
    set_cache_control(response, TTL_PROMO_BANNERS, hit=False)
    return detail


@router.get("/deal-products", response_model=list[ProductRead])
def get_deal_products(
    response: Response,
    db: Session = Depends(get_db),
    min_discount_percent: int | None = Query(default=None, ge=1, le=90),
    campaign_tag: str | None = Query(default=None, max_length=50),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    cache_key = ":".join(
        [
            "catalog",
            "deal-products",
            f"min={min_discount_percent or 'any'}",
            f"campaign={campaign_tag or 'all'}",
            f"limit={limit}",
            f"offset={offset}",
        ]
    )
    ttl = products_list_ttl(section=None, placement=None)

    cached = cache_get_json(cache_key)
    if cached is not None:
        set_cache_control(response, ttl, hit=True)
        return [ProductRead.model_validate(item) for item in cached]

    serialized = list_deal_products(
        db,
        min_discount_percent=min_discount_percent,
        campaign_tag=campaign_tag,
        limit=limit,
        offset=offset,
    )
    payload = [item.model_dump(mode="json") for item in serialized]
    cache_set_json(cache_key, payload, ttl)
    set_cache_control(response, ttl, hit=False)
    return serialized


@router.get("/promo-banners", response_model=list[PromoBannerRead])
def get_promo_banners(
    response: Response,
    db: Session = Depends(get_db),
    placement: str | None = Query(default=None, max_length=30),
):
    cache_key = f"catalog:promo-banners:{placement or 'all'}"
    return cached_list(
        key=cache_key,
        ttl_seconds=TTL_PROMO_BANNERS,
        schema=PromoBannerRead,
        loader=lambda: list_promo_banners(db, placement=placement),
        response=response,
    )


@router.get("/flash-sale-events/active", response_model=list[FlashSaleEventRead])
def get_active_flash_sale_events(response: Response, db: Session = Depends(get_db)):
    return cached_list(
        key="catalog:flash-sale-events:active",
        ttl_seconds=TTL_FLASH_SALE_EVENTS,
        schema=FlashSaleEventRead,
        loader=lambda: list_active_flash_sale_events(db),
        response=response,
    )


@router.get("/flash-sale-events/{slug}/products", response_model=list[ProductRead])
def get_flash_sale_event_products(
    slug: str,
    response: Response,
    db: Session = Depends(get_db),
):
    cache_key = f"catalog:flash-sale-events:{slug}:products"
    ttl = TTL_FLASH_SALE_EVENTS

    cached = cache_get_json(cache_key)
    if cached is not None:
        set_cache_control(response, ttl, hit=True)
        return [ProductRead.model_validate(item) for item in cached]

    serialized = list_flash_sale_event_products(db, slug)
    payload = [item.model_dump(mode="json") for item in serialized]
    cache_set_json(cache_key, payload, ttl)
    set_cache_control(response, ttl, hit=False)
    return serialized
