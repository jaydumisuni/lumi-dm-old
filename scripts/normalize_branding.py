#!/usr/bin/env python3
"""Normalize legacy Lumi branding before development runs and release builds.

This catches old placeholder names that can reappear when an offline workspace is
synced back into the repository. It only touches UTF-8 source/configuration files
and skips generated output, dependencies, virtual environments and this script.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
THIS_FILE = Path(__file__).resolve()

SKIP_DIRECTORIES = {
    ".git",
    ".github",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "out",
    "release",
    "releases",
    "__pycache__",
}

TEXT_SUFFIXES = {
    ".css",
    ".dart",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".kt",
    ".md",
    ".nsh",
    ".plist",
    ".properties",
    ".ps1",
    ".py",
    ".sh",
    ".swift",
    ".toml",
    ".txt",
    ".webmanifest",
    ".xml",
    ".yaml",
    ".yml",
}

# Construct the legacy words in pieces so this guard does not match and rewrite
# its own source code while scanning the repository.
LEGACY_WORDS = (
    ("Re" + "minal", "Lumi"),
    ("re" + "minal", "lumi"),
    ("RE" + "MINAL", "LUMI"),
    ("Ru" + "mi", "Lumi"),
    ("ru" + "mi", "lumi"),
    ("RU" + "MI", "LUMI"),
)
PATTERNS = tuple(
    (re.compile(rf"\b{re.escape(old)}\b"), replacement)
    for old, replacement in LEGACY_WORDS
)


def iter_source_files():
    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file() or path.resolve() == THIS_FILE:
            continue
        if any(part in SKIP_DIRECTORIES for part in path.relative_to(PROJECT_ROOT).parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        yield path


def normalize_text(text: str) -> tuple[str, int]:
    total = 0
    updated = text
    for pattern, replacement in PATTERNS:
        updated, count = pattern.subn(replacement, updated)
        total += count
    return updated, total


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replace legacy Rumi/Reminal branding with Lumi in source files."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report legacy branding without modifying files.",
    )
    args = parser.parse_args()

    affected: list[tuple[Path, int]] = []

    for path in iter_source_files():
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        updated, count = normalize_text(text)
        if not count:
            continue

        affected.append((path, count))
        if args.check:
            continue

        temporary = path.with_name(f"{path.name}.branding.tmp")
        temporary.write_bytes(updated.encode("utf-8"))
        os.replace(temporary, path)

    if affected:
        action = "Found" if args.check else "Corrected"
        print(f"{action} legacy Lumi branding in {len(affected)} file(s):")
        for path, count in affected:
            print(f"  {path.relative_to(PROJECT_ROOT)} ({count})")
    else:
        print("Lumi branding check passed.")

    return 1 if args.check and affected else 0


if __name__ == "__main__":
    raise SystemExit(main())
