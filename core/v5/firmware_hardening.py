"""Final ordering and Apple OTA coverage guarantees for the firmware catalogue."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from . import firmware


_original_search = firmware.search_firmware


def _apple_firmware_complete(
    device: str,
    channel: str,
) -> list[firmware.FirmwareResult]:
    """Return both restore images and OTA packages for every Apple channel.

    Stable searches previously queried only IPSW restore images. OTA packages are
    equally useful to technicians and are filtered by the same stable/beta rule.
    """
    results: list[firmware.FirmwareResult] = []
    for kind in ("ipsw", "ota"):
        try:
            raw = firmware._get_json(
                f"https://api.ipsw.me/v4/{kind}/device/{quote_plus(device)}"
            )
        except Exception:
            continue
        values = raw.get("firmwares", raw) if isinstance(raw, dict) else raw
        for item in list(values or []):
            beta = bool(item.get("beta")) or "beta" in str(
                item.get("version") or ""
            ).lower()
            item_channel = "beta" if beta else "stable"
            if channel not in {"", "all"} and channel != item_channel:
                continue
            url = str(item.get("url") or "")
            build = str(item.get("buildid") or item.get("build") or "")
            version = str(item.get("version") or "")
            filename = url.rsplit("/", 1)[-1].split("?", 1)[0] if url else ""
            results.append(
                firmware.FirmwareResult(
                    id=firmware._safe_id(
                        "apple",
                        device,
                        kind,
                        build or version,
                    ),
                    provider="apple-ipsw",
                    source_name="IPSW.me",
                    source_group="Official OS index",
                    official=False,
                    brand="Apple",
                    device=device,
                    title=f"{version or build} {kind.upper()}",
                    version=version,
                    build=build,
                    channel=item_channel,
                    file_type=kind,
                    url=url,
                    source_url=f"https://ipsw.me/{kind}/{device}",
                    filename=filename,
                    size=int(item.get("filesize") or 0),
                    sha256=str(
                        item.get("sha256sum") or item.get("sha256") or ""
                    ),
                    signed=(
                        bool(item.get("signed"))
                        if "signed" in item
                        else None
                    ),
                    release_date=firmware._date(
                        item.get("releasedate") or item.get("uploaddate")
                    ),
                    notes=(
                        "Signing state is reported by the source and should be "
                        "checked again before flashing."
                    ),
                    direct=bool(url),
                    metadata={
                        "identifier": device,
                        "kind": kind,
                        "md5": item.get("md5sum"),
                    },
                )
            )
    return results


def _search_firmware_newest_first(**kwargs: Any) -> list[dict[str, Any]]:
    values = list(_original_search(**kwargs))

    # Stable sorts preserve the intended hierarchy while making dates newest-first
    # inside each source-quality group.
    values.sort(key=lambda item: str(item.get("title") or "").lower())
    values.sort(
        key=lambda item: str(item.get("release_date") or "0000-00-00"),
        reverse=True,
    )
    values.sort(key=lambda item: 0 if item.get("signed") is True else 1)
    values.sort(key=lambda item: 0 if item.get("direct") else 1)
    values.sort(key=lambda item: 0 if item.get("official") else 1)
    return values[:500]


firmware._apple_firmware = _apple_firmware_complete
firmware.search_firmware = _search_firmware_newest_first
