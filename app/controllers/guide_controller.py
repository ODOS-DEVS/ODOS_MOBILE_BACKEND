"""Tutorial videos for the mobile app's resource-center guides."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.guide_catalog import load_guide_catalog
from app.models import GuideVideo
from app.schemas.guide import (
    GuideCatalogEntry,
    GuideCatalogResponse,
    GuideVideoCreate,
    GuideVideoUpdate,
)
from app.services.media_service import build_video_upload_signature, remove_video_file


def _ordered(statement):
    """One stable order everywhere: guide-level videos before step videos,
    then the admin's chosen order, then oldest first. Without the final key
    two videos sharing a sort_order could swap places between requests."""
    return statement.order_by(
        GuideVideo.guide_slug,
        GuideVideo.step_index.nulls_first(),
        GuideVideo.sort_order,
        GuideVideo.created_at,
    )


def list_published_guide_videos(db: Session) -> list[GuideVideo]:
    """Every published video, for the mobile app to fetch once and hold.

    Returned whole rather than per-guide because the set is small (a handful
    of videos across ~13 guides) and the app needs to know which guides have
    one before the reader opens any of them — that is what drives the Watch
    filter on the index.
    """
    return list(
        db.scalars(
            _ordered(select(GuideVideo).where(GuideVideo.is_published.is_(True)))
        ).all()
    )


def list_admin_guide_videos(db: Session) -> list[GuideVideo]:
    return list(db.scalars(_ordered(select(GuideVideo))).all())


def get_guide_catalog(db: Session) -> GuideCatalogResponse:
    """The guides the shipped app knows about, each annotated with how many
    videos it already has, plus any slug that has videos the app no longer
    lists — the only way an admin would otherwise discover that renaming a
    guide stranded its video."""
    videos = list_admin_guide_videos(db)

    total_by_slug: dict[str, int] = {}
    published_by_slug: dict[str, int] = {}
    for video in videos:
        total_by_slug[video.guide_slug] = total_by_slug.get(video.guide_slug, 0) + 1
        if video.is_published:
            published_by_slug[video.guide_slug] = (
                published_by_slug.get(video.guide_slug, 0) + 1
            )

    entries: list[GuideCatalogEntry] = []
    for guide in load_guide_catalog():
        slug = str(guide["slug"])
        entries.append(
            GuideCatalogEntry(
                slug=slug,
                title=str(guide.get("title") or slug),
                category=str(guide.get("category") or "Uncategorised"),
                summary=guide.get("summary"),
                minutes=guide.get("minutes"),
                starter_order=guide.get("starter_order"),
                steps=guide.get("steps") or [],
                video_count=total_by_slug.get(slug, 0),
                published_video_count=published_by_slug.get(slug, 0),
            )
        )

    known = {entry.slug for entry in entries}
    unknown = sorted({video.guide_slug for video in videos} - known)

    return GuideCatalogResponse(guides=entries, unknown_slugs=unknown)


def _get_video_or_404(db: Session, video_id: uuid.UUID) -> GuideVideo:
    video = db.get(GuideVideo, video_id)
    if not video:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That tutorial video no longer exists.",
        )
    return video


def create_guide_video(payload: GuideVideoCreate, db: Session) -> GuideVideo:
    video = GuideVideo(**payload.model_dump())
    db.add(video)
    db.commit()
    db.refresh(video)
    return video


def update_guide_video(
    video_id: uuid.UUID, payload: GuideVideoUpdate, db: Session
) -> GuideVideo:
    video = _get_video_or_404(db, video_id)

    previous_public_id = video.public_id
    previous_url = video.video_url

    for field, value in payload.model_dump().items():
        setattr(video, field, value)

    db.commit()
    db.refresh(video)

    # Swapping in a re-uploaded file would otherwise leave the old asset
    # billable and unreachable forever.
    replaced = previous_url != video.video_url
    if replaced and previous_public_id and previous_public_id != video.public_id:
        remove_video_file(previous_url, previous_public_id)

    return video


def delete_guide_video(video_id: uuid.UUID, db: Session) -> None:
    video = _get_video_or_404(db, video_id)

    public_id = video.public_id
    video_url = video.video_url

    db.delete(video)
    db.commit()

    remove_video_file(video_url, public_id)


def create_video_upload_signature() -> dict[str, str | int]:
    return build_video_upload_signature()
