"""Download categories and automatic storage-placement rules."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .store import StateStore


_DEFAULT_CATEGORIES = [
    {
        "id": "compressed",
        "name": "Compressed",
        "extensions": ["zip", "rar", "7z", "tar", "gz", "bz2", "xz", "zst"],
        "domains": [],
        "folder": "Compressed",
        "auto_extract": False,
    },
    {
        "id": "documents",
        "name": "Documents",
        "extensions": ["pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "epub", "mobi"],
        "domains": [],
        "folder": "Documents",
        "auto_extract": False,
    },
    {
        "id": "music",
        "name": "Music",
        "extensions": ["mp3", "flac", "wav", "aac", "ogg", "opus", "m4a"],
        "domains": [],
        "folder": "Music",
        "auto_extract": False,
    },
    {
        "id": "programs",
        "name": "Programs",
        "extensions": ["exe", "msi", "apk", "ipa", "appx", "dmg", "pkg", "deb", "rpm"],
        "domains": [],
        "folder": "Programs",
        "auto_extract": False,
    },
    {
        "id": "video",
        "name": "Video",
        "extensions": ["mp4", "mkv", "avi", "mov", "wmv", "webm", "flv", "m2ts", "ts"],
        "domains": [],
        "folder": "Video",
        "auto_extract": False,
    },
    {
        "id": "disk-images",
        "name": "Disk Images",
        "extensions": ["iso", "img", "vhd", "vhdx", "dmg"],
        "domains": [],
        "folder": "Disk Images",
        "auto_extract": False,
    },
    {
        "id": "other",
        "name": "Other",
        "extensions": [],
        "domains": [],
        "folder": "Other",
        "auto_extract": False,
    },
]


@dataclass(slots=True)
class CategoryRule:
    id: str
    name: str
    extensions: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    folder: str = ""
    temp_folder: str = ""
    auto_extract: bool = False
    completion_action: str = "none"
    enabled: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CategoryRule":
        return cls(
            id=str(value["id"]),
            name=str(value.get("name") or value["id"]),
            extensions=[
                str(item).lower().lstrip(".")
                for item in list(value.get("extensions") or [])
            ],
            domains=[str(item).lower() for item in list(value.get("domains") or [])],
            folder=str(value.get("folder") or value.get("name") or value["id"]),
            temp_folder=str(value.get("temp_folder") or ""),
            auto_extract=bool(value.get("auto_extract", False)),
            completion_action=str(value.get("completion_action") or "none"),
            enabled=bool(value.get("enabled", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def matches(self, *, filename: str, url: str, content_type: str = "") -> bool:
        if not self.enabled:
            return False
        host = (urlparse(url).hostname or "").lower()
        extension = Path(filename.split("?", 1)[0]).suffix.lower().lstrip(".")
        domain_match = any(
            fnmatch(host, pattern) or fnmatch(host, f"*.{pattern}")
            for pattern in self.domains
        )
        extension_match = bool(extension and extension in self.extensions)
        mime_match = False
        lowered_type = content_type.lower()
        if self.id == "video":
            mime_match = lowered_type.startswith("video/")
        elif self.id == "music":
            mime_match = lowered_type.startswith("audio/")
        elif self.id == "documents":
            mime_match = lowered_type.startswith("text/") or lowered_type == "application/pdf"
        return domain_match or extension_match or mime_match


@dataclass(slots=True)
class CategoryDecision:
    category_id: str
    target_dir: Path
    temp_dir: Path
    auto_extract: bool = False
    completion_action: str = "none"


class CategoryManager:
    SETTINGS_KEY = "categories.v2"

    def __init__(self, store: StateStore):
        self.store = store
        if self.store.get_setting(self.SETTINGS_KEY) is None:
            self.store.set_setting(self.SETTINGS_KEY, _DEFAULT_CATEGORIES)

    def list(self) -> list[CategoryRule]:
        raw = self.store.get_setting(self.SETTINGS_KEY, _DEFAULT_CATEGORIES)
        return [CategoryRule.from_dict(item) for item in list(raw or [])]

    def save(self, category: CategoryRule) -> CategoryRule:
        categories = self.list()
        replaced = False
        for index, existing in enumerate(categories):
            if existing.id == category.id:
                categories[index] = category
                replaced = True
                break
        if not replaced:
            categories.append(category)
        self.store.set_setting(
            self.SETTINGS_KEY,
            [item.to_dict() for item in categories],
        )
        return category

    def delete(self, category_id: str) -> None:
        if category_id == "other":
            raise ValueError("The Other category cannot be deleted")
        categories = [item for item in self.list() if item.id != category_id]
        self.store.set_setting(
            self.SETTINGS_KEY,
            [item.to_dict() for item in categories],
        )

    def resolve(
        self,
        *,
        filename: str,
        url: str,
        base_dir: Path,
        temp_base_dir: Path,
        content_type: str = "",
        fixed_category: str = "",
    ) -> CategoryDecision:
        categories = self.list()
        selected: CategoryRule | None = None
        if fixed_category:
            selected = next(
                (item for item in categories if item.id == fixed_category),
                None,
            )
            if selected is None:
                raise KeyError(fixed_category)
        else:
            selected = next(
                (
                    item
                    for item in categories
                    if item.id != "other"
                    and item.matches(
                        filename=filename,
                        url=url,
                        content_type=content_type,
                    )
                ),
                None,
            )
        if selected is None:
            selected = next(
                (item for item in categories if item.id == "other"),
                CategoryRule(id="other", name="Other", folder="Other"),
            )

        target = Path(base_dir) / selected.folder if selected.folder else Path(base_dir)
        temp_name = selected.temp_folder or selected.folder or selected.id
        temporary = Path(temp_base_dir) / temp_name
        target.mkdir(parents=True, exist_ok=True)
        temporary.mkdir(parents=True, exist_ok=True)
        return CategoryDecision(
            category_id=selected.id,
            target_dir=target,
            temp_dir=temporary,
            auto_extract=selected.auto_extract,
            completion_action=selected.completion_action,
        )
