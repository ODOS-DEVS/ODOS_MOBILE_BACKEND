"""Structural checks on an uploaded image, done from its header alone.

Nothing here decodes pixels. That is the point: a decompression bomb is
dangerous precisely *because* decoding it is expensive, so the dimensions
are read out of the file's own header and judged before any decoder —
ours or Cloudinary's — is handed the bytes.

A 30000x30000 PNG of flat colour compresses to a few hundred kilobytes and
sails through a byte-size limit, then wants ~3.6 GB of RAM to decode. The
byte cap alone cannot see that coming; the pixel cap can.
"""

from __future__ import annotations

import re
import struct
from pathlib import PurePosixPath

#: No real photograph is wider than this. An 8K frame is 7,680px and a 200MP
#: phone sensor is about 16,000 on its long edge only in raw modes nothing
#: uploads here, so 12,000 leaves generous headroom over genuine content
#: while refusing the shapes only a bomb has.
MAX_IMAGE_DIMENSION = 12_000

#: Total pixels, which is what actually drives decode memory. 80MP covers any
#: real camera (a 50MP phone photo is ~8160x6120) and caps a decode at roughly
#: 320MB of RGBA rather than the multiple gigabytes an unbounded one could ask
#: for.
MAX_IMAGE_PIXELS = 80_000_000

#: Below this an "image" is a tracking pixel or a corrupt header, never a
#: product photo, an avatar or a banner.
MIN_IMAGE_DIMENSION = 16


class ImageRejected(Exception):
    """Raised with a message intended for the person uploading."""


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    # Walk the marker segments to the frame header. Entropy-coded data is never
    # entered, so this stays O(number of segments) rather than O(file size).
    index = 2
    length = len(data)
    while index < length - 9:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        # SOF0..SOF15, excluding the non-frame DHT/JPG/DAC markers.
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack(">HH", data[index + 5 : index + 9])
            return width, height
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        if index + 4 > length:
            break
        segment_length = struct.unpack(">H", data[index + 2 : index + 4])[0]
        if segment_length < 2:
            break
        index += 2 + segment_length
    return None


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    # IHDR is mandated to be the first chunk, so width/height sit at a fixed
    # offset in every valid PNG.
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30:
        return None
    chunk = data[12:16]
    if chunk == b"VP8X":
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    if chunk == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8 ":
        width = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        height = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        return width, height
    return None


def read_image_dimensions(content_type: str, data: bytes) -> tuple[int, int] | None:
    """Width and height from the header, or None if they cannot be read."""
    try:
        if content_type == "image/jpeg":
            return _jpeg_dimensions(data)
        if content_type == "image/png":
            return _png_dimensions(data)
        if content_type == "image/webp":
            return _webp_dimensions(data)
    except (struct.error, IndexError, ValueError):
        return None
    return None


def ensure_safe_image_dimensions(content_type: str, data: bytes) -> tuple[int, int]:
    """Reject bombs and degenerate files. Returns the dimensions on success.

    An unreadable header is refused rather than waved through: the magic
    bytes already said this is a JPEG/PNG/WebP, so a header we cannot parse
    means the file is malformed or deliberately crafted, and neither should
    reach a decoder.
    """
    dimensions = read_image_dimensions(content_type, data)
    if dimensions is None:
        raise ImageRejected("That image appears to be corrupt. Try exporting it again.")

    width, height = dimensions
    if width <= 0 or height <= 0:
        raise ImageRejected("That image appears to be corrupt. Try exporting it again.")

    # Oversize is checked before undersize on purpose. A 60000x10 file trips
    # both tests, and it is a bomb, not a thumbnail — naming it "too small"
    # would describe the least important thing about it.
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise ImageRejected(
            f"That image is {width}x{height}. The longest side must be "
            f"{MAX_IMAGE_DIMENSION:,} pixels or less."
        )

    if width * height > MAX_IMAGE_PIXELS:
        raise ImageRejected(
            f"That image is {width * height / 1_000_000:.0f} megapixels. "
            f"Images must be under {MAX_IMAGE_PIXELS // 1_000_000} megapixels."
        )

    if width < MIN_IMAGE_DIMENSION or height < MIN_IMAGE_DIMENSION:
        raise ImageRejected(
            f"That image is too small — it must be at least "
            f"{MIN_IMAGE_DIMENSION}x{MIN_IMAGE_DIMENSION} pixels."
        )

    return width, height


_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_upload_filename(filename: str | None, extension: str) -> str:
    """A filename safe to hand to a third party.

    The client controls this string completely. Path separators are stripped
    first (so `../../etc/passwd` cannot survive as a path), then anything
    outside a conservative character set, and the result is length-capped and
    forced to the extension matching the verified content type — so the name
    can never disagree with what the magic bytes proved the file to be.
    """
    base = PurePosixPath((filename or "").replace("\\", "/")).name
    stem = PurePosixPath(base).stem if base else ""
    # Every dot goes, not just the final suffix: `PurePosixPath.stem` leaves
    # "shell.php" intact for "shell.php.jpg", and a name carrying a second
    # extension is the kind of thing some server somewhere will act on. The
    # extension is appended by us, from the verified content type.
    cleaned = _UNSAFE_FILENAME.sub("-", stem.replace(".", "-")).strip("-._")[:80]
    return f"{cleaned or 'upload'}{extension}"
