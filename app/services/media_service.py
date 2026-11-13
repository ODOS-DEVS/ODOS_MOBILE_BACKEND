import asyncio
import hashlib
import logging
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests
from fastapi import HTTPException, UploadFile, status

from app.core.config import settings
from app.services import object_storage
from app.services.image_variants import VARIANT_WIDTHS, VariantError, build_variants
from app.services.image_validation import (
    ImageRejected,
    ensure_safe_image_dimensions,
    safe_upload_filename,
)

logger = logging.getLogger(__name__)

ALLOWED_IMAGE_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

# The client-supplied Content-Type header is just a label — a direct API call
# (bypassing the mobile app entirely) can set it to whatever it likes. Check
# the actual file signature so an upload claiming to be a JPEG can't smuggle
# arbitrary bytes (HTML, SVG-with-script, etc.) past that check.
_IMAGE_MAGIC_BYTES: dict[str, tuple[bytes, ...]] = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),  # WEBP is also checked at offset 8 below
}


def _matches_declared_image_type(content_type: str, file_bytes: bytes) -> bool:
    signatures = _IMAGE_MAGIC_BYTES.get(content_type)
    if not signatures:
        return False
    if not any(file_bytes.startswith(sig) for sig in signatures):
        return False
    if content_type == "image/webp":
        return file_bytes[8:12] == b"WEBP"
    return True

# Server-side backstop — the mobile client caps picks below this, but that's a
# client-side convenience only. Without this, a direct API call (bypassing the app)
# could upload an arbitrarily large file with nothing to stop it.
#
# Raised from 10MB when the clients stopped pre-compressing. A true phone
# original is several megabytes and is now the platform's quality ceiling:
# every delivered size is derived from this file by Cloudinary, so rejecting
# it here would put the old downscaled-and-re-encoded pipeline back.
MAX_IMAGE_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_CHAT_AUDIO_BYTES = 8 * 1024 * 1024
MAX_CHAT_DOCUMENT_BYTES = 15 * 1024 * 1024

# A single request may carry at most this many images. Each one costs a
# Cloudinary round trip and up to MAX_IMAGE_UPLOAD_BYTES of memory.
MAX_IMAGES_PER_REQUEST = 10

_READ_CHUNK_BYTES = 1024 * 1024

_AUDIO_SIGNATURES: tuple[tuple[int, bytes], ...] = (
    (4, b"ftyp"),  # m4a / mp4 / aac in an MP4 container
    (0, b"RIFF"),  # wav
    (0, b"\x1a\x45\xdf\xa3"),  # webm / matroska
    (0, b"ID3"),  # mp3 with tag
    (0, b"\xff\xfb"),  # mp3 frame sync
    (0, b"\xff\xf3"),
    (0, b"\xff\xf1"),  # aac ADTS
    (0, b"\xff\xf9"),
)

_DOCUMENT_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/plain": ".txt",
}

_ZIP_MAGIC = (b"PK\x03\x04",)
_OLE_MAGIC = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",)


def _looks_like_audio(file_bytes: bytes) -> bool:
    return any(file_bytes[offset : offset + len(sig)] == sig for offset, sig in _AUDIO_SIGNATURES)


async def _read_upload_capped(upload: UploadFile, limit: int, too_large_detail: str) -> bytes:
    """Read an upload without ever buffering more than `limit` bytes (+1 chunk)."""
    declared = getattr(upload, "size", None)
    if declared is not None and declared > limit:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=too_large_detail)

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=too_large_detail)
        chunks.append(chunk)
    return b"".join(chunks)


ALLOWED_CHAT_AUDIO_CONTENT_TYPES = {
    "audio/m4a",
    "audio/mp4",
    "audio/x-m4a",
    "audio/aac",
    "audio/wav",
    "audio/webm",
    "audio/mpeg",
}
ALLOWED_CHAT_DOCUMENT_CONTENT_TYPES = {
    "application/pdf": (b"%PDF",),
    "application/msword": _OLE_MAGIC,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": _ZIP_MAGIC,
    "application/vnd.ms-excel": _OLE_MAGIC,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": _ZIP_MAGIC,
    "text/plain": (),  # no signature; checked for NUL bytes instead
}

UPLOAD_TARGETS = {
    "products": {"asset_folder": "odos/products"},
    "stores/logo": {"asset_folder": "odos/stores/logos"},
    "stores/banner": {"asset_folder": "odos/stores/banners"},
    "stores/shop": {"asset_folder": "odos/stores/shop"},
    "categories": {"asset_folder": "odos/categories"},
    "markets": {"asset_folder": "odos/markets"},
    "users/avatars": {"asset_folder": "odos/users/avatars"},
    "vendors/applications": {"asset_folder": "odos/vendors/applications"},
    "vendors/applications/logo": {"asset_folder": "odos/vendors/applications/logo"},
    "vendors/applications/banner": {"asset_folder": "odos/vendors/applications/banner"},
    "vendors/applications/shop": {"asset_folder": "odos/vendors/applications/shop"},
    "chat/attachments": {"asset_folder": "odos/chat/attachments"},
    "guides/videos": {"asset_folder": "odos/guides/videos"},
}

GUIDE_VIDEO_FOLDER = "guides/videos"


def _require_cloudinary_configuration() -> None:
    if settings.cloudinary_is_configured:
        return

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=(
            "Cloudinary is not configured yet. Add the Cloudinary credentials to the backend "
            "environment before uploading images."
        ),
    )


def _require_storage_configuration() -> None:
    if settings.s3_is_configured:
        return
    _require_cloudinary_configuration()


def image_variant_url(url: str | None, width: int) -> str | None:
    """URL of the `width`-pixel rendition of an image stored in object storage.

    Falls back to the URL itself for anything we did not generate variants for
    (Cloudinary URLs, external avatars, legacy rows), so callers can apply it
    to any image URL unconditionally."""
    if not url or width not in VARIANT_WIDTHS:
        return url
    if object_storage.key_from_url(url) is None or "/original." not in url:
        return url
    head, _, _ = url.rpartition("/original.")
    return f"{head}/w{width}.webp"


def _store_image_set(
    file_bytes: bytes, content_type: str, extension: str, asset_folder: str
) -> str:
    """Write the original plus resized WebP variants. Blocking; run in a thread.

    The original is stored untouched so nothing ever has to be re-derived from
    a lossy copy. Everything lands under one uuid directory so a delete is a
    single prefix removal and a half-finished upload can be cleaned up."""
    variants = build_variants(file_bytes)
    prefix = f"{asset_folder}/{uuid.uuid4().hex}/"
    original_key = f"{prefix}original{extension}"
    try:
        object_storage.put_object(original_key, file_bytes, content_type)
        for width, data in variants.items():
            object_storage.put_object(f"{prefix}w{width}.webp", data, "image/webp")
    except Exception:
        logger.exception("Object storage upload failed for %s", prefix)
        try:
            object_storage.delete_prefix(prefix)
        except Exception:
            logger.warning("Could not clean up partial upload %s", prefix, exc_info=True)
        raise
    return object_storage.public_url(original_key)


def _cloudinary_upload_url() -> str:
    return (
        f"https://api.cloudinary.com/v1_1/"
        f"{settings.cloudinary_cloud_name.strip()}/image/upload"
    )


def _cloudinary_destroy_url() -> str:
    return (
        f"https://api.cloudinary.com/v1_1/"
        f"{settings.cloudinary_cloud_name.strip()}/image/destroy"
    )


def _resolve_target(folder: str) -> dict[str, str]:
    target = UPLOAD_TARGETS.get(folder)
    if target:
        return target

    normalized_folder = folder.strip().strip("/")
    return {
        "asset_folder": f"odos/{normalized_folder}",
    }


def _sign_cloudinary_payload(payload: dict[str, str]) -> str:
    filtered_items = {
        key: value
        for key, value in payload.items()
        if value not in (None, "", [], {})
        and key not in {"file", "api_key", "resource_type", "signature"}
    }
    payload_to_sign = "&".join(
        f"{key}={filtered_items[key]}" for key in sorted(filtered_items)
    )
    return hashlib.sha1(
        f"{payload_to_sign}{settings.cloudinary_api_secret}".encode()
    ).hexdigest()


def _cloudinary_public_id_from_url(file_url: str | None) -> str | None:
    if not file_url or "res.cloudinary.com" not in file_url:
        return None

    parsed_url = urlparse(file_url)
    path_parts = [segment for segment in parsed_url.path.split("/") if segment]
    if "upload" not in path_parts:
        return None

    upload_index = path_parts.index("upload")
    public_segments = path_parts[upload_index + 1 :]
    if not public_segments:
        return None

    while public_segments and (
        public_segments[0].startswith("v") and public_segments[0][1:].isdigit()
    ):
        public_segments = public_segments[1:]

    if not public_segments:
        return None

    public_id_with_extension = "/".join(public_segments)
    public_id = str(Path(public_id_with_extension).with_suffix(""))
    return public_id or None


def _cloudinary_request_error(response: requests.Response) -> HTTPException:
    try:
        payload = response.json()
        detail = payload.get("error", {}).get("message") or payload.get("message")
    except ValueError:
        detail = response.text.strip() or "Cloudinary request failed."

    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Cloudinary upload failed: {detail}",
    )


async def save_image_upload(upload: UploadFile, folder: str) -> str:
    _require_storage_configuration()

    content_type = upload.content_type or ""
    extension = ALLOWED_IMAGE_CONTENT_TYPES.get(content_type)
    if not extension:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only JPG, PNG, and WEBP images are supported.",
        )

    file_bytes = await _read_upload_capped(
        upload,
        MAX_IMAGE_UPLOAD_BYTES,
        f"Images must be {MAX_IMAGE_UPLOAD_BYTES // (1024 * 1024)}MB or smaller.",
    )
    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded image was empty.",
        )
    if not _matches_declared_image_type(content_type, file_bytes):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That file doesn't look like a valid image. Try a different file.",
        )
    # Byte size alone cannot catch a decompression bomb: a 30000x30000 PNG of
    # flat colour is a few hundred kilobytes on the wire and gigabytes once
    # decoded. Dimensions are read from the header, before any decoder sees it.
    try:
        ensure_safe_image_dimensions(content_type, file_bytes)
    except ImageRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    target = _resolve_target(folder)

    if settings.s3_is_configured:
        try:
            return await asyncio.to_thread(
                _store_image_set, file_bytes, content_type, extension, target["asset_folder"]
            )
        except VariantError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That image appears to be corrupt. Try exporting it again.",
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="We couldn't store that image right now. Please try again.",
            ) from exc

    timestamp = str(int(time.time()))
    upload_payload = {
        "timestamp": timestamp,
        "folder": target["asset_folder"],
    }

    response = await asyncio.to_thread(
        requests.post,
        _cloudinary_upload_url(),
        data={
            **upload_payload,
            "api_key": settings.cloudinary_api_key,
            "signature": _sign_cloudinary_payload(upload_payload),
        },
        files={
            "file": (
                safe_upload_filename(upload.filename, extension),
                file_bytes,
                content_type,
            )
        },
        timeout=30,
    )

    if not response.ok:
        raise _cloudinary_request_error(response)

    payload = response.json()
    secure_url = payload.get("secure_url")
    if not secure_url:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cloudinary did not return a secure image URL.",
        )

    return str(secure_url)


async def save_image_uploads(uploads: list[UploadFile] | None, *, folder: str) -> list[str]:
    if not uploads:
        return []
    if len(uploads) > MAX_IMAGES_PER_REQUEST:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You can upload at most {MAX_IMAGES_PER_REQUEST} images at a time.",
        )

    image_urls: list[str] = []
    for upload in uploads:
        image_urls.append(await save_image_upload(upload, folder=folder))
    return image_urls


async def _upload_raw_to_cloudinary(
    file_bytes: bytes,
    filename: str,
    content_type: str,
    *,
    resource_type: str,
    folder: str,
) -> str:
    _require_storage_configuration()

    target = _resolve_target(folder)

    if settings.s3_is_configured:
        key = f"{target['asset_folder']}/{uuid.uuid4().hex}/{filename}"
        # Documents are downloads, never rendered in the browser: a public
        # bucket serving user-supplied text/PDF inline is an XSS surface.
        disposition = f'attachment; filename="{filename}"' if resource_type == "raw" else None
        try:
            await asyncio.to_thread(
                object_storage.put_object,
                key,
                file_bytes,
                content_type,
                content_disposition=disposition,
            )
        except Exception as exc:
            logger.exception("Object storage upload failed for %s", key)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="We couldn't store that file right now. Please try again.",
            ) from exc
        return object_storage.public_url(key)

    timestamp = str(int(time.time()))
    upload_payload = {
        "timestamp": timestamp,
        "folder": target["asset_folder"],
    }

    response = await asyncio.to_thread(
        requests.post,
        f"https://api.cloudinary.com/v1_1/{settings.cloudinary_cloud_name.strip()}/{resource_type}/upload",
        data={
            **upload_payload,
            "api_key": settings.cloudinary_api_key,
            "signature": _sign_cloudinary_payload(upload_payload),
        },
        files={"file": (filename, file_bytes, content_type)},
        timeout=30,
    )

    if not response.ok:
        raise _cloudinary_request_error(response)

    payload = response.json()
    secure_url = payload.get("secure_url")
    if not secure_url:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cloudinary did not return a secure file URL.",
        )
    return str(secure_url)


async def save_chat_attachment_upload(upload: UploadFile) -> tuple[str, str, str | None]:
    """Upload a chat attachment (image, voice note, or document) and
    classify it. Returns (secure_url, attachment_type, original_filename).

    Images are routed through the already-audited image path (magic-byte
    verified against the declared type). Audio and documents get a
    declared-content-type allowlist, a size cap, and — for documents where a
    signature is well known (PDF) — a magic-byte check too."""
    content_type = (upload.content_type or "").lower()
    folder = "chat/attachments"

    if content_type in ALLOWED_IMAGE_CONTENT_TYPES:
        url = await save_image_upload(upload, folder=folder)
        return url, "image", None

    if content_type in ALLOWED_CHAT_AUDIO_CONTENT_TYPES:
        file_bytes = await _read_upload_capped(
            upload,
            MAX_CHAT_AUDIO_BYTES,
            f"Voice notes must be {MAX_CHAT_AUDIO_BYTES // (1024 * 1024)}MB or smaller.",
        )
        if not file_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That voice note was empty.",
            )
        if not _looks_like_audio(file_bytes):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That doesn't look like a valid voice note. Try recording again.",
            )
        url = await _upload_raw_to_cloudinary(
            file_bytes,
            safe_upload_filename(upload.filename, ".m4a"),
            content_type,
            resource_type="video",
            folder=folder,
        )
        return url, "audio", None

    if content_type in ALLOWED_CHAT_DOCUMENT_CONTENT_TYPES:
        signatures = ALLOWED_CHAT_DOCUMENT_CONTENT_TYPES[content_type]
        file_bytes = await _read_upload_capped(
            upload,
            MAX_CHAT_DOCUMENT_BYTES,
            f"Files must be {MAX_CHAT_DOCUMENT_BYTES // (1024 * 1024)}MB or smaller.",
        )
        if not file_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That file was empty.",
            )
        if signatures:
            valid = any(file_bytes.startswith(sig) for sig in signatures)
        else:
            valid = b"\x00" not in file_bytes[:8192]
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That file doesn't look valid. Try a different file.",
            )
        safe_name = safe_upload_filename(
            upload.filename, _DOCUMENT_EXTENSIONS.get(content_type, "")
        )
        url = await _upload_raw_to_cloudinary(
            file_bytes,
            safe_name,
            content_type,
            resource_type="raw",
            folder=folder,
        )
        return url, "file", safe_name

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="That file type isn't supported yet.",
    )


def normalize_remote_avatar_url(url: str | None) -> str | None:
    if not url or not isinstance(url, str):
        return None

    cleaned = url.strip()
    if not cleaned.startswith("http://") and not cleaned.startswith("https://"):
        return None

    if "googleusercontent.com" in cleaned and "=s" in cleaned:
        base = cleaned.split("=s", 1)[0]
        return f"{base}=s256-c"

    return cleaned


def is_google_avatar_url(url: str | None) -> bool:
    return bool(url and "googleusercontent.com" in url)


def is_managed_avatar_url(url: str | None) -> bool:
    if not url:
        return False
    cleaned = url.strip()
    return (
        "res.cloudinary.com" in cleaned
        or cleaned.startswith("/uploads")
        or object_storage.key_from_url(cleaned) is not None
    )


def import_avatar_from_url(source_url: str | None) -> str | None:
    normalized = normalize_remote_avatar_url(source_url)
    if not normalized:
        return None

    if not (settings.s3_is_configured or settings.cloudinary_is_configured):
        return normalized[:500]

    try:
        with requests.get(normalized, timeout=12, stream=True) as download:
            download.raise_for_status()
            content_type = (
                (download.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
            )
            downloaded = bytearray()
            for chunk in download.iter_content(chunk_size=64 * 1024):
                downloaded.extend(chunk)
                if len(downloaded) > MAX_IMAGE_UPLOAD_BYTES:
                    return normalized[:500]
    except requests.RequestException:
        return normalized[:500]

    extension = ALLOWED_IMAGE_CONTENT_TYPES.get(content_type)
    image_bytes = bytes(downloaded)
    if not extension or not _matches_declared_image_type(content_type, image_bytes):
        return normalized[:500]
    try:
        ensure_safe_image_dimensions(content_type, image_bytes)
    except ImageRejected:
        return normalized[:500]

    if settings.s3_is_configured:
        try:
            return _store_image_set(
                image_bytes, content_type, extension, _resolve_target("users/avatars")["asset_folder"]
            )
        except Exception:
            return normalized[:500]

    target = _resolve_target("users/avatars")
    timestamp = str(int(time.time()))
    upload_payload = {
        "timestamp": timestamp,
        "folder": target["asset_folder"],
    }

    try:
        response = requests.post(
            _cloudinary_upload_url(),
            data={
                **upload_payload,
                "api_key": settings.cloudinary_api_key,
                "signature": _sign_cloudinary_payload(upload_payload),
            },
            files={
                "file": (
                    f"google-avatar{extension}",
                    image_bytes,
                    content_type,
                )
            },
            timeout=30,
        )
    except requests.RequestException:
        return normalized[:500]

    if not response.ok:
        return normalized[:500]

    payload = response.json()
    secure_url = payload.get("secure_url")
    if not secure_url:
        return normalized[:500]

    return str(secure_url)


def remove_media_file(file_url: str | None, resource_type: str = "image") -> None:
    """Destroy an uploaded asset. Pass the resource type it was uploaded as:
    chat voice notes are "video" and documents are "raw"."""
    stored_key = object_storage.key_from_url(file_url)
    if stored_key is not None:
        directory, separator, _ = stored_key.rpartition("/")
        if separator:
            try:
                object_storage.delete_prefix(f"{directory}/")
            except Exception:
                logger.warning("Object storage delete failed for %s", stored_key, exc_info=True)
        return

    public_id = _cloudinary_public_id_from_url(file_url)
    if not public_id:
        return

    _require_cloudinary_configuration()

    timestamp = str(int(time.time()))
    destroy_payload = {
        "public_id": public_id,
        "timestamp": timestamp,
    }

    try:
        response = requests.post(
            _cloudinary_destroy_url().replace("/image/destroy", f"/{resource_type}/destroy"),
            data={
                **destroy_payload,
                "api_key": settings.cloudinary_api_key,
                "signature": _sign_cloudinary_payload(destroy_payload),
            },
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException:
        # Never fail the caller over a leaked remote asset, but leave a trail.
        logger.warning("Cloudinary destroy failed for %s", public_id, exc_info=True)
        return


def build_video_upload_signature(folder: str = GUIDE_VIDEO_FOLDER) -> dict[str, str | int]:
    """Credentials for a browser to upload one video straight to Cloudinary.

    Tutorial videos are tens of megabytes. Streaming them through this API on
    the way to Cloudinary would hold a worker open for the whole transfer and
    buffer the file in memory, so the admin uploads directly and only tells us
    the resulting URL. The API secret stays here — what leaves is a signature
    over a fixed folder and a timestamp, which Cloudinary rejects once stale.
    """
    _require_cloudinary_configuration()

    target = _resolve_target(folder)
    timestamp = int(time.time())
    payload = {
        "timestamp": str(timestamp),
        "folder": target["asset_folder"],
    }

    return {
        "cloud_name": settings.cloudinary_cloud_name.strip(),
        "api_key": settings.cloudinary_api_key,
        "timestamp": timestamp,
        "folder": target["asset_folder"],
        "signature": _sign_cloudinary_payload(payload),
        "upload_url": (
            f"https://api.cloudinary.com/v1_1/"
            f"{settings.cloudinary_cloud_name.strip()}/video/upload"
        ),
    }


def remove_video_file(file_url: str | None, public_id: str | None = None) -> None:
    """Destroy an uploaded video. Cloudinary files uploaded as `video` must be
    destroyed on the video endpoint — calling the image one reports success
    while leaving the asset (and its storage bill) in place."""
    if not settings.cloudinary_is_configured:
        return

    resolved_id = public_id or _cloudinary_public_id_from_url(file_url)
    if not resolved_id:
        return

    timestamp = str(int(time.time()))
    destroy_payload = {
        "public_id": resolved_id,
        "timestamp": timestamp,
    }

    try:
        response = requests.post(
            f"https://api.cloudinary.com/v1_1/"
            f"{settings.cloudinary_cloud_name.strip()}/video/destroy",
            data={
                **destroy_payload,
                "api_key": settings.cloudinary_api_key,
                "signature": _sign_cloudinary_payload(destroy_payload),
            },
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException:
        # Losing the remote asset is a cleanup problem, never a reason to fail
        # the admin's delete — the row is what the app reads.
        return
