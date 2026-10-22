import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.controllers.guide_controller import (
    create_guide_video,
    create_video_upload_signature,
    delete_guide_video,
    get_guide_catalog,
    list_admin_guide_videos,
    list_published_guide_videos,
    update_guide_video,
)
from app.core.admin_permissions import require_admin_feature
from app.core.database import get_db
from app.models import User
from app.schemas.guide import (
    AdminGuideVideoRead,
    CloudinaryVideoSignature,
    GuideCatalogResponse,
    GuideVideoCreate,
    GuideVideoRead,
    GuideVideoUpdate,
)

RequireGuidesAdmin = Annotated[User, Depends(require_admin_feature("guides"))]

# Public: read-only, unauthenticated on purpose. The resource center works for
# signed-out shoppers, and help content is not privileged.
router = APIRouter(prefix="/guides", tags=["guides"])

admin_router = APIRouter(prefix="/admin/guides", tags=["admin", "guides"])


@router.get("/videos", response_model=list[GuideVideoRead])
def guide_videos(db: Session = Depends(get_db)):
    """Every published tutorial video, in one request.

    The app fetches this once per session and keys it by guide slug, so a
    reader opening a guide never waits on the network to find out whether a
    video exists — and a guide with no video shows no video section at all.
    """
    return list_published_guide_videos(db)


@admin_router.get("/catalog", response_model=GuideCatalogResponse)
def admin_guide_catalog(
    current_user: RequireGuidesAdmin,
    db: Session = Depends(get_db),
):
    return get_guide_catalog(db)


@admin_router.get("/videos", response_model=list[AdminGuideVideoRead])
def admin_guide_videos(
    current_user: RequireGuidesAdmin,
    db: Session = Depends(get_db),
):
    return list_admin_guide_videos(db)


@admin_router.post(
    "/videos",
    response_model=AdminGuideVideoRead,
    status_code=status.HTTP_201_CREATED,
)
def admin_create_guide_video(
    payload: GuideVideoCreate,
    current_user: RequireGuidesAdmin,
    db: Session = Depends(get_db),
):
    return create_guide_video(payload, db)


@admin_router.patch("/videos/{video_id}", response_model=AdminGuideVideoRead)
def admin_update_guide_video(
    video_id: uuid.UUID,
    payload: GuideVideoUpdate,
    current_user: RequireGuidesAdmin,
    db: Session = Depends(get_db),
):
    return update_guide_video(video_id, payload, db)


@admin_router.delete("/videos/{video_id}")
def admin_delete_guide_video(
    video_id: uuid.UUID,
    current_user: RequireGuidesAdmin,
    db: Session = Depends(get_db),
):
    # Returns a body rather than 204 to match every other admin delete in this
    # API — the admin client always parses a JSON response.
    delete_guide_video(video_id, db)
    return {"success": True}


@admin_router.post("/videos/upload-signature", response_model=CloudinaryVideoSignature)
def admin_guide_video_upload_signature(current_user: RequireGuidesAdmin):
    """Hand the admin browser a short-lived, folder-scoped Cloudinary
    signature so the file itself never travels through this API."""
    return create_video_upload_signature()
