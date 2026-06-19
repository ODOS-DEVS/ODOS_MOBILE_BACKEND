import json
import logging
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from app.core.config import settings
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

CACHE_PREFIX = "odos:cache:"

TTL_CATEGORIES = 900
TTL_MARKETS = 900
TTL_STORES_LIST = 120
TTL_PRODUCTS_LIST = 60
TTL_PRODUCTS_FLASH = 30
TTL_PRODUCT_DETAIL = 60
TTL_STORE_DETAIL = 60
TTL_PROMO_BANNERS = 120
TTL_FLASH_SALE_EVENTS = 30

SchemaT = TypeVar("SchemaT", bound=BaseModel)


def cache_is_enabled() -> bool:
    return settings.cache_enabled and bool(settings.redis_url.strip())


def _full_key(key: str) -> str:
    return f"{CACHE_PREFIX}{key}"


def cache_get_json(key: str) -> Any | None:
    if not cache_is_enabled():
        return None

    client = get_redis()
    if client is None:
        return None

    try:
        raw = client.get(_full_key(key))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.warning("Cache read failed for %s: %s", key, exc)
        return None


def cache_set_json(key: str, value: Any, ttl_seconds: int) -> None:
    if not cache_is_enabled() or ttl_seconds <= 0:
        return

    client = get_redis()
    if client is None:
        return

    try:
        client.setex(_full_key(key), ttl_seconds, json.dumps(value, default=str))
    except Exception as exc:
        logger.warning("Cache write failed for %s: %s", key, exc)


def cache_delete(key: str) -> None:
    if not cache_is_enabled():
        return

    client = get_redis()
    if client is None:
        return

    try:
        client.delete(_full_key(key))
    except Exception as exc:
        logger.warning("Cache delete failed for %s: %s", key, exc)


def cache_delete_matching(pattern: str) -> int:
    if not cache_is_enabled():
        return 0

    client = get_redis()
    if client is None:
        return 0

    full_pattern = _full_key(pattern)
    deleted = 0

    try:
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor=cursor, match=full_pattern, count=100)
            if keys:
                deleted += int(client.delete(*keys))
            if cursor == 0:
                break
    except Exception as exc:
        logger.warning("Cache pattern delete failed for %s: %s", pattern, exc)

    return deleted


def set_cache_control(response, ttl_seconds: int, *, hit: bool) -> None:
    response.headers["Cache-Control"] = f"public, max-age={ttl_seconds}"
    response.headers["X-Cache"] = "HIT" if hit else "MISS"


def cached_list(
    *,
    key: str,
    ttl_seconds: int,
    schema: type[SchemaT],
    loader: Callable[[], list[Any]],
    response,
) -> list[SchemaT]:
    cached = cache_get_json(key)
    if cached is not None:
        set_cache_control(response, ttl_seconds, hit=True)
        return [schema.model_validate(item) for item in cached]

    rows = loader()
    payload = [schema.model_validate(row).model_dump(mode="json") for row in rows]
    cache_set_json(key, payload, ttl_seconds)
    set_cache_control(response, ttl_seconds, hit=False)
    return [schema.model_validate(row) for row in rows]


def cached_item(
    *,
    key: str,
    ttl_seconds: int,
    schema: type[SchemaT],
    loader: Callable[[], Any | None],
    response,
) -> SchemaT | None:
    cached = cache_get_json(key)
    if cached is not None:
        set_cache_control(response, ttl_seconds, hit=True)
        return schema.model_validate(cached)

    row = loader()
    if row is None:
        return None

    payload = schema.model_validate(row).model_dump(mode="json")
    cache_set_json(key, payload, ttl_seconds)
    set_cache_control(response, ttl_seconds, hit=False)
    return schema.model_validate(row)


def build_products_cache_key(
    *,
    audience: str | None,
    section: str | None,
    placement: str | None,
    flash_event: str | None,
    category: str | None,
    subcategory: str | None,
    store_id: str | None,
    limit: int | None,
    offset: int | None = None,
) -> str:
    parts = ["catalog", "products"]
    for name, value in (
        ("audience", audience),
        ("section", section),
        ("placement", placement),
        ("flash_event", flash_event),
        ("category", category),
        ("subcategory", subcategory),
        ("store_id", store_id),
        ("limit", limit),
        ("offset", offset),
    ):
        if value is not None:
            parts.append(f"{name}={value}")
    return ":".join(parts)


def products_list_ttl(*, section: str | None, placement: str | None) -> int:
    if section == "flash-sale" or placement == "flash-sale":
        return TTL_PRODUCTS_FLASH
    return TTL_PRODUCTS_LIST


def build_stores_cache_key(
    *,
    market_slug: str | None,
    category: str | None,
    audience: str | None,
) -> str:
    parts = ["catalog", "stores"]
    for name, value in (
        ("market_slug", market_slug),
        ("category", category),
        ("audience", audience),
    ):
        if value is not None:
            parts.append(f"{name}={value}")
    return ":".join(parts)


def invalidate_catalog_categories() -> None:
    cache_delete("catalog:categories")


def invalidate_catalog_markets() -> None:
    cache_delete("catalog:markets")


def invalidate_catalog_promo_banners() -> None:
    cache_delete("catalog:promo-banners:all")
    cache_delete("catalog:promo-banners:home")
    cache_delete("catalog:promo-banners:deals")
    cache_delete("catalog:promo-banners")


def invalidate_catalog_flash_sale_events() -> None:
    cache_delete("catalog:flash-sale-events:active")
    cache_delete_matching("catalog:flash-sale-events:*")
    invalidate_catalog_products()


def invalidate_catalog_products() -> None:
    cache_delete_matching("catalog:products*")


def invalidate_catalog_product(product_id: str) -> None:
    cache_delete(f"catalog:product:{product_id}")
    invalidate_catalog_products()


def invalidate_catalog_stores() -> None:
    cache_delete_matching("catalog:stores*")


def invalidate_catalog_store(store_id: str) -> None:
    cache_delete(f"catalog:store:{store_id}")
    invalidate_catalog_stores()
