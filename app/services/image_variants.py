"""Resized copies of an uploaded image.

Cloudinary resized on the fly from a URL. Object storage only serves what was
written, so the sizes are generated once at upload. Clients pick the smallest
variant that covers the slot they are drawing and fall back to the original.
"""

from __future__ import annotations

import io
import threading

from PIL import Image, ImageOps

from app.services.image_validation import MAX_IMAGE_PIXELS

#: Widths in pixels. Chosen for the three slots the apps actually draw:
#: list/avatars (320), cards and detail on phones (800), and full-screen or
#: tablet/web (1600). The untouched original sits above all of these.
VARIANT_WIDTHS: tuple[int, ...] = (320, 800, 1600)

WEBP_QUALITY = 82

# Pillow's own decompression-bomb guard, set to the same ceiling the header
# check enforces so the two can never disagree.
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

# An 80MP decode is hundreds of MB. Bound how many run at once so a burst of
# uploads queues instead of exhausting memory.
_DECODE_SLOTS = threading.BoundedSemaphore(2)


class VariantError(Exception):
    """The bytes passed the header checks but cannot be decoded."""


def build_variants(data: bytes) -> dict[int, bytes]:
    """WebP renditions keyed by width. Never upscales: a source narrower than a
    target gets a same-size copy, so every URL the clients can build exists.
    Output carries no EXIF/GPS metadata and has orientation baked in."""
    with _DECODE_SLOTS:
        try:
            with Image.open(io.BytesIO(data)) as source:
                if source.format == "JPEG":
                    # Lets libjpeg decode at a reduced scale, which is much
                    # cheaper than a full-size decode for a 1600px target.
                    source.draft("RGB", (max(VARIANT_WIDTHS) * 2, max(VARIANT_WIDTHS) * 2))
                image = ImageOps.exif_transpose(source)
                image.load()
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise VariantError("The image could not be decoded.") from exc

        has_alpha = image.mode in ("RGBA", "LA") or "transparency" in image.info
        image = image.convert("RGBA" if has_alpha else "RGB")

        variants: dict[int, bytes] = {}
        for width in VARIANT_WIDTHS:
            target_width = min(width, image.width)
            if target_width == image.width:
                resized = image
            else:
                target_height = max(1, round(image.height * target_width / image.width))
                resized = image.resize((target_width, target_height), Image.LANCZOS)
            buffer = io.BytesIO()
            resized.save(buffer, format="WEBP", quality=WEBP_QUALITY, method=4)
            variants[width] = buffer.getvalue()
        return variants
