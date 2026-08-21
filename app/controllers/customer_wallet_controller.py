from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.controllers.notification_controller import create_notification_event
from app.controllers.order_controller import (
    _broadcast_order_realtime,
    _dispatch_order_push,
    _dispatch_vendor_new_order_alerts,
    activate_order_after_payment,
    prepare_order_for_checkout,
)
from app.models import (
    CustomerWallet,
    CustomerWalletTopUp,
    CustomerWalletTransaction,
    Order,
    ReturnRequest,
    User,
)
from app.schemas.customer_wallet import (
    CustomerWalletRead,
    CustomerWalletTopUpCreate,
    CustomerWalletTopUpSessionRead,
    CustomerWalletTopUpVerificationRead,
    CustomerWalletTransactionRead,
    WalletCheckoutCreate,
    WalletCheckoutRead,
)
from app.schemas.order import OrderRead
from app.services.finance_math import amount_to_subunit, round_money
from app.services.paystack_service import initialize_transaction, verify_transaction
from app.services.push_service import build_push_data, send_expo_push_notification
from app.services.sms_service import send_wallet_topup_confirmation_sms

logger = logging.getLogger(__name__)


def _topup_payment_label(topup: CustomerWalletTopUp) -> str | None:
    metadata = (topup.raw_response or {}).get("metadata")
    if not isinstance(metadata, dict):
        return None
    label = metadata.get("payment_label")
    return str(label).strip() if label else None


def _dispatch_wallet_topup_notifications(
    db: Session,
    *,
    user: User,
    topup: CustomerWalletTopUp,
    wallet: CustomerWallet,
) -> None:
    amount = round_money(topup.amount_subunit / 100)
    balance_after = round_money(wallet.available_balance)
    payment_label = _topup_payment_label(topup)

    event = create_notification_event(
        db,
        user,
        kind="wallet_topup",
        title="Wallet topped up",
        body=(
            f"{wallet.currency} {amount:.2f} was added to your ODOS wallet. "
            f"New balance: {wallet.currency} {balance_after:.2f}."
        ),
        icon="wallet-outline",
        accent="success",
        action_label="View wallet",
        route_type="customer_wallet",
        route_target_id=str(wallet.id),
    )

    db.flush()

    try:
        send_expo_push_notification(
            user=user,
            title="Wallet topped up",
            body=f"{wallet.currency} {amount:.2f} added · Balance {wallet.currency} {balance_after:.2f}",
            data=build_push_data(
                push_type="wallet_topup",
                route_type="customer_wallet",
                route_target_id=str(wallet.id),
                notification_event=event,
                extra={
                    "amount": amount,
                    "currency": wallet.currency,
                    "balanceAfter": balance_after,
                },
            ),
        )
    except Exception:
        logger.exception("Failed to send wallet top-up push for user %s", user.id)

    if user.phone_verified and user.phone_number:
        try:
            send_wallet_topup_confirmation_sms(
                phone_number=user.phone_number,
                amount=amount,
                currency=wallet.currency,
                balance_after=balance_after,
                payment_label=payment_label,
            )
        except Exception:
            logger.exception(
                "Failed to send wallet top-up SMS for user %s topup %s",
                user.id,
                topup.id,
            )


def _preferred_channel(payment_type: str | None) -> list[str] | None:
    if not payment_type:
        return None
    normalized = payment_type.strip().lower()
    if normalized == "card":
        return ["card"]
    if normalized in {"momo", "mobile_money"}:
        return ["mobile_money"]
    return None


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


def get_or_create_customer_wallet_for_update(
    db: Session,
    user_id: uuid.UUID,
) -> CustomerWallet:
    wallet = db.scalar(
        select(CustomerWallet)
        .where(CustomerWallet.user_id == user_id)
        .with_for_update()
    )
    if wallet:
        return wallet
    wallet = CustomerWallet(user_id=user_id, currency="GHS")
    db.add(wallet)
    db.flush()
    return wallet


def credit_customer_wallet_for_return(
    db: Session,
    request: ReturnRequest,
) -> CustomerWallet | None:
    if request.status != "refunded":
        return None

    order = request.order
    order_item = request.order_item
    user = order.user if order else None
    if not user or not order_item:
        return None

    refund_amount = round_money(request.refund_amount or 0)
    if refund_amount <= 0:
        return None

    existing_transaction = db.scalar(
        select(CustomerWalletTransaction.id).where(
            CustomerWalletTransaction.return_request_id == request.id,
            CustomerWalletTransaction.kind == "credit_return",
        )
    )
    if existing_transaction:
        return get_or_create_customer_wallet(db, user.id)

    wallet = get_or_create_customer_wallet_for_update(db, user.id)
    wallet.available_balance = round_money(wallet.available_balance + refund_amount)
    wallet.lifetime_refunds = round_money(wallet.lifetime_refunds + refund_amount)

    db.add(
        CustomerWalletTransaction(
            wallet_id=wallet.id,
            user_id=user.id,
            order_id=order.id,
            return_request_id=request.id,
            kind="credit_return",
            title=f"Refund for {order_item.title}",
            amount=refund_amount,
            balance_after=wallet.available_balance,
            metadata_json={"return_request_id": str(request.id)},
        )
    )

    refund_event = create_notification_event(
        db,
        user,
        kind="wallet_refund",
        title="Refund added to your wallet",
        body=f"GHS {refund_amount:.2f} from your return is now in your ODOS wallet.",
        icon="wallet-outline",
        accent="success",
        action_label="View wallet",
        route_type="customer_wallet",
        route_target_id=str(wallet.id),
    )
    try:
        send_expo_push_notification(
            user=user,
            title="Refund added to your wallet",
            body=f"GHS {refund_amount:.2f} is now in your ODOS wallet.",
            data=build_push_data(
                push_type="wallet_refund",
                route_type="customer_wallet",
                route_target_id=str(wallet.id),
                notification_event=refund_event,
                extra={
                    "amount": refund_amount,
                    "currency": wallet.currency,
                    "orderId": str(order.id),
                },
            ),
        )
    except Exception:
        logger.exception("Failed to send wallet refund push for user %s", user.id)
    return wallet


DELIVERY_DELAY_GOODWILL_CREDIT = 5.0


def credit_customer_wallet_for_delivery_delay(db: Session, order: Order) -> CustomerWallet | None:
    """A small, automatic apology credit the first time an order is flagged
    delayed past its stage SLA — see delivery_ops_monitor_service. Idempotent
    per order: safe to call repeatedly, only ever credits once."""
    user = order.user
    if not user:
        return None

    already_credited = db.scalar(
        select(CustomerWalletTransaction.id).where(
            CustomerWalletTransaction.user_id == user.id,
            CustomerWalletTransaction.kind == "credit_delivery_delay",
            CustomerWalletTransaction.order_id == order.id,
        )
    )
    if already_credited:
        return get_or_create_customer_wallet(db, user.id)

    credit_amount = round_money(DELIVERY_DELAY_GOODWILL_CREDIT)
    wallet = get_or_create_customer_wallet_for_update(db, user.id)
    wallet.available_balance = round_money(wallet.available_balance + credit_amount)

    db.add(
        CustomerWalletTransaction(
            wallet_id=wallet.id,
            user_id=user.id,
            order_id=order.id,
            kind="credit_delivery_delay",
            title=f"Delivery delay apology · order #{order.order_number}",
            amount=credit_amount,
            balance_after=wallet.available_balance,
            metadata_json={"order_id": str(order.id)},
        )
    )

    apology_event = create_notification_event(
        db,
        user,
        kind="delivery_delay_apology",
        title="Sorry for the wait 🙏",
        body=(
            f"Order #{order.order_number} is taking longer than expected. "
            f"We've added GHS {credit_amount:.2f} to your ODOS wallet, and our team "
            "is on it — you can also reach support anytime from this order."
        ),
        icon="heart-outline",
        accent="warning",
        action_label="View order",
        route_type="order",
        route_target_id=str(order.id),
    )
    try:
        send_expo_push_notification(
            user=user,
            title="Sorry for the wait 🙏",
            body=f"We've added GHS {credit_amount:.2f} to your wallet while we chase down order #{order.order_number}.",
            data=build_push_data(
                push_type="delivery_delay_apology",
                route_type="order",
                route_target_id=str(order.id),
                notification_event=apology_event,
                extra={"orderId": str(order.id), "amount": credit_amount},
            ),
        )
    except Exception:
        logger.exception("Failed to send delivery-delay apology push for user %s", user.id)

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
    paystack_callback_url = _append_query_params(
        str(request.url_for("paystack_checkout_redirect")),
        return_url=callback_url,
    )
    paystack_cancel_url = _append_query_params(
        str(request.url_for("paystack_checkout_redirect")),
        return_url=cancel_url,
    )
    metadata = {
        "wallet_id": str(wallet.id),
        "user_id": str(current_user.id),
        "kind": "customer_wallet_topup",
        "payment_type": payload.payment_type,
        "payment_label": payload.payment_label,
        "payment_network": payload.payment_network,
        "payment_phone": payload.payment_phone,
        "payment_last4": payload.payment_last4,
    }
    preferred_channels = _preferred_channel(payload.payment_type)
    try:
        paystack_response = initialize_transaction(
            email=current_user.email,
            amount_subunit=amount_to_subunit(amount),
            reference=reference,
            callback_url=paystack_callback_url,
            cancel_url=paystack_cancel_url,
            currency=wallet.currency,
            channels=preferred_channels,
            metadata=metadata,
        )
    except HTTPException as exc:
        # Some Paystack setups reject certain channels (for example mobile money),
        # so we retry without restriction instead of blocking the top-up flow.
        if preferred_channels and exc.status_code == status.HTTP_502_BAD_GATEWAY:
            paystack_response = initialize_transaction(
                email=current_user.email,
                amount_subunit=amount_to_subunit(amount),
                reference=reference,
                callback_url=paystack_callback_url,
                cancel_url=paystack_cancel_url,
                currency=wallet.currency,
                channels=None,
                metadata=metadata,
            )
        else:
            raise
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
        raw_response={"metadata": metadata},
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
) -> CustomerWalletTopUpVerificationRead:
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
    topup_status = _reconcile_wallet_topup(db, topup)
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
    if topup_status == "paid":
        message = "Wallet funded successfully."
    elif topup_status == "cancelled":
        message = "Top-up was cancelled before completion."
    elif topup_status == "pending":
        message = "We're still waiting for payment confirmation."
    else:
        message = "Top-up was not successful."
    return CustomerWalletTopUpVerificationRead(
        reference=reference,
        status=topup_status,
        message=message,
        amount=round_money(topup.amount_subunit / 100),
        currency=topup.currency,
        payment_label=(topup.raw_response or {}).get("metadata", {}).get("payment_label")
        if isinstance((topup.raw_response or {}).get("metadata"), dict)
        else None,
        payment_type=(topup.raw_response or {}).get("metadata", {}).get("payment_type")
        if isinstance((topup.raw_response or {}).get("metadata"), dict)
        else None,
        wallet=_serialize_wallet(refreshed_wallet),
    )


def _reconcile_wallet_topup(db: Session, topup: CustomerWalletTopUp) -> str:
    verification_response = verify_transaction(topup.reference)
    provider_payload = verification_response.get("data", {})
    provider_status = str(provider_payload.get("status") or "").strip().lower()
    amount_subunit = int(provider_payload.get("amount") or 0)
    currency = str(provider_payload.get("currency") or topup.currency).upper()
    now = datetime.now(UTC)

    previous_raw = topup.raw_response if isinstance(topup.raw_response, dict) else {}
    previous_metadata = previous_raw.get("metadata")
    provider_metadata = provider_payload.get("metadata")
    if isinstance(previous_metadata, dict):
        merged_metadata = (
            {**previous_metadata, **provider_metadata}
            if isinstance(provider_metadata, dict)
            else previous_metadata
        )
        topup.raw_response = {**provider_payload, "metadata": merged_metadata}
    else:
        topup.raw_response = provider_payload
    topup.verified_at = now
    topup.gateway_response = provider_payload.get("gateway_response")
    topup.provider_transaction_id = (
        str(provider_payload.get("id")) if provider_payload.get("id") is not None else None
    )
    if amount_subunit != topup.amount_subunit or currency != topup.currency:
        topup.status = "failed"
        db.commit()
        return topup.status
    if provider_status != "success":
        topup.status = "cancelled" if provider_status in {"abandoned", "cancelled"} else "pending"
        db.commit()
        return topup.status

    if topup.status == "paid":
        db.commit()
        return topup.status

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

    user = db.get(User, topup.user_id)
    if user:
        _dispatch_wallet_topup_notifications(
            db,
            user=user,
            topup=topup,
            wallet=wallet,
        )

    db.commit()
    return topup.status


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
    wallet = get_or_create_customer_wallet_for_update(db, current_user.id)
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
    wallet_balance_after = round_money(wallet.available_balance)
    placed_event = activate_order_after_payment(
        db,
        current_user,
        order,
        wallet_balance_after=wallet_balance_after,
    )
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
    _dispatch_order_push(
        user=current_user,
        title="Order placed successfully",
        body=f"Order #{order.order_number} is now being prepared.",
        order=order,
        notification_event=placed_event,
    )
    db.commit()
    db.refresh(order)
    _broadcast_order_realtime(db, order)
    _dispatch_vendor_new_order_alerts(db, order)
    db.commit()
    db.refresh(order)
    return WalletCheckoutRead(
        order=OrderRead.model_validate(order),
        wallet_balance_after=round_money(wallet.available_balance),
        message="Order paid successfully using your wallet.",
    )
