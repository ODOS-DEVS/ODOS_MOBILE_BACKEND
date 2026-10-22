import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Cloudinary delivers HLS as .m3u8; everything else here is a progressive file
# that expo-video can stream directly.
ALLOWED_VIDEO_URL_SUFFIXES = (".mp4", ".mov", ".m4v", ".webm", ".m3u8")


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


class GuideVideoRead(BaseModel):
    """What the mobile app receives. Deliberately excludes `public_id` and
    publish state — the app only ever sees videos it is allowed to play."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    guide_slug: str
    step_index: int | None
    title: str
    description: str | None
    video_url: str
    thumbnail_url: str | None
    duration_seconds: int | None
    width: int | None
    height: int | None


class AdminGuideVideoRead(GuideVideoRead):
    provider: str
    public_id: str | None
    is_published: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class GuideVideoWriteBase(BaseModel):
    guide_slug: str = Field(min_length=1, max_length=80)
    step_index: int | None = Field(default=None, ge=0, le=200)
    title: str = Field(min_length=1, max_length=140)
    description: str | None = Field(default=None, max_length=400)
    video_url: str = Field(min_length=1, max_length=500)
    thumbnail_url: str | None = Field(default=None, max_length=500)
    provider: str = Field(default="cloudinary", max_length=40)
    public_id: str | None = Field(default=None, max_length=300)
    duration_seconds: int | None = Field(default=None, ge=0, le=60 * 60 * 6)
    width: int | None = Field(default=None, ge=0, le=10_000)
    height: int | None = Field(default=None, ge=0, le=10_000)
    is_published: bool = True
    sort_order: int = Field(default=0, ge=0, le=10_000)

    @field_validator("guide_slug")
    @classmethod
    def _normalize_slug(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("A guide slug is required.")
        return cleaned

    @field_validator("title", "provider")
    @classmethod
    def _require_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("This field cannot be blank.")
        return cleaned

    @field_validator("description", "thumbnail_url", "public_id")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        return _clean_optional_text(value)

    @field_validator("video_url")
    @classmethod
    def _validate_video_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.startswith("https://"):
            # Plain http would be blocked by ATS on iOS and cleartext policy on
            # Android, so it would save fine here and fail silently on device.
            raise ValueError("The video URL must start with https://.")

        path = cleaned.split("?", 1)[0].split("#", 1)[0].lower()
        if not path.endswith(ALLOWED_VIDEO_URL_SUFFIXES):
            raise ValueError(
                "The video URL must point directly at a video file "
                f"({', '.join(ALLOWED_VIDEO_URL_SUFFIXES)}). "
                "Page links such as YouTube or Vimeo cannot be played in the app."
            )
        return cleaned


class GuideVideoCreate(GuideVideoWriteBase):
    pass


class GuideVideoUpdate(GuideVideoWriteBase):
    """Full replace on PATCH: the admin form always submits every field, so a
    partial-update model would only add ways for the two to disagree."""


class GuideCatalogStep(BaseModel):
    index: int
    title: str


class GuideCatalogEntry(BaseModel):
    slug: str
    title: str
    category: str
    summary: str | None = None
    minutes: int | None = None
    starter_order: int | None = None
    steps: list[GuideCatalogStep] = Field(default_factory=list)
    video_count: int = 0
    published_video_count: int = 0


class GuideCatalogResponse(BaseModel):
    guides: list[GuideCatalogEntry]
    # Slugs with videos attached that the shipped app no longer lists — the
    # signal that a guide was renamed or removed and its video is now orphaned.
    unknown_slugs: list[str] = Field(default_factory=list)


class CloudinaryVideoSignature(BaseModel):
    """Everything the admin browser needs to POST the file straight to
    Cloudinary. The secret never leaves the server; only this signature does,
    and it is scoped to one folder and expires with its timestamp."""

    cloud_name: str
    api_key: str
    timestamp: int
    folder: str
    signature: str
    upload_url: str
