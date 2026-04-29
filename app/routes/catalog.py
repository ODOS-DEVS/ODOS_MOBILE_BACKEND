from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.controllers.catalog_controller import (
    get_catalog_product,
    get_store,
    list_markets,
    list_catalog_categories,
    list_catalog_products,
    list_stores,
)
from app.core.database import get_db
from app.schemas.catalog import CategoryRead, MarketRead, ProductRead, StoreRead

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/categories", response_model=list[CategoryRead])
def get_categories(db: Session = Depends(get_db)):
    return list_catalog_categories(db)


@router.get("/products", response_model=list[ProductRead])
def get_products(
    audience: str | None = Query(default=None, max_length=50),
    section: str | None = Query(default=None, max_length=50),
    limit: int | None = Query(default=None, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return list_catalog_products(
        db,
        audience=audience,
        section=section,
        limit=limit,
    )


@router.get("/products/{product_id}", response_model=ProductRead)
def get_product(product_id: str, db: Session = Depends(get_db)):
    product = get_catalog_product(db, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That product was not found.",
        )

    return product


@router.get("/markets", response_model=list[MarketRead])
def get_markets(db: Session = Depends(get_db)):
    return list_markets(db)


@router.get("/stores", response_model=list[StoreRead])
def get_stores(
    market_slug: str | None = Query(default=None, max_length=50),
    category: str | None = Query(default=None, max_length=120),
    db: Session = Depends(get_db),
):
    return list_stores(db, market_slug=market_slug, category=category)


@router.get("/stores/{store_id}", response_model=StoreRead)
def get_store_by_id(store_id: str, db: Session = Depends(get_db)):
    store = get_store(db, store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That store was not found.",
        )

    return store
