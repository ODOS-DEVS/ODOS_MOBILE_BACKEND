from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.controllers.order_controller import activate_order_after_payment, prepare_order_for_checkout
from app.models import CustomerWallet, CustomerWalletTopUp, CustomerWalletTransaction, User
from app.schemas.customer_wallet import (
    CustomerWalletRead,
    CustomerWalletTopUpCreate,
    CustomerWalletTopUpSessionRead,
    CustomerWalletTransactionRead,
    WalletCheckoutCreate,
    WalletCheckoutRead,
)
from app.schemas.order import OrderRead
from app.services.finance_math import amount_to_subunit, round_money
from app.services.paystack_service import initialize_transaction, verify_transaction


def _append_query_params(url: str, **params: str | None) -> str:
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parsed_url = urlsplit(url)
    query = dict(parse_qsl(parsed_url.query, keep_blank_values=True))
    for key, value in params.items():
        if value is not None:
            query[key] = value
    return urlunsplit(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            urlencode(query),
            parsed_url.fragment,
        )
    )


def get_or_create_customer_wallet(db: Session, user_id: uuid.UUID) -> CustomerWallet:
    wallet = db.scalar(select(CustomerWallet).where(CustomerWallet.user_id == user_id))
    if wallet:
        return wallet
    wallet = CustomerWallet(user_id=user_id, currency="GHS")
    db.add(wallet)
    db.flush()
    return wallet


def _serialize_wallet(wallet: CustomerWallet) -> CustomerWalletRead:
    recent_transactions = sorted(
        wallet.transactions,
        key=lambda transaction: transaction.created_at,
        reverse=True,
    )[:25]
    return CustomerWalletRead(
        id=wallet.id,
        user_id=wallet.user_id,
        currency=wallet.currency,
        available_balance=round_money(wallet.available_balance),
        lifetime_topups=round_money(wallet.lifetime_topups),
        lifetime_spend=round_money(wallet.lifetime_spend),
        lifetime_refunds=round_money(wallet.lifetime_refunds),
        recent_transactions=[
            CustomerWalletTransactionRead(
                id=tx.id,
                kind=tx.kind,
                title=tx.title,
                amount=round_money(tx.amount),
                balance_after=round_money(tx.balance_after),
                order_id=tx.order_id,
                topup_id=tx.topup_id,
                created_at=tx.created_at,
            )
            for tx in recent_transactions
        ],
    )


def fetch_customer_wallet(db: Session, current_user: User) -> CustomerWalletRead:
    wallet = db.scalar(
        select(CustomerWallet)
        .options(selectinload(CustomerWallet.transactions))
        .where(CustomerWallet.user_id == current_user.id)
    )
    if not wallet:
        get_or_create_customer_wallet(db, current_user.id)
        db.commit()
        wallet = db.scalar(
            select(CustomerWallet)
            .options(selectinload(CustomerWallet.transactions))
            .where(CustomerWallet.user_id == current_user.id)
        )
    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="We couldn't prepare your wallet.",
        )
    return _serialize_wallet(wallet)


def initialize_wallet_topup(
    db: Session,
    request: Request,
    current_user: User,
    payload: CustomerWalletTopUpCreate,
) -> CustomerWalletTopUpSessionRead:
    amount = round_money(payload.amount)
    if amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Top up amount must be greater than 0.",
        )
    wallet = get_or_create_customer_wallet(db, current_user.id)
    reference = f"odos-wallet-{uuid.uuid4().hex}"
    callback_url = _append_query_params(
        payload.callback_url or "odosmobileexpo://wallet/topup-return",
        reference=reference,
    )
    cancel_url = _append_query_params(
        payload.cancel_url or payload.callback_url or "odosmobileexpo://wallet/topup-return",
        reference=reference,
        cancelled="1",
    )
    paystack_response = initialize_transaction(
        email=current_user.email,
        amount_subunit=amount_to_subunit(amount),
        reference=reference,
        callback_url=callback_url,
        cancel_url=cancel_url,
        currency=wallet.currency,
        channels=None,
        metadata={
            "wallet_id": str(wallet.id),
            "user_id": str(current_user.id),
            "kind": "customer_wallet_topup",
        },
    )
    response_data = paystack_response.get("data", {})
    topup = CustomerWalletTopUp(
        wallet_id=wallet.id,
        user_id=current_user.id,
        provider="paystack",
        reference=reference,
        access_code=response_data.get("access_code"),
        authorization_url=response_data.get("authorization_url"),
        amount_subunit=amount_to_subunit(amount),
        currency=wallet.currency,
        status="pending",
    )
    db.add(topup)
    db.commit()
    return CustomerWalletTopUpSessionRead(
        reference=reference,
        authorization_url=response_data["authorization_url"],
        access_code=response_data["access_code"],
        amount=amount,
        currency=wallet.currency,
        status="pending",
    )


def verify_wallet_topup(
    db: Session,
    current_user: User,
    reference: str,
) -> CustomerWalletRead:
    topup = db.scalar(
        select(CustomerWalletTopUp)
        .options(selectinload(CustomerWalletTopUp.wallet).selectinload(CustomerWallet.transactions))
        .where(
            CustomerWalletTopUp.reference == reference,
            CustomerWalletTopUp.user_id == current_user.id,
        )
    )
    if not topup or not topup.wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That top up session was not found.",
        )
    _reconcile_wallet_topup(db, topup)
    refreshed_wallet = db.scalar(
        select(CustomerWallet)
        .options(selectinload(CustomerWallet.transactions))
        .where(CustomerWallet.id == topup.wallet_id)
    )
    if not refreshed_wallet:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Top up was processed but wallet reload failed.",
        )
    return _serialize_wallet(refreshed_wallet)


def _reconcile_wallet_topup(db: Session, topup: CustomerWalletTopUp) -> None:
    verification_response = verify_transaction(topup.reference)
    provider_payload = verification_response.get("data", {})
    provider_status = str(provider_payload.get("status") or "").strip().lower()
    amount_subunit = int(provider_payload.get("amount") or 0)
    currency = str(provider_payload.get("currency") or topup.currency).upper()
    now = datetime.now(UTC)

    topup.raw_response = provider_payload
    topup.verified_at = now
    topup.gateway_response = provider_payload.get("gateway_response")
    topup.provider_transaction_id = (
        str(provider_payload.get("id")) if provider_payload.get("id") is not None else None
    )
    if amount_subunit != topup.amount_subunit or currency != topup.currency:
        topup.status = "failed"
        db.commit()
        return
    if provider_status != "success":
        topup.status = "cancelled" if provider_status in {"abandoned", "cancelled"} else "pending"
        db.commit()
        return

    if topup.status == "paid":
        db.commit()
        return

    wallet = topup.wallet
    wallet.available_balance = round_money(wallet.available_balance + (topup.amount_subunit / 100))
    wallet.lifetime_topups = round_money(wallet.lifetime_topups + (topup.amount_subunit / 100))
    topup.status = "paid"
    topup.paid_at = now
    db.add(
        CustomerWalletTransaction(
            wallet_id=wallet.id,
            user_id=wallet.user_id,
            topup_id=topup.id,
            kind="topup",
            title="Wallet top up",
            amount=topup.amount_subunit / 100,
            balance_after=wallet.available_balance,
        )
    )
    db.commit()


def reconcile_wallet_topup_by_reference(db: Session, reference: str) -> uuid.UUID | None:
    topup = db.scalar(
        select(CustomerWalletTopUp)
        .options(selectinload(CustomerWalletTopUp.wallet))
        .where(CustomerWalletTopUp.reference == reference)
    )
    if not topup:
        return None
    _reconcile_wallet_topup(db, topup)
    return topup.user_id


def create_wallet_checkout(
    db: Session,
    current_user: User,
    payload: WalletCheckoutCreate,
) -> WalletCheckoutRead:
    wallet = get_or_create_customer_wallet(db, current_user.id)
    order_total = round_money(payload.total_amount)
    if wallet.available_balance < order_total:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Insufficient wallet balance for this order.",
        )

    order = prepare_order_for_checkout(
        db,
        current_user,
        payload,
        payment_provider="wallet",
        payment_reference=f"wallet-{uuid.uuid4().hex}",
    )
    wallet.available_balance = round_money(wallet.available_balance - order_total)
    wallet.lifetime_spend = round_money(wallet.lifetime_spend + order_total)
    activate_order_after_payment(db, current_user, order)
    order.payment_type = "wallet"
    order.payment_label = payload.payment_label or "Wallet"
    order.payment_provider = "wallet"
    db.add(
        CustomerWalletTransaction(
            wallet_id=wallet.id,
            user_id=current_user.id,
            order_id=order.id,
            kind="debit_order",
            title=f"Order #{order.order_number} payment",
            amount=-order_total,
            balance_after=wallet.available_balance,
        )
    )
    db.commit()
    db.refresh(order)
    return WalletCheckoutRead(
        order=OrderRead.model_validate(order),
        wallet_balance_after=round_money(wallet.available_balance),
        message="Order paid successfully using your wallet.",
    )
