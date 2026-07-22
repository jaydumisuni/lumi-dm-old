from __future__ import annotations

import json
from pathlib import Path


def test_lumi_repo_contains_no_self_packaging_machinery() -> None:
    root = Path(__file__).resolve().parents[1]
    forbidden = [
        root / "BUILD.md",
        root / "electron" / "prepare-icons.py",
        root / "electron" / "installer.nsh",
        root / "electron" / "package-lock.json",
    ]

    assert not [str(path.relative_to(root)) for path in forbidden if path.exists()]

    package = json.loads(
        (root / "electron" / "package.json").read_text(encoding="utf-8")
    )
    assert package["main"] == "main.js"
    assert "scripts" not in package
    assert "build" not in package
    assert "dependencies" not in package
    assert "devDependencies" not in package


def test_source_documents_keep_runtime_and_builder_responsibilities_separate() -> None:
    root = Path(__file__).resolve().parents[1]
    development = (root / "DEVELOPMENT.md").read_text(encoding="utf-8")
    ownership = (root / "docs" / "SOURCE_OWNERSHIP.md").read_text(
        encoding="utf-8"
    )

    assert "python server.py" in development
    assert "python -m pytest -q" in development
    assert "application behavior is already working" in ownership
    assert "Generated outputs" in ownership
    assert "must not repair" in ownership
