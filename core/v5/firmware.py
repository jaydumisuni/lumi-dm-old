"""Deterministic firmware discovery for technicians.

The catalogue never guesses a firmware match. Provider adapters return evidence
from public official/community sources, and every result keeps its original source
URL so a technician can inspect or copy it before staging the download in Lumi.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import re
import threading
import time
from typing import Any, Callable
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup
import requests


_USER_AGENT = "Lumi-DM-Firmware/1.0 (+https://github.com/jaydumisuni/lumi-dm)"
_TIMEOUT = (8, 20)


@dataclass(slots=True)
class FirmwareProvider:
    id: str
    name: str
    group: str
    brands: list[str]
    description: str
    official: bool
    direct_files: bool
    channels: list[str] = field(default_factory=lambda: ["stable"])
    homepage: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FirmwareDevice:
    id: str
    name: str
    brand: str
    provider: str
    model: str = ""
    codename: str = ""
    supported: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FirmwareResult:
    id: str
    provider: str
    source_name: str
    source_group: str
    official: bool
    brand: str
    device: str
    title: str
    version: str = ""
    build: str = ""
    channel: str = "stable"
    file_type: str = "firmware"
    url: str = ""
    source_url: str = ""
    filename: str = ""
    size: int = 0
    sha256: str = ""
    signed: bool | None = None
    release_date: str = ""
    notes: str = ""
    direct: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _TTLCache:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._values: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, ttl: float, loader: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            value = self._values.get(key)
            if value and now - value[0] <= ttl:
                return value[1]
        loaded = loader()
        with self._lock:
            self._values[key] = (now, loaded)
        return loaded


_CACHE = _TTLCache()


def _session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": _USER_AGENT, "Accept": "application/json,text/html;q=0.9,*/*;q=0.8"})
    return session


def _get_json(url: str) -> Any:
    with _session() as session:
        response = session.get(url, timeout=_TIMEOUT)
        response.raise_for_status()
        return response.json()


def _get_text(url: str) -> str:
    with _session() as session:
        response = session.get(url, timeout=_TIMEOUT)
        response.raise_for_status()
        return response.text


def _date(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc).date().isoformat()
        except (OSError, OverflowError, ValueError):
            return ""
    text = str(value)
    return text[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", text) else text


def _safe_id(*parts: str) -> str:
    raw = "-".join(str(part or "") for part in parts).lower()
    return re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-")[:240]


_PROVIDERS = [
    FirmwareProvider(
        id="apple-ipsw",
        name="Apple IPSW / OTA",
        group="Official OS",
        brands=["Apple"],
        description="Signed and unsigned Apple restore images and OTA packages indexed by IPSW.me.",
        official=False,
        direct_files=True,
        channels=["stable", "beta", "all"],
        homepage="https://ipsw.me",
    ),
    FirmwareProvider(
        id="google-pixel",
        name="Google Pixel Factory / OTA",
        group="Official OS",
        brands=["Google Pixel"],
        description="Factory images, full OTA packages and public Android preview builds from Google.",
        official=True,
        direct_files=True,
        channels=["stable", "beta", "all"],
        homepage="https://developers.google.com/android/images",
    ),
    FirmwareProvider(
        id="lineageos",
        name="LineageOS",
        group="Custom OS",
        brands=["Android"],
        description="Official LineageOS device builds and recovery images.",
        official=True,
        direct_files=True,
        channels=["stable", "nightly", "all"],
        homepage="https://download.lineageos.org",
    ),
    FirmwareProvider(
        id="grapheneos",
        name="GrapheneOS",
        group="Custom OS",
        brands=["Google Pixel"],
        description="Official GrapheneOS factory images and full update packages.",
        official=True,
        direct_files=True,
        channels=["stable", "beta", "all"],
        homepage="https://grapheneos.org/releases",
    ),
    FirmwareProvider(
        id="eos",
        name="/e/OS",
        group="Custom OS",
        brands=["Android"],
        description="Supported-device selector, installation guides and official/community /e/OS builds.",
        official=True,
        direct_files=False,
        channels=["official", "community", "all"],
        homepage="https://wiki.e.foundation/devices",
    ),
    FirmwareProvider(
        id="androidfilehost",
        name="AndroidFileHost",
        group="Community mirrors",
        brands=["Android"],
        description="Community-hosted firmware, recoveries, kernels and custom ROM packages.",
        official=False,
        direct_files=False,
        channels=["all"],
        homepage="https://androidfilehost.com",
    ),
    FirmwareProvider(
        id="needrom",
        name="Needrom",
        group="Community mirrors",
        brands=["Android"],
        description="Community firmware and ROM listings. Technician verification is required.",
        official=False,
        direct_files=False,
        channels=["all"],
        homepage="https://www.needrom.com",
    ),
    FirmwareProvider(
        id="xda",
        name="XDA Forums",
        group="Community knowledge",
        brands=["Android"],
        description="Device forums, maintainer threads, ROM releases and installation evidence.",
        official=False,
        direct_files=False,
        channels=["all"],
        homepage="https://xdaforums.com",
    ),
]


_BRANDS = [
    "Apple", "Samsung", "Google Pixel", "Xiaomi", "Redmi", "POCO",
    "OnePlus", "Oppo", "Realme", "Vivo", "Motorola", "Huawei", "Honor",
    "Tecno", "Infinix", "itel", "Nothing", "Sony", "Asus", "Nokia / HMD",
    "ZTE / Nubia", "Lenovo", "LG", "Meizu", "Android",
]


_PIXEL_DEVICES = [
    ("oriole", "Pixel 6"), ("raven", "Pixel 6 Pro"), ("bluejay", "Pixel 6a"),
    ("panther", "Pixel 7"), ("cheetah", "Pixel 7 Pro"), ("lynx", "Pixel 7a"),
    ("tangorpro", "Pixel Tablet"), ("felix", "Pixel Fold"),
    ("shiba", "Pixel 8"), ("husky", "Pixel 8 Pro"), ("akita", "Pixel 8a"),
    ("tokay", "Pixel 9"), ("caiman", "Pixel 9 Pro"),
    ("komodo", "Pixel 9 Pro XL"), ("comet", "Pixel 9 Pro Fold"),
    ("tegu", "Pixel 9a"), ("frankel", "Pixel 10"),
    ("blazer", "Pixel 10 Pro"), ("mustang", "Pixel 10 Pro XL"),
    ("rango", "Pixel 10 Pro Fold"), ("stallion", "Pixel 10a"),
]


def providers() -> list[dict[str, Any]]:
    return [provider.to_dict() for provider in _PROVIDERS]


def brands() -> list[str]:
    return list(_BRANDS)


def _apple_devices() -> list[FirmwareDevice]:
    def load() -> list[FirmwareDevice]:
        raw = _get_json("https://api.ipsw.me/v4/devices")
        return [
            FirmwareDevice(
                id=str(item.get("identifier") or ""),
                name=str(item.get("name") or item.get("identifier") or "Apple device"),
                brand="Apple",
                provider="apple-ipsw",
                model=str(item.get("model") or ""),
                codename=str(item.get("identifier") or ""),
                metadata={"boardconfig": item.get("boardconfig"), "platform": item.get("platform")},
            )
            for item in list(raw or [])
            if item.get("identifier")
        ]

    return _CACHE.get("apple-devices", 12 * 60 * 60, load)


def _lineage_devices() -> list[FirmwareDevice]:
    def load() -> list[FirmwareDevice]:
        raw = _get_json("https://download.lineageos.org/api/v2/devices")
        values = raw.get("devices", raw) if isinstance(raw, dict) else raw
        result: list[FirmwareDevice] = []
        for item in list(values or []):
            if isinstance(item, str):
                codename, name = item, item
                metadata: dict[str, Any] = {}
            else:
                codename = str(item.get("model") or item.get("device") or item.get("codename") or "")
                name = str(item.get("name") or item.get("model_name") or codename)
                metadata = dict(item)
            if codename:
                result.append(FirmwareDevice(
                    id=codename,
                    name=name,
                    brand=str(metadata.get("oem") or metadata.get("vendor") or "Android"),
                    provider="lineageos",
                    model=str(metadata.get("model") or ""),
                    codename=codename,
                    metadata=metadata,
                ))
        return result

    return _CACHE.get("lineage-devices", 6 * 60 * 60, load)


def list_devices(provider: str = "", query: str = "", brand: str = "") -> list[dict[str, Any]]:
    query_lower = query.strip().lower()
    brand_lower = brand.strip().lower()
    values: list[FirmwareDevice] = []
    try:
        if provider in {"", "apple-ipsw"} and brand_lower in {"", "apple"}:
            values.extend(_apple_devices())
    except Exception:
        pass
    if provider in {"", "google-pixel", "grapheneos"} and brand_lower in {"", "google pixel", "google"}:
        values.extend([
            FirmwareDevice(
                id=codename,
                name=name,
                brand="Google Pixel",
                provider="google-pixel",
                model=name,
                codename=codename,
            )
            for codename, name in _PIXEL_DEVICES
        ])
    if provider in {"", "lineageos"}:
        try:
            values.extend(_lineage_devices())
        except Exception:
            pass

    dedup: dict[tuple[str, str], FirmwareDevice] = {}
    for item in values:
        key = (item.provider, item.id)
        if brand_lower and brand_lower not in item.brand.lower() and brand_lower not in item.name.lower():
            continue
        searchable = " ".join([item.name, item.id, item.model, item.codename, item.brand]).lower()
        if query_lower and query_lower not in searchable:
            continue
        dedup[key] = item
    return [item.to_dict() for item in sorted(dedup.values(), key=lambda value: (value.brand.lower(), value.name.lower()))[:500]]


def _apple_firmware(device: str, channel: str) -> list[FirmwareResult]:
    result: list[FirmwareResult] = []
    kinds = ["ipsw", "ota"] if channel in {"", "all", "beta"} else ["ipsw"]
    for kind in kinds:
        try:
            raw = _get_json(f"https://api.ipsw.me/v4/{kind}/device/{quote_plus(device)}")
        except Exception:
            continue
        values = raw.get("firmwares", raw) if isinstance(raw, dict) else raw
        for item in list(values or []):
            beta = bool(item.get("beta")) or "beta" in str(item.get("version") or "").lower()
            item_channel = "beta" if beta else "stable"
            if channel not in {"", "all"} and channel != item_channel:
                continue
            url = str(item.get("url") or "")
            build = str(item.get("buildid") or item.get("build") or "")
            version = str(item.get("version") or "")
            filename = url.rsplit("/", 1)[-1].split("?", 1)[0] if url else ""
            result.append(FirmwareResult(
                id=_safe_id("apple", device, kind, build or version),
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
                sha256=str(item.get("sha256sum") or item.get("sha256") or ""),
                signed=bool(item.get("signed")) if "signed" in item else None,
                release_date=_date(item.get("releasedate") or item.get("uploaddate")),
                notes="Signing state is reported by the source and should be checked again before flashing.",
                direct=bool(url),
                metadata={"identifier": device, "kind": kind, "md5": item.get("md5sum")},
            ))
    return result


def _parse_google_page(url: str, device: str, channel: str, file_type: str) -> list[FirmwareResult]:
    html = _CACHE.get(f"google-page:{url}", 60 * 60, lambda: _get_text(url))
    soup = BeautifulSoup(html, "html.parser")
    query = device.strip().lower()
    results: list[FirmwareResult] = []
    for link in soup.select("a[href]"):
        href = urljoin(url, str(link.get("href") or ""))
        if not re.search(r"\.(?:zip|tgz)(?:\?|$)", href, re.I):
            continue
        parent = link.find_parent("tr") or link.parent
        text = " ".join(parent.stripped_strings) if parent else link.get_text(" ", strip=True)
        filename = href.rsplit("/", 1)[-1].split("?", 1)[0]
        searchable = f"{text} {filename}".lower()
        if query and query not in searchable:
            codename = next((code for code, name in _PIXEL_DEVICES if query in {code.lower(), name.lower()}), "")
            if not codename or codename.lower() not in searchable:
                continue
        checksum_match = re.search(r"\b[a-f0-9]{64}\b", text, re.I)
        build_match = re.search(r"\b[A-Z]{1,4}\d{1,3}[A-Z]?\.\d{6}\.\d{3}(?:\.[A-Z0-9]+)?\b", text)
        model = next((name for code, name in _PIXEL_DEVICES if code in filename.lower()), device or "Google Pixel")
        results.append(FirmwareResult(
            id=_safe_id("google", filename),
            provider="google-pixel",
            source_name="Google Developers",
            source_group="Official OS",
            official=True,
            brand="Google Pixel",
            device=model,
            title=f"{model} {file_type}",
            version="",
            build=build_match.group(0) if build_match else "",
            channel=channel,
            file_type=file_type,
            url=href,
            source_url=url,
            filename=filename,
            sha256=checksum_match.group(0).lower() if checksum_match else "",
            release_date="",
            notes=text[:500],
            direct=True,
        ))
    return results


def _google_firmware(device: str, channel: str) -> list[FirmwareResult]:
    results: list[FirmwareResult] = []
    if channel in {"", "stable", "all"}:
        for url, kind in [
            ("https://developers.google.com/android/images", "factory image"),
            ("https://developers.google.com/android/ota", "full OTA"),
        ]:
            try:
                results.extend(_parse_google_page(url, device, "stable", kind))
            except Exception:
                continue
    if channel in {"beta", "all"}:
        for url in [
            "https://developer.android.com/about/versions/17/download",
            "https://developer.android.com/about/versions/17/qpr1/download",
            "https://developer.android.com/about/versions/17/qpr2/download",
        ]:
            try:
                results.extend(_parse_google_page(url, device, "beta", "preview factory image"))
            except Exception:
                continue
    return results


def _lineage_firmware(device: str, channel: str) -> list[FirmwareResult]:
    raw = _get_json(f"https://download.lineageos.org/api/v2/devices/{quote_plus(device)}/builds")
    values = raw.get("response", raw.get("builds", raw)) if isinstance(raw, dict) else raw
    results: list[FirmwareResult] = []
    for item in list(values or []):
        build_type = str(item.get("type") or item.get("build_type") or "nightly").lower()
        item_channel = "nightly" if "night" in build_type else "stable"
        if channel not in {"", "all"} and channel != item_channel:
            continue
        url = str(item.get("url") or item.get("download_url") or "")
        filename = str(item.get("filename") or (url.rsplit("/", 1)[-1] if url else ""))
        results.append(FirmwareResult(
            id=_safe_id("lineage", device, filename or item.get("datetime")),
            provider="lineageos",
            source_name="LineageOS",
            source_group="Custom OS",
            official=True,
            brand=str(item.get("oem") or "Android"),
            device=device,
            title=str(item.get("version") or filename or "LineageOS build"),
            version=str(item.get("version") or ""),
            build=str(item.get("build_id") or item.get("id") or ""),
            channel=item_channel,
            file_type=str(item.get("type") or "ROM"),
            url=url,
            source_url=f"https://download.lineageos.org/devices/{device}/builds",
            filename=filename,
            size=int(item.get("size") or 0),
            sha256=str(item.get("sha256") or ""),
            release_date=_date(item.get("datetime") or item.get("date")),
            notes="Official LineageOS build. Read the device installation guide before flashing.",
            direct=bool(url),
            metadata=dict(item),
        ))
    return results


def _graphene_firmware(device: str, channel: str) -> list[FirmwareResult]:
    url = "https://grapheneos.org/releases"
    html = _CACHE.get("graphene-releases", 60 * 60, lambda: _get_text(url))
    soup = BeautifulSoup(html, "html.parser")
    query = device.strip().lower()
    results: list[FirmwareResult] = []
    for link in soup.select("a[href]"):
        href = urljoin(url, str(link.get("href") or ""))
        if not re.search(r"\.(?:zip|tar\.gz)(?:\?|$)", href, re.I):
            continue
        filename = href.rsplit("/", 1)[-1].split("?", 1)[0]
        context = " ".join((link.find_parent(["li", "p", "tr", "section"]) or link.parent).stripped_strings)
        searchable = f"{filename} {context}".lower()
        if query and query not in searchable:
            codename = next((code for code, name in _PIXEL_DEVICES if query in {code.lower(), name.lower()}), "")
            if not codename or codename not in searchable:
                continue
        beta = "beta" in searchable or "alpha" in searchable
        item_channel = "beta" if beta else "stable"
        if channel not in {"", "all"} and channel != item_channel:
            continue
        model = next((name for code, name in _PIXEL_DEVICES if code in searchable), device or "Google Pixel")
        results.append(FirmwareResult(
            id=_safe_id("graphene", filename),
            provider="grapheneos",
            source_name="GrapheneOS",
            source_group="Custom OS",
            official=True,
            brand="Google Pixel",
            device=model,
            title=f"GrapheneOS {model}",
            channel=item_channel,
            file_type="factory/update package",
            url=href,
            source_url=url,
            filename=filename,
            notes=context[:500],
            direct=True,
        ))
    return results


def _search_sources(brand: str, device: str, query: str, provider: str = "") -> list[FirmwareResult]:
    phrase = " ".join(part for part in [brand, device, query, "firmware ROM"] if part).strip()
    encoded = quote_plus(phrase)
    sources = [
        ("androidfilehost", "AndroidFileHost", "Community mirrors", f"https://androidfilehost.com/?w=search&s={encoded}"),
        ("needrom", "Needrom", "Community mirrors", f"https://www.needrom.com/?s={encoded}"),
        ("xda", "XDA Forums", "Community knowledge", f"https://xdaforums.com/search/?q={encoded}"),
        ("eos", "/e/OS device selector", "Custom OS", f"https://wiki.e.foundation/devices?query={quote_plus(device or brand)}"),
    ]
    if brand.lower() in {"samsung"}:
        sources.insert(0, ("samsung-support", "Samsung Support / Smart Switch", "Official OS", "https://www.samsung.com/support/"))
    if brand.lower() in {"xiaomi", "redmi", "poco"}:
        sources.insert(0, ("xiaomi-support", "Xiaomi Support / HyperOS", "Official OS", "https://www.mi.com/global/support/"))
    results: list[FirmwareResult] = []
    for source_id, name, group, url in sources:
        if provider and provider not in {source_id, "all"}:
            continue
        results.append(FirmwareResult(
            id=_safe_id(source_id, phrase),
            provider=source_id,
            source_name=name,
            source_group=group,
            official=group == "Official OS",
            brand=brand or "Android",
            device=device or query or "Device search",
            title=f"Search {name} for {device or query or brand}",
            channel="all",
            file_type="source search",
            url=url,
            source_url=url,
            filename="",
            notes="This source requires technician review before download. Confirm model, region, bootloader and partition requirements.",
            direct=False,
            metadata={"query": phrase},
        ))
    return results


def search_firmware(
    *,
    provider: str = "all",
    brand: str = "",
    device: str = "",
    query: str = "",
    channel: str = "all",
    include_community: bool = True,
) -> list[dict[str, Any]]:
    results: list[FirmwareResult] = []
    selected = provider or "all"
    if selected in {"all", "apple-ipsw"} and (brand.lower() == "apple" or selected == "apple-ipsw") and device:
        try:
            results.extend(_apple_firmware(device, channel))
        except Exception:
            pass
    if selected in {"all", "google-pixel"} and ("pixel" in brand.lower() or selected == "google-pixel"):
        try:
            results.extend(_google_firmware(device or query, channel))
        except Exception:
            pass
    if selected in {"all", "lineageos"} and device:
        try:
            results.extend(_lineage_firmware(device, channel))
        except Exception:
            pass
    if selected in {"all", "grapheneos"} and ("pixel" in brand.lower() or selected == "grapheneos"):
        try:
            results.extend(_graphene_firmware(device or query, channel))
        except Exception:
            pass
    if include_community or selected in {"androidfilehost", "needrom", "xda", "eos"}:
        results.extend(_search_sources(brand, device, query, selected))

    needle = query.strip().lower()
    if needle:
        results = [
            item for item in results
            if needle in " ".join([
                item.title, item.version, item.build, item.filename, item.device,
                item.source_name, item.notes,
            ]).lower()
            or item.file_type == "source search"
        ]
    dedup: dict[str, FirmwareResult] = {}
    for item in results:
        dedup[item.id] = item
    ordered = sorted(
        dedup.values(),
        key=lambda item: (
            0 if item.official else 1,
            0 if item.direct else 1,
            0 if item.signed is True else 1,
            item.release_date or "0000-00-00",
            item.title.lower(),
        ),
        reverse=False,
    )
    return [item.to_dict() for item in ordered[:500]]
