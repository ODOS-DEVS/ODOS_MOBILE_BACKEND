"""Upload hardening: dimensions read from the header, filenames neutralised.

The bomb test is the one that matters. It builds a real PNG header claiming
30000x30000 — the shape of file that slips past a byte-size limit and then
asks a decoder for gigabytes of memory.
"""

from __future__ import annotations

import struct
import zlib

import pytest

from app.services.image_validation import (
    MAX_IMAGE_DIMENSION,
    ImageRejected,
    ensure_safe_image_dimensions,
    read_image_dimensions,
    safe_upload_filename,
)


def png_bytes(width: int, height: int) -> bytes:
    """A structurally valid PNG header declaring the given size."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr
    chunk += struct.pack(">I", zlib.crc32(b"IHDR" + ihdr) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk


def jpeg_bytes(width: int, height: int) -> bytes:
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, height, width, 3) + b"\x00" * 9
    return b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9 + sof


def webp_bytes(width: int, height: int) -> bytes:
    body = b"VP8X" + struct.pack("<I", 10) + b"\x00" * 4
    body += (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


class TestDimensionReading:
    @pytest.mark.parametrize(
        "content_type,builder",
        [("image/png", png_bytes), ("image/jpeg", jpeg_bytes), ("image/webp", webp_bytes)],
    )
    def test_dimensions_are_read_from_every_supported_format(self, content_type, builder):
        assert read_image_dimensions(content_type, builder(1280, 960)) == (1280, 960)

    def test_an_unreadable_header_reads_as_unknown(self):
        assert read_image_dimensions("image/png", b"\x89PNG\r\n\x1a\nnope") is None


class TestDecompressionBombs:
    def test_a_thirty_thousand_pixel_png_is_refused(self):
        """The classic bomb: trivial on the wire, gigabytes once decoded."""
        bomb = png_bytes(30_000, 30_000)
        assert len(bomb) < 100, "the header alone is tiny — that is the whole problem"
        with pytest.raises(ImageRejected, match="longest side"):
            ensure_safe_image_dimensions("image/png", bomb)

    def test_a_long_thin_bomb_is_refused_on_its_long_edge(self):
        with pytest.raises(ImageRejected, match="longest side"):
            ensure_safe_image_dimensions("image/png", png_bytes(60_000, 10))

    def test_a_megapixel_bomb_inside_the_dimension_cap_is_still_refused(self):
        """Both sides legal, the product is not — this is why there are two caps."""
        with pytest.raises(ImageRejected, match="megapixels"):
            ensure_safe_image_dimensions("image/png", png_bytes(11_000, 11_000))

    def test_a_corrupt_header_is_refused_rather_than_passed_to_a_decoder(self):
        with pytest.raises(ImageRejected, match="corrupt"):
            ensure_safe_image_dimensions("image/png", b"\x89PNG\r\n\x1a\ngarbage")

    def test_zero_dimensions_are_refused(self):
        with pytest.raises(ImageRejected, match="corrupt"):
            ensure_safe_image_dimensions("image/png", png_bytes(0, 0))


class TestLegitimateImages:
    @pytest.mark.parametrize("w,h", [(1201, 1203), (4032, 3024), (8160, 6120), (640, 640)])
    def test_real_camera_and_catalogue_sizes_pass(self, w, h):
        assert ensure_safe_image_dimensions("image/png", png_bytes(w, h)) == (w, h)

    def test_a_tracking_pixel_is_refused(self):
        with pytest.raises(ImageRejected, match="too small"):
            ensure_safe_image_dimensions("image/png", png_bytes(1, 1))

    def test_the_boundary_itself_is_allowed(self):
        assert ensure_safe_image_dimensions(
            "image/png", png_bytes(MAX_IMAGE_DIMENSION, 100)
        ) == (MAX_IMAGE_DIMENSION, 100)


class TestFilenameSafety:
    def test_traversal_cannot_survive(self):
        assert "/" not in safe_upload_filename("../../../etc/passwd", ".jpg")
        assert ".." not in safe_upload_filename("../../../etc/passwd", ".jpg")

    def test_windows_separators_are_handled_too(self):
        assert "\\" not in safe_upload_filename(r"..\..\windows\system32\a", ".png")

    def test_the_extension_always_matches_the_verified_type(self):
        """Bytes proven to be a PNG get a .png name, whatever was claimed."""
        assert safe_upload_filename("shell.php", ".png").endswith(".png")

    def test_no_second_extension_survives(self):
        """"shell.php.jpg" must not keep a `.php` any server might act on.
        The letters themselves are harmless; a dot in front of them is not."""
        out = safe_upload_filename("shell.php.jpg", ".png")
        assert out.count(".") == 1 and out.endswith(".png")
        assert ".php" not in out

    def test_exotic_characters_are_stripped(self):
        out = safe_upload_filename("pro<duct>;rm -rf *.jpg", ".jpg")
        assert all(c.isalnum() or c in "._-" for c in out)

    def test_an_absent_or_empty_name_still_yields_something_usable(self):
        assert safe_upload_filename(None, ".jpg") == "upload.jpg"
        assert safe_upload_filename("   ", ".jpg").endswith(".jpg")

    def test_absurdly_long_names_are_capped(self):
        assert len(safe_upload_filename("a" * 5000, ".jpg")) <= 84
