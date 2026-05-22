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
