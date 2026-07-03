from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.controllers.voucher_controller import (
    calculate_promotions,
    claim_voucher,
    list_public_promotions,
    list_store_vouchers,
    list_user_vouchers,
    preview_voucher,
    suggest_vouchers,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.voucher import (
    PromotionCalculateRead,
    PromotionCalculateRequest,
    StoreVoucherRead,
    VoucherPreviewRead,
    VoucherPreviewRequest,
    VoucherSuggestionsRequest,
    VoucherWalletRead,
)

router = APIRouter(prefix="/vouchers", tags=["vouchers"])


@router.get("/promotions", response_model=list[StoreVoucherRead])
def get_public_promotions(
    db: Session = Depends(get_db),
):
    return list_public_promotions(db)


@router.get("/stores/{store_id}", response_model=list[StoreVoucherRead])
def get_store_vouchers(
    store_id: str,
    db: Session = Depends(get_db),
):
    return list_store_vouchers(db, store_id)


@router.get("/me", response_model=list[VoucherWalletRead])
def get_my_vouchers(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_user_vouchers(db, current_user)


@router.post("/{voucher_id}/claim", response_model=VoucherWalletRead, status_code=status.HTTP_201_CREATED)
def post_claim_voucher(
    voucher_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return claim_voucher(db, current_user, voucher_id)


@router.post("/calculate", response_model=PromotionCalculateRead)
def post_calculate_promotions(
    payload: PromotionCalculateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return calculate_promotions(db, current_user, payload)


@router.post("/preview", response_model=VoucherPreviewRead)
def post_voucher_preview(
    payload: VoucherPreviewRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return preview_voucher(db, current_user, payload)


@router.post("/suggestions", response_model=list[VoucherPreviewRead])
def post_voucher_suggestions(
    payload: VoucherSuggestionsRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return suggest_vouchers(db, current_user, payload)
