"""Unified source resolution contract for Lumi DM."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import re
from typing import Any, Protocol
from urllib.parse import urlparse

from core import engine as legacy

from .http_transfer import probe_resource
from .models import RequestEnvelope, TaskType


@dataclass(slots=True)
class ResolvedFile:
    id: str
    name: str
    size: int = 0
    role: str = "file"
    content_type: str = ""
    request: RequestEnvelope | None = None
    selected: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, public: bool = True) -> dict[str, Any]:
        value = asdict(self)
        if self.request is not None:
            value["request"] = (
                self.request.redacted_dict()
                if public
                else asdict(self.request)
            )
        return value


@dataclass(slots=True)
class ResolvedResource:
    source_type: str
    title: str
    source_url: str
    files: list[ResolvedFile]
    total_size: int = 0
    range_supported: bool = False
    provider_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, public: bool = True) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "title": self.title,
            "source_url": self.source_url,
            "files": [item.to_dict(public=public) for item in self.files],
            "total_size": self.total_size,
            "range_supported": self.range_supported,
            "provider_id": self.provider_id,
            "metadata": self.metadata,
        }


class Resolver(Protocol):
    id: str

    def accepts(self, envelope: RequestEnvelope) -> bool: ...

    def resolve(self, envelope: RequestEnvelope) -> ResolvedResource: ...


class DirectHTTPResolver:
    id = "direct-http"

    def accepts(self, envelope: RequestEnvelope) -> bool:
        return envelope.url.lower().startswith(("http://", "https://", "ftp://"))

    def resolve(self, envelope: RequestEnvelope) -> ResolvedResource:
        if envelope.url.lower().startswith("ftp://"):
            filename = Path(urlparse(envelope.url).path).name or "download.bin"
            return ResolvedResource(
                source_type=TaskType.FTP.value,
                title=filename,
                source_url=envelope.url,
                files=[
                    ResolvedFile(
                        id="file-0",
                        name=filename,
                        request=envelope,
                    )
                ],
            )
        probe = probe_resource(envelope)
        envelope.final_url = probe.final_url
        envelope.suggested_filename = envelope.suggested_filename or probe.filename
        file = ResolvedFile(
            id="file-0",
            name=envelope.suggested_filename or probe.filename,
            size=probe.total_bytes,
            content_type=probe.content_type,
            request=envelope,
        )
        return ResolvedResource(
            source_type=TaskType.HTTP.value,
            title=file.name,
            source_url=envelope.url,
            files=[file],
            total_size=probe.total_bytes,
            range_supported=probe.range_supported,
            metadata={
                "etag": probe.etag,
                "last_modified": probe.last_modified,
                "final_url": probe.final_url,
            },
        )


class TorrentResolver:
    id = "torrent"

    def accepts(self, envelope: RequestEnvelope) -> bool:
        lowered = envelope.url.lower()
        return lowered.startswith("magnet:") or lowered.endswith(".torrent")

    def resolve(self, envelope: RequestEnvelope) -> ResolvedResource:
        if envelope.url.lower().startswith("magnet:"):
            match = re.search(r"[?&]dn=([^&]+)", envelope.url)
            from urllib.parse import unquote_plus

            title = unquote_plus(match.group(1)) if match else "Magnet download"
        else:
            title = Path(urlparse(envelope.url).path).name or "Torrent download"
        return ResolvedResource(
            source_type=TaskType.TORRENT.value,
            title=title,
            source_url=envelope.url,
            files=[],
            metadata={"metadata_pending": True},
        )


class VideoResolver:
    id = "yt-dlp"
    _HOSTS = re.compile(
        r"youtube\.com|youtu\.be|vimeo\.com|tiktok\.com|twitter\.com|"
        r"x\.com|instagram\.com|dailymotion\.com|twitch\.tv|facebook\.com|"
        r"bilibili\.com|nicovideo\.jp|soundcloud\.com",
        re.I,
    )

    def accepts(self, envelope: RequestEnvelope) -> bool:
        host = urlparse(envelope.url).hostname or ""
        return bool(self._HOSTS.search(host))

    def resolve(self, envelope: RequestEnvelope) -> ResolvedResource:
        info = legacy.get_video_formats(envelope.url)
        title = str(info.get("title") or "Video")
        files: list[ResolvedFile] = []
        for item in list(info.get("formats") or []):
            format_id = str(item.get("format_id") or item.get("id") or "")
            if not format_id:
                continue
            extension = str(item.get("ext") or "mp4")
            label = str(
                item.get("format_note")
                or item.get("resolution")
                or item.get("quality")
                or format_id
            )
            files.append(
                ResolvedFile(
                    id=format_id,
                    name=f"{title} [{label}].{extension}",
                    size=int(item.get("filesize") or item.get("filesize_approx") or 0),
                    role=(
                        "audio"
                        if item.get("vcodec") == "none"
                        else "video"
                    ),
                    metadata={
                        "format_id": format_id,
                        "resolution": item.get("resolution"),
                        "fps": item.get("fps"),
                        "vcodec": item.get("vcodec"),
                        "acodec": item.get("acodec"),
                        "ext": extension,
                    },
                )
            )
        return ResolvedResource(
            source_type=TaskType.VIDEO.value,
            title=title,
            source_url=envelope.url,
            files=files,
            total_size=max((item.size for item in files), default=0),
            provider_id=self.id,
            metadata={
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail"),
                "uploader": info.get("uploader"),
            },
        )


class HLSResolver:
    id = "hls-dash"

    def accepts(self, envelope: RequestEnvelope) -> bool:
        path = urlparse(envelope.url).path.lower()
        return path.endswith(".m3u8") or path.endswith(".mpd")

    def resolve(self, envelope: RequestEnvelope) -> ResolvedResource:
        filename = Path(urlparse(envelope.url).path).name or "stream"
        return ResolvedResource(
            source_type=TaskType.HLS.value,
            title=filename,
            source_url=envelope.url,
            files=[
                ResolvedFile(
                    id="stream-0",
                    name=filename,
                    role="stream",
                    request=envelope,
                )
            ],
            provider_id=self.id,
        )


class ResolverRegistry:
    def __init__(self):
        self._resolvers: list[Resolver] = []

    def register(self, resolver: Resolver, *, first: bool = False) -> None:
        self._resolvers = [item for item in self._resolvers if item.id != resolver.id]
        if first:
            self._resolvers.insert(0, resolver)
        else:
            self._resolvers.append(resolver)

    def resolver_for(self, envelope: RequestEnvelope) -> Resolver:
        for resolver in self._resolvers:
            if resolver.accepts(envelope):
                return resolver
        raise ValueError(f"No resolver supports: {envelope.url}")

    def resolve(self, envelope: RequestEnvelope) -> ResolvedResource:
        return self.resolver_for(envelope).resolve(envelope)

    def ids(self) -> list[str]:
        return [item.id for item in self._resolvers]


def default_registry() -> ResolverRegistry:
    registry = ResolverRegistry()
    registry.register(TorrentResolver())
    registry.register(VideoResolver())
    registry.register(HLSResolver())
    registry.register(DirectHTTPResolver())
    return registry
