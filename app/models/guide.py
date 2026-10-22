import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class GuideVideo(Base):
    """A tutorial video attached to one resource-center guide in the mobile app.

    The written guides themselves ship inside the app bundle (so the help
    centre works offline and costs no request on open) — only the video is
    remote. `guide_slug` is therefore a soft reference to a slug the app
    defines, deliberately not a foreign key: either side can be ahead of the
    other and both degrade quietly. A slug the app does not know about is
    simply never rendered, and a guide with no row here shows no video
    section at all rather than an empty player.
    """

    __tablename__ = "guide_videos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    guide_slug: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    # NULL means the video covers the whole guide and plays at the top.
    # An integer targets that zero-based step and plays inline beneath it.
    step_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    title: Mapped[str] = mapped_column(String(140), nullable=False)
    description: Mapped[str | None] = mapped_column(String(400), nullable=True)

    video_url: Mapped[str] = mapped_column(String(500), nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider: Mapped[str] = mapped_column(
        String(40),
        default="cloudinary",
        server_default="cloudinary",
        nullable=False,
    )
    # Needed to delete the asset from Cloudinary when the row goes; absent for
    # videos that were linked by URL rather than uploaded through the admin.
    public_id: Mapped[str | None] = mapped_column(String(300), nullable=True)

    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Stored so the player can reserve the right box before the first frame
    # decodes, instead of resizing under the reader mid-scroll.
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)

    is_published: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # The mobile app reads published videos for every guide in one request
        # on open; this is the index that serves it.
        Index("ix_guide_videos_published_slug", "is_published", "guide_slug"),
    )
