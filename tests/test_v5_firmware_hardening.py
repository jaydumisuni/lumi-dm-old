from __future__ import annotations

from core.v5 import firmware


def _apple_item(*, version: str, date: str, kind: str) -> dict:
    extension = "ipsw" if kind == "ipsw" else "zip"
    return {
        "version": version,
        "buildid": version.replace(".", ""),
        "url": f"https://updates.example/{version}-{kind}.{extension}",
        "filesize": 1024,
        "signed": True,
        "releasedate": f"{date}T00:00:00Z",
    }


def test_stable_apple_search_queries_ipsw_and_ota(monkeypatch) -> None:
    requested: list[str] = []

    def fake_json(url: str):
        requested.append(url)
        kind = "ota" if "/ota/" in url else "ipsw"
        return {"firmwares": [_apple_item(version="18.6", date="2026-07-15", kind=kind)]}

    monkeypatch.setattr(firmware, "_get_json", fake_json)

    results = firmware._apple_firmware("iPhone15,2", "stable")

    assert any("/ipsw/device/iPhone15%2C2" in url for url in requested)
    assert any("/ota/device/iPhone15%2C2" in url for url in requested)
    assert {item.file_type for item in results} == {"ipsw", "ota"}
    assert all(item.channel == "stable" for item in results)


def test_firmware_results_are_newest_first_inside_quality_group(monkeypatch) -> None:
    monkeypatch.setattr(
        firmware,
        "_apple_firmware",
        lambda _device, _channel: [
            firmware.FirmwareResult(
                id="old",
                provider="apple-ipsw",
                source_name="Official index",
                source_group="Official OS index",
                official=True,
                brand="Apple",
                device="iPhone15,2",
                title="Older release",
                release_date="2026-01-10",
                url="https://example.invalid/old.ipsw",
                direct=True,
                signed=True,
            ),
            firmware.FirmwareResult(
                id="new",
                provider="apple-ipsw",
                source_name="Official index",
                source_group="Official OS index",
                official=True,
                brand="Apple",
                device="iPhone15,2",
                title="Newer release",
                release_date="2026-07-15",
                url="https://example.invalid/new.ipsw",
                direct=True,
                signed=True,
            ),
        ],
    )

    results = firmware.search_firmware(
        provider="apple-ipsw",
        brand="Apple",
        device="iPhone15,2",
        channel="stable",
        include_community=False,
    )

    assert [item["id"] for item in results[:2]] == ["new", "old"]
