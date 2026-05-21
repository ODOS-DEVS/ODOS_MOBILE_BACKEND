from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.controllers.notification_controller import create_notification_event
from app.controllers.finance_controller import record_vendor_payout_paid
from app.core.config import settings
from app.models import (
    Order,
    ReturnRequest,
    Store,
    User,
    UserRole,
    VendorStatus,
    VendorWallet,
    VendorWalletTransaction,
    VendorWithdrawalRequest,
)
from app.schemas.admin import (
    AdminVendorWithdrawalRequestRead,
    AdminVendorWithdrawalUpdate,
)
from app.schemas.vendor import (
    VendorPayoutInstitutionRead,
    VendorWalletPayoutDetailsUpdate,
    VendorWalletRead,
    VendorWalletTransactionRead,
    VendorWithdrawalCreate,
    VendorWithdrawalRequestRead,
)
from app.services.finance_math import round_money as _round_money
from app.services.finance_math import return_reversal_breakdown, vendor_allocation_map
from app.services.paystack_service import (
    create_transfer_recipient,
    initiate_transfer,
    list_transfer_institutions,
    resolve_account_number,
)
from app.services.realtime_service import realtime_manager

SUPPORTED_PAYOUT_METHOD_TYPES = {"mobile_money", "bank_transfer"}
SUPPORTED_WITHDRAWAL_STATUSES = {
    "pending",
    "approved",
    "processing",
    "rejected",
    "paid",
    "failed",
}
PAYOUT_METHOD_TO_RECIPIENT_TYPE = {
    "mobile_money": "mobile_money",
    "bank_transfer": "ghipss",
}


def _require_approved_vendor(current_user: User) -> None:
    if current_user.role == UserRole.CUSTOMER or current_user.vendor_status != VendorStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your vendor wallet is only available to approved vendors.",
        )


def _mask_account_number(value: str | None) -> str | None:
    if not value:
        return None
    compact = value.strip()
    if len(compact) <= 4:
        return compact
    return f"{'*' * max(len(compact) - 4, 0)}{compact[-4:]}"


def _normalize_provider_identifier(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def _fetch_supported_payout_institutions(
    payout_method_type: str,
) -> list[VendorPayoutInstitutionRead]:
    institutions = list_transfer_institutions(
        payout_method_type=payout_method_type,
        currency=settings.paystack_currency,
    )
    result: list[VendorPayoutInstitutionRead] = []
    for entry in institutions:
        code = str(entry.get("code") or "").strip()
        name = str(entry.get("name") or "").strip()
        if not code or not name:
            continue
        result.append(
            VendorPayoutInstitutionRead(
                code=code,
                name=name,
                payout_method_type=payout_method_type,
                currency=str(entry.get("currency") or settings.paystack_currency).upper(),
            )
        )
    return result


def _resolve_payout_institution(
    payout_method_type: str,
    provider_input: str,
) -> VendorPayoutInstitutionRead:
    institutions = _fetch_supported_payout_institutions(payout_method_type)
    normalized_input = _normalize_provider_identifier(provider_input)
    for institution in institutions:
        code_match = _normalize_provider_identifier(institution.code)
        name_match = _normalize_provider_identifier(institution.name)
        if normalized_input in {code_match, name_match}:
            return institution

    readable_type = "mobile money network" if payout_method_type == "mobile_money" else "bank"
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            f"Choose a supported {readable_type} from the Paystack payout list "
            "before saving payout details."
        ),
    )


def publish_vendor_wallet_updates(vendor_user_id: uuid.UUID) -> None:
    payload = {
        "vendor_user_id": str(vendor_user_id),
        "occurred_at": datetime.now(UTC).isoformat(),
    }
    realtime_manager.publish_user_event_sync(
        str(vendor_user_id),
        "vendor.wallet.updated",
        payload,
    )
    realtime_manager.publish_user_event_sync(
        str(vendor_user_id),
        "vendor.dashboard.updated",
        payload,
    )


def _serialize_wallet_transaction(
    transaction: VendorWalletTransaction,
) -> VendorWalletTransactionRead:
    return VendorWalletTransactionRead(
        id=transaction.id,
        kind=transaction.kind,
        title=transaction.title,
        amount=_round_money(transaction.amount),
        gross_amount=(
            _round_money(transaction.gross_amount)
            if transaction.gross_amount is not None
            else None
        ),
        commission_amount=(
            _round_money(transaction.commission_amount)
            if transaction.commission_amount is not None
            else None
        ),
        balance_after=_round_money(transaction.balance_after),
        order_id=transaction.order_id,
        return_request_id=transaction.return_request_id,
        withdrawal_request_id=transaction.withdrawal_request_id,
        created_at=transaction.created_at,
    )


def _serialize_vendor_withdrawal_request(
    request: VendorWithdrawalRequest,
) -> VendorWithdrawalRequestRead:
    return VendorWithdrawalRequestRead(
        id=request.id,
        status=request.status,
        amount=_round_money(request.amount),
        note=request.note,
        admin_note=request.admin_note,
        payout_method_type=request.payout_method_type,
        payout_account_name=request.payout_account_name,
        payout_account_number_masked=_mask_account_number(
            request.payout_account_number
        )
        or "",
        payout_provider=request.payout_provider,
        transfer_failure_reason=request.transfer_failure_reason,
        reviewed_by_user_id=request.reviewed_by_user_id,
        reviewed_by_name=request.reviewed_by_user.full_name
        if request.reviewed_by_user
        else None,
        reviewed_at=request.reviewed_at,
        paid_at=request.paid_at,
        transfer_initiated_at=request.transfer_initiated_at,
        created_at=request.created_at,
        updated_at=request.updated_at,
    )


def _serialize_vendor_wallet(wallet: VendorWallet) -> VendorWalletRead:
    recent_transactions = sorted(
        wallet.transactions,
        key=lambda transaction: transaction.created_at,
        reverse=True,
    )[:12]
    withdrawal_requests = sorted(
        wallet.withdrawal_requests,
        key=lambda request: request.created_at,
        reverse=True,
    )[:12]
    return VendorWalletRead(
        id=wallet.id,
        vendor_user_id=wallet.vendor_user_id,
        currency=wallet.currency,
        available_balance=_round_money(wallet.available_balance),
        pending_withdrawal_balance=_round_money(wallet.pending_withdrawal_balance),
        lifetime_earnings=_round_money(wallet.lifetime_earnings),
        total_withdrawn=_round_money(wallet.total_withdrawn),
        total_commission=_round_money(wallet.total_commission),
        payout_method_type=wallet.payout_method_type,
        payout_account_name=wallet.payout_account_name,
        payout_account_number_masked=_mask_account_number(
            wallet.payout_account_number
        ),
        payout_provider=wallet.payout_provider,
        recent_transactions=[
            _serialize_wallet_transaction(transaction)
            for transaction in recent_transactions
        ],
        withdrawal_requests=[
            _serialize_vendor_withdrawal_request(request)
            for request in withdrawal_requests
        ],
    )


def get_or_create_vendor_wallet(db: Session, vendor_user_id: uuid.UUID) -> VendorWallet:
    wallet = db.scalar(
        select(VendorWallet).where(VendorWallet.vendor_user_id == vendor_user_id)
    )
    if wallet:
        return wallet

    wallet = VendorWallet(
        vendor_user_id=vendor_user_id,
        currency="GHS",
    )
    db.add(wallet)
    db.flush()
    return wallet


def _load_vendor_wallet(
    db: Session,
    vendor_user_id: uuid.UUID,
) -> VendorWallet:
    wallet = db.scalar(
        select(VendorWallet)
        .options(
            selectinload(VendorWallet.transactions),
            selectinload(VendorWallet.withdrawal_requests).selectinload(
                VendorWithdrawalRequest.reviewed_by_user
            ),
        )
        .where(VendorWallet.vendor_user_id == vendor_user_id)
    )
    if wallet:
        return wallet

    get_or_create_vendor_wallet(db, vendor_user_id)
    db.commit()
    wallet = db.scalar(
        select(VendorWallet)
        .options(
            selectinload(VendorWallet.transactions),
            selectinload(VendorWallet.withdrawal_requests).selectinload(
                VendorWithdrawalRequest.reviewed_by_user
            ),
        )
        .where(VendorWallet.vendor_user_id == vendor_user_id)
    )
    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="We couldn't prepare the vendor wallet.",
        )
    return wallet


def _vendor_allocation_map(
    order: Order,
    *,
    vendor_scope: set[uuid.UUID] | None = None,
) -> dict[uuid.UUID, dict[str, float]]:
    return vendor_allocation_map(order, vendor_scope=vendor_scope)


def settle_vendor_wallets_for_order(
    db: Session,
    order: Order,
    *,
    vendor_scope: set[uuid.UUID] | None = None,
) -> set[uuid.UUID]:
    allocations = _vendor_allocation_map(order, vendor_scope=vendor_scope)
    if not allocations:
        return set()

    changed_vendor_ids: set[uuid.UUID] = set()
    for vendor_user_id, allocation in allocations.items():
        existing_transaction = db.scalar(
            select(VendorWalletTransaction.id).where(
                VendorWalletTransaction.vendor_user_id == vendor_user_id,
                VendorWalletTransaction.order_id == order.id,
                VendorWalletTransaction.kind == "sale_settlement",
            )
        )
        if existing_transaction:
            continue

        wallet = get_or_create_vendor_wallet(db, vendor_user_id)
        wallet.available_balance = _round_money(
            wallet.available_balance + allocation["net_amount"]
        )
        wallet.lifetime_earnings = _round_money(
            wallet.lifetime_earnings + allocation["net_amount"]
        )
        wallet.total_commission = _round_money(
            wallet.total_commission + allocation["commission_amount"]
        )

        db.add(
            VendorWalletTransaction(
                wallet_id=wallet.id,
                vendor_user_id=vendor_user_id,
                order_id=order.id,
                kind="sale_settlement",
                title=f"Order #{order.order_number} settled",
                amount=allocation["net_amount"],
                gross_amount=allocation["gross_amount"],
                commission_amount=allocation["commission_amount"],
                balance_after=wallet.available_balance,
            )
        )

        vendor_user = db.get(User, vendor_user_id)
        if vendor_user:
            create_notification_event(
                db,
                vendor_user,
                kind="wallet_credit",
                title="Sale settled to your wallet",
                body=(
                    f"Order #{order.order_number} added {wallet.currency} "
                    f"{allocation['net_amount']:.2f} to your vendor wallet."
                ),
                icon="wallet-outline",
                accent="success",
                action_label="View wallet",
                route_type="vendor_wallet",
                route_target_id=str(wallet.id),
                image_key=order.items[0].image_key if order.items else None,
            )

        changed_vendor_ids.add(vendor_user_id)

    return changed_vendor_ids


def reverse_vendor_wallet_for_return_request(
    db: Session,
    request: ReturnRequest,
) -> uuid.UUID | None:
    if request.status != "refunded":
        return None

    order_item = request.order_item
    if not order_item or not order_item.vendor_user_id:
        return None

    existing_sale_settlement = db.scalar(
        select(VendorWalletTransaction.id).where(
            VendorWalletTransaction.vendor_user_id == order_item.vendor_user_id,
            VendorWalletTransaction.order_id == request.order_id,
            VendorWalletTransaction.kind == "sale_settlement",
        )
    )
    if not existing_sale_settlement:
        return None

    existing_transaction = db.scalar(
        select(VendorWalletTransaction.id).where(
            VendorWalletTransaction.vendor_user_id == order_item.vendor_user_id,
            VendorWalletTransaction.return_request_id == request.id,
            VendorWalletTransaction.kind == "refund_reversal",
        )
    )
    if existing_transaction:
        return None

    order = request.order
    reversal = return_reversal_breakdown(order, order_item, request.quantity)
    refund_gross_amount = reversal["gross_amount"]
    refund_commission_amount = reversal["commission_amount"]
    refund_net_amount = reversal["net_amount"]

    wallet = get_or_create_vendor_wallet(db, order_item.vendor_user_id)
    wallet.available_balance = _round_money(
        wallet.available_balance - refund_net_amount
    )
    wallet.lifetime_earnings = _round_money(
        wallet.lifetime_earnings - refund_net_amount
    )
    wallet.total_commission = _round_money(
        wallet.total_commission - refund_commission_amount
    )

    db.add(
        VendorWalletTransaction(
            wallet_id=wallet.id,
            vendor_user_id=order_item.vendor_user_id,
            order_id=order.id,
            return_request_id=request.id,
            kind="refund_reversal",
            title=f"Refund reversal for order #{order.order_number}",
            amount=-refund_net_amount,
            gross_amount=refund_gross_amount,
            commission_amount=refund_commission_amount,
            balance_after=wallet.available_balance,
        )
    )

    vendor_user = db.get(User, order_item.vendor_user_id)
    if vendor_user:
        create_notification_event(
            db,
            vendor_user,
            kind="wallet_reversal",
            title="Refund deducted from your wallet",
            body=(
                f"{wallet.currency} {refund_net_amount:.2f} was reversed for "
                f"{request.order_item.title} after a refund was completed."
            ),
            icon="refresh-circle-outline",
            accent="warning",
            action_label="View wallet",
            route_type="vendor_wallet",
            route_target_id=str(wallet.id),
            image_key=request.order_item.image_key,
        )

    return order_item.vendor_user_id


def fetch_vendor_wallet(db: Session, current_user: User) -> VendorWalletRead:
    _require_approved_vendor(current_user)
    wallet = _load_vendor_wallet(db, current_user.id)
    return _serialize_vendor_wallet(wallet)


def list_vendor_payout_institutions(
    current_user: User,
    payout_method_type: str,
) -> list[VendorPayoutInstitutionRead]:
    _require_approved_vendor(current_user)
    if payout_method_type not in SUPPORTED_PAYOUT_METHOD_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported payout method.",
        )
    return _fetch_supported_payout_institutions(payout_method_type)


def update_vendor_payout_details(
    db: Session,
    current_user: User,
    payload: VendorWalletPayoutDetailsUpdate,
) -> VendorWalletRead:
    _require_approved_vendor(current_user)
    if payload.payout_method_type not in SUPPORTED_PAYOUT_METHOD_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Choose either mobile money or bank transfer for payouts.",
        )

    wallet = _load_vendor_wallet(db, current_user.id)
    resolved_provider_name = payload.payout_provider
    resolved_provider_code: str | None = None
    resolved_account_name = payload.payout_account_name
    recipient_code: str | None = None

    if settings.paystack_is_configured:
        institution = _resolve_payout_institution(
            payload.payout_method_type,
            payload.payout_provider,
        )
        resolved_provider_name = institution.name
        resolved_provider_code = institution.code

        if payload.payout_method_type == "bank_transfer":
            resolved_account = resolve_account_number(
                account_number=payload.payout_account_number,
                bank_code=institution.code,
            )
            resolved_account_name = str(
                resolved_account.get("account_name") or payload.payout_account_name
            ).strip()

        recipient = create_transfer_recipient(
            recipient_type=PAYOUT_METHOD_TO_RECIPIENT_TYPE[payload.payout_method_type],
            name=resolved_account_name,
            account_number=payload.payout_account_number,
            bank_code=institution.code,
            currency=settings.paystack_currency,
            description=f"ODOS vendor payout for {current_user.email}",
        )
        recipient_code = str(recipient.get("recipient_code") or "").strip() or None
        if not recipient_code:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Paystack did not return a reusable payout recipient code.",
            )

    wallet.payout_method_type = payload.payout_method_type
    wallet.payout_account_name = resolved_account_name
    wallet.payout_account_number = payload.payout_account_number
    wallet.payout_provider = resolved_provider_name
    wallet.payout_provider_code = resolved_provider_code
    wallet.paystack_recipient_code = recipient_code
    db.commit()
    refreshed_wallet = _load_vendor_wallet(db, current_user.id)
    publish_vendor_wallet_updates(current_user.id)
    return _serialize_vendor_wallet(refreshed_wallet)


def create_vendor_withdrawal_request(
    db: Session,
    current_user: User,
    payload: VendorWithdrawalCreate,
) -> VendorWithdrawalRequestRead:
    _require_approved_vendor(current_user)
    wallet = _load_vendor_wallet(db, current_user.id)
    amount = _round_money(payload.amount)

    if amount < float(settings.vendor_withdrawal_minimum):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Withdrawals must be at least "
                f"GHS {float(settings.vendor_withdrawal_minimum):.2f}."
            ),
        )

    if amount > wallet.available_balance:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You do not have enough available balance for that withdrawal.",
        )

    if not (
        wallet.payout_method_type
        and wallet.payout_account_name
        and wallet.payout_account_number
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Save your payout method details before requesting a withdrawal.",
        )

    if settings.paystack_is_configured and not wallet.paystack_recipient_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Your payout details are not fully verified yet. Save your payout "
                "details again so ODOS can register them with Paystack."
            ),
        )

    wallet.available_balance = _round_money(wallet.available_balance - amount)
    wallet.pending_withdrawal_balance = _round_money(
        wallet.pending_withdrawal_balance + amount
    )

    withdrawal_request = VendorWithdrawalRequest(
        wallet_id=wallet.id,
        vendor_user_id=current_user.id,
        status="pending",
        amount=amount,
        note=payload.note,
        payout_method_type=wallet.payout_method_type,
        payout_account_name=wallet.payout_account_name,
        payout_account_number=wallet.payout_account_number,
        payout_provider=wallet.payout_provider,
        payout_provider_code=wallet.payout_provider_code,
        paystack_recipient_code=wallet.paystack_recipient_code,
    )
    db.add(withdrawal_request)
    db.flush()

    db.add(
        VendorWalletTransaction(
            wallet_id=wallet.id,
            vendor_user_id=current_user.id,
            withdrawal_request_id=withdrawal_request.id,
            kind="withdrawal_requested",
            title="Withdrawal request submitted",
            amount=-amount,
            balance_after=wallet.available_balance,
        )
    )

    create_notification_event(
        db,
        current_user,
        kind="withdrawal_requested",
        title="Withdrawal request submitted",
        body=(
            f"We've queued your {wallet.currency} {amount:.2f} withdrawal for review."
        ),
        icon="cash-outline",
        accent="neutral",
        action_label="View wallet",
        route_type="vendor_wallet",
        route_target_id=str(wallet.id),
    )
    db.commit()
    refreshed_wallet = _load_vendor_wallet(db, current_user.id)
    publish_vendor_wallet_updates(current_user.id)
    matching_request = next(
        (
            request
            for request in refreshed_wallet.withdrawal_requests
            if request.id == withdrawal_request.id
        ),
        None,
    )
    if not matching_request:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The withdrawal was created, but we couldn't reload it.",
    )
    return _serialize_vendor_withdrawal_request(matching_request)


def _release_withdrawal_back_to_available_balance(
    db: Session,
    request: VendorWithdrawalRequest,
    *,
    transaction_kind: str,
    transaction_title: str,
) -> None:
    wallet = request.wallet
    existing_reversal = db.scalar(
        select(VendorWalletTransaction.id).where(
            VendorWalletTransaction.vendor_user_id == request.vendor_user_id,
            VendorWalletTransaction.withdrawal_request_id == request.id,
            VendorWalletTransaction.kind == transaction_kind,
        )
    )
    if existing_reversal:
        return

    wallet.pending_withdrawal_balance = _round_money(
        wallet.pending_withdrawal_balance - request.amount
    )
    wallet.available_balance = _round_money(
        wallet.available_balance + request.amount
    )
    db.add(
        VendorWalletTransaction(
            wallet_id=wallet.id,
            vendor_user_id=request.vendor_user_id,
            withdrawal_request_id=request.id,
            kind=transaction_kind,
            title=transaction_title,
            amount=request.amount,
            balance_after=wallet.available_balance,
        )
    )


def _mark_withdrawal_paid(
    db: Session,
    request: VendorWithdrawalRequest,
) -> None:
    wallet = request.wallet
    if request.status == "paid":
        return
    record_vendor_payout_paid(db, request)
    wallet.pending_withdrawal_balance = _round_money(
        wallet.pending_withdrawal_balance - request.amount
    )
    wallet.total_withdrawn = _round_money(wallet.total_withdrawn + request.amount)
    request.status = "paid"
    request.paid_at = datetime.now(UTC)
    request.transfer_failure_reason = None


def _initiate_vendor_withdrawal_transfer(
    request: VendorWithdrawalRequest,
) -> str:
    if not request.paystack_recipient_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This withdrawal does not have a verified payout recipient yet. "
                "Ask the vendor to save their payout details again."
            ),
        )

    transfer_reference = request.paystack_transfer_reference or f"wd-{uuid.uuid4().hex}"
    transfer_data = initiate_transfer(
        amount_subunit=int(round(request.amount * 100)),
        recipient_code=request.paystack_recipient_code,
        reference=transfer_reference,
        reason=request.note or f"ODOS vendor payout for withdrawal {request.id}",
        currency=request.wallet.currency,
    )
    provider_status = str(transfer_data.get("status") or "").strip().lower()
    if provider_status == "otp":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Paystack transfer OTP is enabled for this business account. "
                "Disable transfer OTP or switch to an approval flow before using "
                "automatic ODOS disbursements."
            ),
        )

    request.paystack_transfer_reference = transfer_reference
    request.paystack_transfer_code = str(transfer_data.get("transfer_code") or "").strip() or None
    request.paystack_transfer_id = (
        str(transfer_data.get("id")) if transfer_data.get("id") is not None else None
    )
    request.transfer_initiated_at = datetime.now(UTC)
    request.transfer_failure_reason = None
    return provider_status or "pending"


def reconcile_paystack_transfer_event(
    db: Session,
    *,
    reference: str,
    event_type: str,
    transfer_payload: dict,
) -> uuid.UUID | None:
    request = db.scalar(
        select(VendorWithdrawalRequest)
        .options(
            selectinload(VendorWithdrawalRequest.wallet),
            selectinload(VendorWithdrawalRequest.vendor_user),
            selectinload(VendorWithdrawalRequest.reviewed_by_user),
        )
        .where(VendorWithdrawalRequest.paystack_transfer_reference == reference)
    )
    if not request:
        return None

    event_status = str(transfer_payload.get("status") or "").strip().lower()
    request.paystack_transfer_code = (
        str(transfer_payload.get("transfer_code") or "").strip()
        or request.paystack_transfer_code
    )
    request.paystack_transfer_id = (
        str(transfer_payload.get("id"))
        if transfer_payload.get("id") is not None
        else request.paystack_transfer_id
    )

    if event_type == "transfer.success" or event_status == "success":
        _mark_withdrawal_paid(db, request)
        create_notification_event(
            db,
            request.vendor_user,
            kind="withdrawal_updated",
            title="Withdrawal paid out",
            body=(
                f"Your {request.wallet.currency} {request.amount:.2f} withdrawal "
                "has been sent successfully."
            ),
            icon="cash-outline",
            accent="success",
            action_label="View wallet",
            route_type="vendor_wallet",
            route_target_id=str(request.wallet.id),
        )
    elif event_type in {"transfer.failed", "transfer.reversed"} or event_status in {
        "failed",
        "reversed",
    }:
        request.status = "failed"
        request.paid_at = None
        request.transfer_failure_reason = str(
            transfer_payload.get("reason")
            or transfer_payload.get("failures")
            or "Paystack could not complete this transfer."
        )[:255]
        _release_withdrawal_back_to_available_balance(
            db,
            request,
            transaction_kind="withdrawal_failed",
            transaction_title="Withdrawal returned after transfer failure",
        )
        create_notification_event(
            db,
            request.vendor_user,
            kind="withdrawal_updated",
            title="Withdrawal transfer failed",
            body=(
                f"ODOS could not complete your {request.wallet.currency} "
                f"{request.amount:.2f} transfer, so the money has been returned "
                "to your available wallet balance."
            ),
            icon="alert-circle-outline",
            accent="warning",
            action_label="View wallet",
            route_type="vendor_wallet",
            route_target_id=str(request.wallet.id),
        )
    else:
        request.status = "processing"

    return request.vendor_user_id


def _serialize_admin_vendor_withdrawal_request(
    request: VendorWithdrawalRequest,
    *,
    store_name_map: dict[uuid.UUID, str | None],
) -> AdminVendorWithdrawalRequestRead:
    return AdminVendorWithdrawalRequestRead(
        id=request.id,
        wallet_id=request.wallet_id,
        vendor_user_id=request.vendor_user_id,
        vendor_name=request.vendor_user.full_name,
        vendor_email=request.vendor_user.email,
        store_name=store_name_map.get(request.vendor_user_id),
        currency=request.wallet.currency,
        status=request.status,
        amount=_round_money(request.amount),
        note=request.note,
        admin_note=request.admin_note,
        payout_method_type=request.payout_method_type,
        payout_account_name=request.payout_account_name,
        payout_account_number_masked=_mask_account_number(
            request.payout_account_number
        )
        or "",
        payout_provider=request.payout_provider,
        paystack_transfer_reference=request.paystack_transfer_reference,
        paystack_transfer_code=request.paystack_transfer_code,
        transfer_failure_reason=request.transfer_failure_reason,
        transfer_initiated_at=request.transfer_initiated_at,
        wallet_available_balance=_round_money(request.wallet.available_balance),
        wallet_pending_withdrawal_balance=_round_money(
            request.wallet.pending_withdrawal_balance
        ),
        reviewed_by_user_id=request.reviewed_by_user_id,
        reviewed_by_name=request.reviewed_by_user.full_name
        if request.reviewed_by_user
        else None,
        reviewed_at=request.reviewed_at,
        paid_at=request.paid_at,
        created_at=request.created_at,
        updated_at=request.updated_at,
    )


def list_admin_vendor_withdrawal_requests(
    db: Session,
    current_user: User,
) -> list[AdminVendorWithdrawalRequestRead]:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access is required for this action.",
        )

    requests = list(
        db.scalars(
            select(VendorWithdrawalRequest)
            .options(
                selectinload(VendorWithdrawalRequest.wallet),
                selectinload(VendorWithdrawalRequest.vendor_user),
                selectinload(VendorWithdrawalRequest.reviewed_by_user),
            )
            .order_by(VendorWithdrawalRequest.created_at.desc())
        ).all()
    )
    vendor_ids = {request.vendor_user_id for request in requests}
    store_rows = db.execute(
        select(Store.vendor_user_id, Store.title)
        .where(Store.vendor_user_id.in_(vendor_ids))
        .order_by(Store.created_at.asc())
    ).all() if vendor_ids else []
    store_name_map: dict[uuid.UUID, str | None] = {}
    for vendor_user_id, store_title in store_rows:
        store_name_map.setdefault(vendor_user_id, store_title)

    return [
        _serialize_admin_vendor_withdrawal_request(
            request,
            store_name_map=store_name_map,
        )
        for request in requests
    ]


def update_admin_vendor_withdrawal_request(
    db: Session,
    current_user: User,
    request_id: str,
    payload: AdminVendorWithdrawalUpdate,
) -> AdminVendorWithdrawalRequestRead:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access is required for this action.",
        )
    if payload.status not in SUPPORTED_WITHDRAWAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported withdrawal status.",
        )

    request = db.scalar(
        select(VendorWithdrawalRequest)
        .options(
            selectinload(VendorWithdrawalRequest.wallet),
            selectinload(VendorWithdrawalRequest.vendor_user),
            selectinload(VendorWithdrawalRequest.reviewed_by_user),
        )
        .where(VendorWithdrawalRequest.id == request_id)
    )
    if not request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Withdrawal request not found.",
        )

    if request.status in {"paid", "rejected", "failed"} and payload.status != request.status:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resolved withdrawal requests cannot be changed again.",
        )

    wallet = request.wallet
    previous_status = request.status
    next_status = payload.status

    if previous_status not in {"rejected", "failed"} and next_status == "rejected":
        _release_withdrawal_back_to_available_balance(
            db,
            request,
            transaction_kind="withdrawal_rejected",
            transaction_title="Withdrawal request declined",
        )
        request.transfer_failure_reason = None
        request.paid_at = None
    elif previous_status != "paid" and next_status == "paid":
        if settings.paystack_is_configured:
            provider_status = _initiate_vendor_withdrawal_transfer(request)
            request.status = "processing"
            request.paid_at = None
            request.transfer_failure_reason = None
            next_status = "processing"
            if provider_status == "success":
                _mark_withdrawal_paid(db, request)
                next_status = "paid"
        else:
            _mark_withdrawal_paid(db, request)
    elif next_status == "approved":
        request.paid_at = None
        request.transfer_failure_reason = None
    elif previous_status != "failed" and next_status == "failed":
        _release_withdrawal_back_to_available_balance(
            db,
            request,
            transaction_kind="withdrawal_failed",
            transaction_title="Withdrawal returned after transfer failure",
        )
        request.paid_at = None

    request.status = next_status
    request.admin_note = payload.admin_note
    request.reviewed_by_user_id = current_user.id
    request.reviewed_at = datetime.now(UTC)

    if next_status == "paid":
        message_title = "Withdrawal paid out"
        message_body = (
            f"Your {wallet.currency} {request.amount:.2f} withdrawal has been marked paid."
        )
        accent = "success"
    elif next_status == "approved":
        message_title = "Withdrawal approved"
        message_body = (
            f"Your {wallet.currency} {request.amount:.2f} withdrawal is approved and awaiting payout."
        )
        accent = "success"
    elif next_status == "processing":
        message_title = "Withdrawal payout started"
        message_body = (
            f"ODOS has started processing your {wallet.currency} {request.amount:.2f} payout."
        )
        accent = "neutral"
    elif next_status == "failed":
        message_title = "Withdrawal payout failed"
        message_body = (
            f"Your {wallet.currency} {request.amount:.2f} payout could not be completed."
        )
        accent = "warning"
    elif next_status == "rejected":
        message_title = "Withdrawal declined"
        message_body = (
            f"Your {wallet.currency} {request.amount:.2f} withdrawal was declined."
        )
        accent = "warning"
    else:
        message_title = "Withdrawal updated"
        message_body = (
            f"Your {wallet.currency} {request.amount:.2f} withdrawal is now {next_status}."
        )
        accent = "neutral"

    create_notification_event(
        db,
        request.vendor_user,
        kind="withdrawal_updated",
        title=message_title,
        body=message_body,
        icon="cash-outline",
        accent=accent,
        action_label="View wallet",
        route_type="vendor_wallet",
        route_target_id=str(wallet.id),
    )

    db.commit()
    refreshed_request = db.scalar(
        select(VendorWithdrawalRequest)
        .options(
            selectinload(VendorWithdrawalRequest.wallet),
            selectinload(VendorWithdrawalRequest.vendor_user),
            selectinload(VendorWithdrawalRequest.reviewed_by_user),
        )
        .where(VendorWithdrawalRequest.id == request.id)
    )
    if not refreshed_request:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The withdrawal request was updated, but we couldn't reload it.",
        )

    store_title = db.scalar(
        select(Store.title).where(Store.vendor_user_id == refreshed_request.vendor_user_id)
    )
    publish_vendor_wallet_updates(refreshed_request.vendor_user_id)
    return _serialize_admin_vendor_withdrawal_request(
        refreshed_request,
        store_name_map={refreshed_request.vendor_user_id: store_title},
    )
