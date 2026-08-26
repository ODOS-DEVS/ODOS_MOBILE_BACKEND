"""Aggregation over `promo_analytics_events`.

One implementation serves both audiences. Admin calls pass no store scope and
see the whole marketplace; vendor calls pass `entity_ids` already narrowed to
that vendor's store, so the scoping decision lives at the call site and the
maths cannot drift between the two views.

The funnel is impression → click → conversion. Vouchers additionally carry real
money: `voucher_redemptions` records the discount actually granted, which is
reported alongside the funnel rather than inferred from conversion counts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    MerchandisingCampaign,
    MerchandisingCampaignStore,
    PromoAnalyticsEvent,
    PromoBanner,
    Voucher,
    VoucherRedemption,
)
from app.schemas.promo_analytics import (
    PromoAnalyticsDatapoint,
    PromoAnalyticsLeaderboardItem,
    PromoAnalyticsLeaderboardRead,
    PromoAnalyticsTimeseriesRead,
)

ENTITY_TYPES = ("campaign", "voucher", "banner")


@dataclass
class Funnel:
    impressions: int = 0
    clicks: int = 0
    conversions: int = 0

    @property
    def click_through_rate(self) -> float:
        if self.impressions <= 0:
            return 0.0
        return round(self.clicks / self.impressions * 100, 1)

    @property
    def conversion_rate(self) -> float:
        # Of the people who clicked — a click-to-conversion rate answers "is the
        # offer good?", where impression-to-conversion mostly answers "was it
        # placed well?", which click_through_rate already covers.
        if self.clicks <= 0:
            return 0.0
        return round(self.conversions / self.clicks * 100, 1)


@dataclass
class EntityPerformance:
    entity_id: str
    label: str
    funnel: Funnel = field(default_factory=Funnel)
    redemption_count: int = 0
    unique_user_count: int = 0
    total_discount_amount: float = 0.0


def window_start(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _apply_scope(stmt, entity_type: str, entity_ids: list[str] | None, start: datetime):
    stmt = stmt.where(
        PromoAnalyticsEvent.entity_type == entity_type,
        PromoAnalyticsEvent.created_at >= start,
    )
    if entity_ids is not None:
        stmt = stmt.where(PromoAnalyticsEvent.entity_id.in_(entity_ids))
    return stmt


def funnel_by_entity(
    db: Session,
    entity_type: str,
    entity_ids: list[str] | None,
    start: datetime,
) -> dict[str, Funnel]:
    """Impression/click/conversion counts keyed by entity id."""
    if entity_ids is not None and not entity_ids:
        return {}

    stmt = _apply_scope(
        select(
            PromoAnalyticsEvent.entity_id,
            PromoAnalyticsEvent.event_type,
            func.count(PromoAnalyticsEvent.id),
        ),
        entity_type,
        entity_ids,
        start,
    ).group_by(PromoAnalyticsEvent.entity_id, PromoAnalyticsEvent.event_type)

    result: dict[str, Funnel] = {}
    for entity_id, event_type, count in db.execute(stmt).all():
        funnel = result.setdefault(str(entity_id), Funnel())
        if event_type == "impression":
            funnel.impressions = int(count or 0)
        elif event_type == "click":
            funnel.clicks = int(count or 0)
        elif event_type == "conversion":
            funnel.conversions = int(count or 0)
    return result


def daily_series(
    db: Session,
    entity_type: str,
    entity_ids: list[str] | None,
    days: int,
) -> list[PromoAnalyticsDatapoint]:
    """One datapoint per day, including days with no events at all.

    Gaps matter here: a chart that silently omits quiet days compresses the
    time axis and makes a campaign look busier than it was.
    """
    start = window_start(days)
    buckets: dict[date, Funnel] = {}

    if entity_ids is None or entity_ids:
        day = func.date_trunc("day", PromoAnalyticsEvent.created_at).label("day")
        stmt = _apply_scope(
            select(day, PromoAnalyticsEvent.event_type, func.count(PromoAnalyticsEvent.id)),
            entity_type,
            entity_ids,
            start,
        ).group_by(day, PromoAnalyticsEvent.event_type)

        for bucket, event_type, count in db.execute(stmt).all():
            key = bucket.date() if isinstance(bucket, datetime) else bucket
            funnel = buckets.setdefault(key, Funnel())
            if event_type == "impression":
                funnel.impressions = int(count or 0)
            elif event_type == "click":
                funnel.clicks = int(count or 0)
            elif event_type == "conversion":
                funnel.conversions = int(count or 0)

    today = datetime.now(timezone.utc).date()
    series: list[PromoAnalyticsDatapoint] = []
    for offset in range(days, -1, -1):
        current = today - timedelta(days=offset)
        funnel = buckets.get(current, Funnel())
        series.append(
            PromoAnalyticsDatapoint(
                date=current.isoformat(),
                impressions=funnel.impressions,
                clicks=funnel.clicks,
                conversions=funnel.conversions,
                click_through_rate=funnel.click_through_rate,
                conversion_rate=funnel.conversion_rate,
            )
        )
    return series


# ---------------------------------------------------------------- entity lookup


def campaign_ids_for_store(db: Session, store_id: str) -> list[str]:
    """Campaigns that feature this store.

    A straight join on the campaign/store link table. The previous vendor-side
    implementation loaded every active campaign and filtered in Python via
    get_campaign_target_maps, which issued one query per campaign.
    """
    rows = db.scalars(
        select(MerchandisingCampaignStore.campaign_id).where(
            MerchandisingCampaignStore.store_id == store_id
        )
    ).all()
    return [str(row) for row in rows]


def voucher_ids_for_store(db: Session, store_id: str) -> list[str]:
    rows = db.scalars(
        select(Voucher.id).where(
            Voucher.store_id == store_id,
            Voucher.owner_type == "vendor",
        )
    ).all()
    return [str(row) for row in rows]


def entity_labels(db: Session, entity_type: str, entity_ids: list[str]) -> dict[str, str]:
    """Human-readable name per entity id, so a leaderboard is not a list of UUIDs."""
    if not entity_ids:
        return {}

    uuids: list[uuid.UUID] = []
    for value in entity_ids:
        try:
            uuids.append(uuid.UUID(str(value)))
        except (ValueError, AttributeError, TypeError):
            # Ingested ids are free-form strings; anything that is not a UUID
            # simply has no row to name it.
            continue
    if not uuids:
        return {}

    if entity_type == "campaign":
        rows = db.execute(
            select(MerchandisingCampaign.id, MerchandisingCampaign.title).where(
                MerchandisingCampaign.id.in_(uuids)
            )
        ).all()
    elif entity_type == "voucher":
        rows = db.execute(
            select(Voucher.id, Voucher.title).where(Voucher.id.in_(uuids))
        ).all()
    elif entity_type == "banner":
        rows = db.execute(
            select(PromoBanner.id, PromoBanner.title).where(PromoBanner.id.in_(uuids))
        ).all()
    else:
        return {}

    return {str(row_id): title for row_id, title in rows}


def voucher_money_by_id(
    db: Session,
    voucher_ids: list[str] | None,
    start: datetime,
) -> dict[str, tuple[int, int, float]]:
    """(redemptions, unique users, discount given) per voucher inside the window."""
    stmt = select(
        VoucherRedemption.voucher_id,
        func.count(VoucherRedemption.id),
        func.count(func.distinct(VoucherRedemption.user_id)),
        func.coalesce(func.sum(VoucherRedemption.discount_amount), 0),
    ).where(VoucherRedemption.created_at >= start)

    if voucher_ids is not None:
        if not voucher_ids:
            return {}
        uuids = []
        for value in voucher_ids:
            try:
                uuids.append(uuid.UUID(str(value)))
            except (ValueError, TypeError):
                continue
        if not uuids:
            return {}
        stmt = stmt.where(VoucherRedemption.voucher_id.in_(uuids))

    stmt = stmt.group_by(VoucherRedemption.voucher_id)
    return {
        str(voucher_id): (int(count or 0), int(users or 0), round(float(discount or 0), 2))
        for voucher_id, count, users, discount in db.execute(stmt).all()
    }


# ---------------------------------------------------------------- public builders


def build_timeseries(
    db: Session,
    *,
    entity_type: str,
    days: int,
    entity_ids: list[str] | None = None,
    entity_id: str | None = None,
) -> PromoAnalyticsTimeseriesRead:
    scoped_ids = [entity_id] if entity_id else entity_ids
    series = daily_series(db, entity_type, scoped_ids, days)
    return PromoAnalyticsTimeseriesRead(
        entity_type=entity_type,
        entity_id=entity_id,
        data=series,
        total_impressions=sum(point.impressions for point in series),
        total_clicks=sum(point.clicks for point in series),
        total_conversions=sum(point.conversions for point in series),
    )


def build_performance(
    db: Session,
    *,
    entity_type: str,
    days: int,
    entity_ids: list[str] | None = None,
) -> list[EntityPerformance]:
    """Per-entity funnel (plus money, for vouchers), best conversions first."""
    start = window_start(days)
    funnels = funnel_by_entity(db, entity_type, entity_ids, start)

    known_ids = entity_ids if entity_ids is not None else list(funnels.keys())
    labels = entity_labels(db, entity_type, known_ids)
    money = (
        voucher_money_by_id(db, entity_ids, start) if entity_type == "voucher" else {}
    )

    rows: list[EntityPerformance] = []
    for entity_id in known_ids:
        key = str(entity_id)
        redemptions, users, discount = money.get(key, (0, 0, 0.0))
        rows.append(
            EntityPerformance(
                entity_id=key,
                label=labels.get(key, key),
                funnel=funnels.get(key, Funnel()),
                redemption_count=redemptions,
                unique_user_count=users,
                total_discount_amount=discount,
            )
        )

    rows.sort(
        key=lambda row: (
            row.funnel.conversions,
            row.total_discount_amount,
            row.funnel.clicks,
        ),
        reverse=True,
    )
    return rows


def build_leaderboard(
    db: Session,
    *,
    entity_type: str,
    days: int,
    limit: int = 10,
    entity_ids: list[str] | None = None,
) -> PromoAnalyticsLeaderboardRead:
    rows = build_performance(db, entity_type=entity_type, days=days, entity_ids=entity_ids)
    return PromoAnalyticsLeaderboardRead(
        entity_type=entity_type,
        items=[
            PromoAnalyticsLeaderboardItem(
                entity_id=row.entity_id,
                entity_label=row.label,
                impressions=row.funnel.impressions,
                clicks=row.funnel.clicks,
                conversions=row.funnel.conversions,
                click_through_rate=row.funnel.click_through_rate,
                conversion_rate=row.funnel.conversion_rate,
            )
            for row in rows[:limit]
        ],
    )


def _to_performance_schema(row: EntityPerformance):
    from app.schemas.promo_analytics import PromoAnalyticsEntityPerformance

    return PromoAnalyticsEntityPerformance(
        entity_id=row.entity_id,
        entity_label=row.label,
        impressions=row.funnel.impressions,
        clicks=row.funnel.clicks,
        conversions=row.funnel.conversions,
        click_through_rate=row.funnel.click_through_rate,
        conversion_rate=row.funnel.conversion_rate,
        redemption_count=row.redemption_count,
        unique_user_count=row.unique_user_count,
        total_discount_amount=row.total_discount_amount,
    )


def build_overview(
    db: Session,
    *,
    days: int,
    store_id: str | None = None,
    top_limit: int = 10,
):
    """Campaign + voucher + banner performance in a single response.

    `store_id` switches the whole thing from marketplace-wide (admin) to one
    vendor's own promotions. Banners are marketplace furniture — a vendor does
    not own any — so a store-scoped call reports an empty banner channel rather
    than the marketplace's numbers.
    """
    from app.schemas.promo_analytics import (
        PromoAnalyticsChannelSummary,
        PromoAnalyticsOverviewRead,
    )

    start = window_start(days)
    channels: list[PromoAnalyticsChannelSummary] = []
    all_rows: list[EntityPerformance] = []

    for entity_type in ENTITY_TYPES:
        if store_id is None:
            entity_ids: list[str] | None = None
        elif entity_type == "campaign":
            entity_ids = campaign_ids_for_store(db, store_id)
        elif entity_type == "voucher":
            entity_ids = voucher_ids_for_store(db, store_id)
        else:
            entity_ids = []

        rows = build_performance(db, entity_type=entity_type, days=days, entity_ids=entity_ids)
        all_rows.extend(rows)

        totals = Funnel(
            impressions=sum(row.funnel.impressions for row in rows),
            clicks=sum(row.funnel.clicks for row in rows),
            conversions=sum(row.funnel.conversions for row in rows),
        )
        channels.append(
            PromoAnalyticsChannelSummary(
                entity_type=entity_type,
                tracked_entities=len(rows),
                impressions=totals.impressions,
                clicks=totals.clicks,
                conversions=totals.conversions,
                click_through_rate=totals.click_through_rate,
                conversion_rate=totals.conversion_rate,
            )
        )

    voucher_ids = voucher_ids_for_store(db, store_id) if store_id else None
    money = voucher_money_by_id(db, voucher_ids, start)

    all_rows.sort(
        key=lambda row: (row.funnel.conversions, row.total_discount_amount, row.funnel.clicks),
        reverse=True,
    )

    return PromoAnalyticsOverviewRead(
        days=days,
        generated_at=datetime.now(timezone.utc),
        scope="store" if store_id else "marketplace",
        channels=channels,
        total_discount_given=round(sum(value[2] for value in money.values()), 2),
        total_redemptions=sum(value[0] for value in money.values()),
        top_performers=[_to_performance_schema(row) for row in all_rows[:top_limit]],
    )
