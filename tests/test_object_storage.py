import io

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services import media_service, object_storage  # noqa: E402
from app.services.image_variants import VARIANT_WIDTHS, VariantError, build_variants  # noqa: E402

BASE = "https://usc1.contabostorage.com/tenant:odos-media"


@pytest.fixture
def s3_settings(monkeypatch):
    monkeypatch.setattr(settings, "s3_endpoint_url", "https://usc1.contabostorage.com")
    monkeypatch.setattr(settings, "s3_bucket", "odos-media")
    monkeypatch.setattr(settings, "s3_access_key", "key")
    monkeypatch.setattr(settings, "s3_secret_key", "secret")
    monkeypatch.setattr(settings, "s3_public_base_url", BASE)


def _png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_variants_cover_every_width_and_never_upscale():
    variants = build_variants(_png(1000, 500))
    assert set(variants) == set(VARIANT_WIDTHS)
    sizes = {w: Image.open(io.BytesIO(data)).size for w, data in variants.items()}
    assert sizes[320] == (320, 160)
    assert sizes[800] == (800, 400)
    assert sizes[1600] == (1000, 500)  # capped at the source width


def test_variants_reject_undecodable_bytes():
    with pytest.raises(VariantError):
        build_variants(b"\x89PNG\r\n\x1a\n" + b"not really a png")


def test_variant_url_swaps_only_our_own_objects(s3_settings):
    original = f"{BASE}/odos/products/abc123/original.jpg"
    assert media_service.image_variant_url(original, 320) == f"{BASE}/odos/products/abc123/w320.webp"
    cloudinary = "https://res.cloudinary.com/x/image/upload/v1/odos/products/a.jpg"
    assert media_service.image_variant_url(cloudinary, 320) == cloudinary
    assert media_service.image_variant_url(original, 999) == original
    assert media_service.image_variant_url(None, 320) is None


def test_key_from_url_ignores_foreign_hosts(s3_settings):
    assert object_storage.key_from_url(f"{BASE}/odos/a/b/original.png") == "odos/a/b/original.png"
    assert object_storage.key_from_url("https://evil.example/odos/a/b/original.png") is None


def test_delete_prefix_refuses_shallow_or_foreign_prefixes(s3_settings, monkeypatch):
    def boom():
        raise AssertionError("must not reach the bucket")

    monkeypatch.setattr(object_storage, "_client", boom)
    for prefix in ("", "odos/", "odos/products/", "other/a/b/", "odos/products/abc"):
        object_storage.delete_prefix(prefix)
