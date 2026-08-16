import hashlib
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from fastapi import HTTPException, UploadFile, status

from app.core.config import settings

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

# Server-side backstop — the mobile client already caps picks at 8MB, but that's a
# client-side convenience only. Without this, a direct API call (bypassing the app)
# could upload an arbitrarily large file with nothing to stop it.
MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_CHAT_AUDIO_BYTES = 8 * 1024 * 1024
MAX_CHAT_DOCUMENT_BYTES = 15 * 1024 * 1024

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
    "application/msword": (),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (),
    "application/vnd.ms-excel": (),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (),
    "text/plain": (),
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
}


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
        f"{payload_to_sign}{settings.cloudinary_api_secret}".encode("utf-8")
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
    _require_cloudinary_configuration()

    content_type = upload.content_type or ""
    extension = ALLOWED_IMAGE_CONTENT_TYPES.get(content_type)
    if not extension:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only JPG, PNG, and WEBP images are supported.",
        )

    file_bytes = await upload.read()
    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded image was empty.",
        )
    if len(file_bytes) > MAX_IMAGE_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Images must be {MAX_IMAGE_UPLOAD_BYTES // (1024 * 1024)}MB or smaller.",
        )
    if not _matches_declared_image_type(content_type, file_bytes):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That file doesn't look like a valid image. Try a different file.",
        )

    target = _resolve_target(folder)
    timestamp = str(int(time.time()))
    upload_payload = {
        "timestamp": timestamp,
        "folder": target["asset_folder"],
    }

    response = requests.post(
        _cloudinary_upload_url(),
        data={
            **upload_payload,
            "api_key": settings.cloudinary_api_key,
            "signature": _sign_cloudinary_payload(upload_payload),
        },
        files={
            "file": (
                upload.filename or f"upload{extension}",
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
    _require_cloudinary_configuration()

    target = _resolve_target(folder)
    timestamp = str(int(time.time()))
    upload_payload = {
        "timestamp": timestamp,
        "folder": target["asset_folder"],
    }

    response = requests.post(
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
        file_bytes = await upload.read()
        if not file_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That voice note was empty.",
            )
        if len(file_bytes) > MAX_CHAT_AUDIO_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Voice notes must be {MAX_CHAT_AUDIO_BYTES // (1024 * 1024)}MB or smaller.",
            )
        url = await _upload_raw_to_cloudinary(
            file_bytes,
            upload.filename or "voice-note.m4a",
            content_type,
            resource_type="video",
            folder=folder,
        )
        return url, "audio", None

    if content_type in ALLOWED_CHAT_DOCUMENT_CONTENT_TYPES:
        signatures = ALLOWED_CHAT_DOCUMENT_CONTENT_TYPES[content_type]
        file_bytes = await upload.read()
        if not file_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That file was empty.",
            )
        if len(file_bytes) > MAX_CHAT_DOCUMENT_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Files must be {MAX_CHAT_DOCUMENT_BYTES // (1024 * 1024)}MB or smaller.",
            )
        if signatures and not any(file_bytes.startswith(sig) for sig in signatures):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That file doesn't look valid. Try a different file.",
            )
        url = await _upload_raw_to_cloudinary(
            file_bytes,
            upload.filename or "file",
            content_type,
            resource_type="raw",
            folder=folder,
        )
        return url, "file", upload.filename

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
    return "res.cloudinary.com" in cleaned or cleaned.startswith("/uploads")


def import_avatar_from_url(source_url: str | None) -> str | None:
    normalized = normalize_remote_avatar_url(source_url)
    if not normalized:
        return None

    if not settings.cloudinary_is_configured:
        return normalized[:500]

    try:
        download = requests.get(normalized, timeout=12)
        download.raise_for_status()
    except requests.RequestException:
        return normalized[:500]

    content_type = (download.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
    extension = ALLOWED_IMAGE_CONTENT_TYPES.get(content_type, ".jpg")

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
                    download.content,
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


def remove_media_file(file_url: str | None) -> None:
    _require_cloudinary_configuration()

    public_id = _cloudinary_public_id_from_url(file_url)
    if not public_id:
        return

    timestamp = str(int(time.time()))
    destroy_payload = {
        "public_id": public_id,
        "timestamp": timestamp,
    }

    try:
        response = requests.post(
            _cloudinary_destroy_url(),
            data={
                **destroy_payload,
                "api_key": settings.cloudinary_api_key,
                "signature": _sign_cloudinary_payload(destroy_payload),
            },
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException:
        return
