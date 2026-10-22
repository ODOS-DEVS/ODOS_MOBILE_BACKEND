"""Unit tests for guide tutorial videos.

Three things here can break quietly and would only surface on a shopper's
device, so each is pinned:

  1. A video URL the app cannot play. expo-video streams files, not web
     pages — a YouTube link saves fine and then shows a dead player. Same for
     http:// URLs, which iOS ATS and Android cleartext policy both block.
  2. A Cloudinary signature computed over the wrong fields. Cloudinary
     answers 401 and the admin sees "upload failed" with nothing to go on.
  3. A guide catalog that cannot be parsed, which would take the admin's
     guide picker down rather than merely leaving it empty.

Database-backed behaviour (published filtering, coverage, orphan detection)
needs a live session and follows this suite's existing Postgres convention.
"""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from app.core.guide_catalog import known_guide_slugs, load_guide_catalog
from app.schemas.guide import GuideVideoCreate


def build_payload(**overrides):
    payload = {
        "guide_slug": "first-order",
        "title": "Placing your first order",
        "video_url": "https://res.cloudinary.com/demo/video/upload/v1/guide.mp4",
    }
    payload.update(overrides)
    return payload


# --- 1. Only URLs the app can actually play are accepted -------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://res.cloudinary.com/demo/video/upload/v1/guide.mp4",
        "https://res.cloudinary.com/demo/video/upload/v1/guide.mov",
        "https://res.cloudinary.com/demo/video/upload/v1/guide.webm",
        "https://res.cloudinary.com/demo/video/upload/v1/guide.m3u8",
        # A query string must not hide the extension from the check.
        "https://cdn.example.com/guide.mp4?signature=abc123",
    ],
)
def test_accepts_direct_video_urls(url):
    video = GuideVideoCreate(**build_payload(video_url=url))
    assert video.video_url == url


@pytest.mark.parametrize(
    "url",
    [
        # Page links, not media — these are the ones a person naturally pastes.
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://vimeo.com/123456789",
        # Blocked by ATS / cleartext policy before it ever reaches the decoder.
        "http://res.cloudinary.com/demo/video/upload/v1/guide.mp4",
        # Not a video at all.
        "https://res.cloudinary.com/demo/image/upload/v1/poster.jpg",
    ],
)
def test_rejects_urls_the_app_cannot_play(url):
    with pytest.raises(ValidationError):
        GuideVideoCreate(**build_payload(video_url=url))


def test_slug_is_normalised_so_casing_cannot_split_a_guide():
    video = GuideVideoCreate(**build_payload(guide_slug="  First-Order  "))
    assert video.guide_slug == "first-order"


def test_blank_optional_text_becomes_null_rather_than_empty_string():
    video = GuideVideoCreate(**build_payload(description="   ", thumbnail_url=""))
    assert video.description is None
    assert video.thumbnail_url is None


def test_step_index_defaults_to_whole_guide():
    assert GuideVideoCreate(**build_payload()).step_index is None


def test_negative_step_index_is_rejected():
    with pytest.raises(ValidationError):
        GuideVideoCreate(**build_payload(step_index=-1))


# --- 2. The Cloudinary signature covers exactly the signed fields ----------


def test_upload_signature_is_computed_over_folder_and_timestamp_only(monkeypatch):
    """Cloudinary verifies a SHA1 over the sorted, non-excluded params. If this
    drifts, every direct upload 401s."""
    from app.core.config import settings
    from app.services import media_service

    monkeypatch.setattr(settings, "cloudinary_cloud_name", "demo-cloud")
    monkeypatch.setattr(settings, "cloudinary_api_key", "key-123")
    monkeypatch.setattr(settings, "cloudinary_api_secret", "secret-xyz")

    signature = media_service.build_video_upload_signature()

    assert signature["folder"] == "odos/guides/videos"
    assert signature["upload_url"] == (
        "https://api.cloudinary.com/v1_1/demo-cloud/video/upload"
    )
    # The secret itself must never be handed to the browser.
    assert "secret-xyz" not in str(signature.values())

    expected = hashlib.sha1(
        f"folder={signature['folder']}&timestamp={signature['timestamp']}secret-xyz".encode()
    ).hexdigest()
    assert signature["signature"] == expected


def test_upload_signature_refuses_when_cloudinary_is_unconfigured(monkeypatch):
    from fastapi import HTTPException

    from app.core.config import settings
    from app.services import media_service

    monkeypatch.setattr(settings, "cloudinary_cloud_name", "")
    monkeypatch.setattr(settings, "cloudinary_api_key", "")
    monkeypatch.setattr(settings, "cloudinary_api_secret", "")

    with pytest.raises(HTTPException):
        media_service.build_video_upload_signature()


# --- 3. The guide catalog snapshot stays loadable --------------------------


def test_guide_catalog_loads_and_has_unique_slugs():
    catalog = load_guide_catalog()
    assert catalog, "guide_catalog.json is missing or empty — run npm run export:guide-catalog"

    slugs = [guide["slug"] for guide in catalog]
    assert len(slugs) == len(set(slugs))
    assert known_guide_slugs() == set(slugs)


def test_every_catalogued_guide_has_a_title_and_ordered_steps():
    for guide in load_guide_catalog():
        assert guide.get("title"), f"{guide['slug']} has no title"
        indexes = [step["index"] for step in guide.get("steps", [])]
        # The admin's step picker numbers from these, so a gap or a repeat
        # would silently point a video at the wrong step.
        assert indexes == list(range(len(indexes))), f"{guide['slug']} has odd step indexes"


def test_missing_catalog_file_degrades_to_empty_rather_than_raising(monkeypatch, tmp_path):
    from app.core import guide_catalog

    monkeypatch.setattr(guide_catalog, "CATALOG_PATH", tmp_path / "nope.json")
    guide_catalog.load_guide_catalog.cache_clear()
    try:
        assert guide_catalog.load_guide_catalog() == []
    finally:
        guide_catalog.load_guide_catalog.cache_clear()


def test_malformed_catalog_file_degrades_to_empty_rather_than_raising(monkeypatch, tmp_path):
    from app.core import guide_catalog

    broken = tmp_path / "guide_catalog.json"
    broken.write_text("{ not json", encoding="utf-8")

    monkeypatch.setattr(guide_catalog, "CATALOG_PATH", broken)
    guide_catalog.load_guide_catalog.cache_clear()
    try:
        assert guide_catalog.load_guide_catalog() == []
    finally:
        guide_catalog.load_guide_catalog.cache_clear()
