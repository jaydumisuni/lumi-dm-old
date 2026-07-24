from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import zipfile

SOURCE = Path("source").resolve()
OUTPUT_ROOT = Path("export").resolve()
TARGET = OUTPUT_ROOT / "Lumi-DM"
ZIP_PATH = OUTPUT_ROOT / "Lumi-DM-clean-source.zip"
SOURCE_COMMIT = "67daf3a4e3e95c81738da7bb5eb84b4cbe4186a4"

EXCLUDED_DIRS = {
    ".git", ".github", "scripts", "docs", "dist", "build", "release", "out",
    "installer_output", "build_config", "node_modules", "builder_cache",
    "import_cache", "test-output", "__pycache__", ".pytest_cache", ".mypy_cache",
}
EXCLUDED_FILES = {
    "BUILD.md", "DEVELOPMENT.md", "build.bat", "build.ps1", "package.json",
    "package-lock.json", "electron/package.json", "electron/package-lock.json",
    "electron/prepare-icons.py", "electron/installer.nsh",
}


def ignored(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in EXCLUDED_DIRS}


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def main() -> None:
    if not SOURCE.is_dir():
        raise SystemExit(f"Source checkout missing: {SOURCE}")
    shutil.rmtree(OUTPUT_ROOT, ignore_errors=True)
    OUTPUT_ROOT.mkdir(parents=True)
    shutil.copytree(SOURCE, TARGET, ignore=ignored)

    for relative in EXCLUDED_FILES:
        path = TARGET / relative
        if path.is_file():
            path.unlink()
    for path in list(TARGET.rglob("*")):
        if path.is_file() and (path.suffix in {".pyc", ".pyo", ".log"}):
            path.unlink()

    write_text(TARGET / "README.md", """
# Lumi DM

**Lumi DM** is the THETECHGUY DIGITAL SOLUTIONS multi-source download manager.

## Official download

End users download Lumi only from the repository's **GitHub Releases** page or from:

**https://thetechguyds.com/tools**

This repository does not provide a separate desktop-wrapper download, a run-from-source package, or a self-build installer path.

## Build and release ownership

Lumi contains application source and the `techguy-build.json` project contract only.

**THETECHGUY Software Builder** owns Electron and Python dependency preparation, temporary packaging workspaces, `LUMIDM-server.exe` creation, desktop packaging, the THETECHGUY graphical installer, Windows uninstall registration, signing, SHA-256 evidence and GitHub Release publishing.

No public installer is produced directly from this repository.

**ONE BRAND • ALL SOLUTIONS**
""")

    config = {
        "schemaVersion": 2,
        "company": "THETECHGUY DIGITAL SOLUTIONS",
        "domain": "thetechguyds.com",
        "website": "https://thetechguyds.com/tools",
        "appName": "Lumi DM",
        "appVersion": "1.0.0",
        "projectType": "electron-source",
        "entryFile": "electron/main.js",
        "logo": "static/favicon-96.png",
        "icon": "assets/windows/Lumi-DM.ico",
        "description": "Fast multi-source download manager with browser capture, media, torrents, firmware, operating-system images and technician workflows.",
        "repository": "jaydumisuni/Lumi-DM",
        "distribution": {
            "publicDownloads": "github-releases-only",
            "runFromSourceSupported": False,
            "desktopWrapperDownload": False,
            "selfBuildSupported": False,
        },
        "output": {"dist": "dist/electron", "installer": "installer_output"},
        "targets": ["windows-exe", "windows-installer"],
        "installer": {
            "template": "thetechguy-neon",
            "windowSize": "standard",
            "logoSize": "medium",
            "runAsAdmin": True,
            "installBase": r"C:\Program Files\THETECHGUY DIGITAL SOLUTIONS",
            "visitWebsiteChecked": True,
            "runAfterInstallChecked": True,
            "desktopShortcutChecked": True,
            "startMenuShortcutChecked": True,
            "checkDependenciesOnStart": True,
            "activationPageInInstaller": False,
            "requireCustomGraphicalInstaller": True,
            "rejectVendorInstallerArtifacts": True,
            "requireRegisteredUninstall": True,
        },
        "electron": {
            "builderOwnsPackaging": True,
            "sourceRoot": "electron",
            "appId": "com.lumi.dm",
            "electronVersion": "^29.0.0",
            "electronBuilderVersion": "^24.0.0",
            "pythonSidecars": [{
                "id": "lumi-server",
                "enabled": True,
                "entry": "server.py",
                "name": "LUMIDM-server",
                "requirements": "requirements.txt",
                "output": "dist/server",
                "extraRequirements": ["libtorrent==2.0.13", "imageio-ffmpeg>=0.5,<1"],
                "hiddenImports": ["libtorrent"],
                "collectAll": ["yt_dlp", "cryptography", "psutil", "imageio_ffmpeg"],
                "collectSubmodules": ["core"],
            }],
        },
        "dependencies": [{
            "id": "sevenzip",
            "name": "7-Zip archive engine",
            "kind": "sevenzip",
            "required": False,
            "installMode": "manual-if-missing",
            "note": "Optional enhancement for RAR, 7z and multipart archive extraction. Core downloads remain fully functional without it.",
        }],
        "releaseControls": {
            "updateCheck": True,
            "loginRequired": False,
            "subscriptionRequired": False,
            "onlineRequired": False,
        },
        "githubRelease": {
            "repository": "jaydumisuni/Lumi-DM",
            "tag": "v1.0.0",
            "title": "Lumi DM 1.0.0",
            "notes": "",
            "targetCommitish": "main",
            "prerelease": False,
            "replaceSameNameAssets": True,
            "generateSha256Sidecars": True,
            "tokenStoredInProject": False,
            "tokenEnvironmentVariables": ["GITHUB_TOKEN", "GH_TOKEN"],
        },
        "sourceBoundary": {
            "containsApplicationSource": True,
            "containsBuildEnvironment": False,
            "containsInstallerEngine": False,
            "containsSigningKeys": False,
            "builderRepository": "jaydumisuni/thetechguy-software-builder",
        },
    }
    write_text(TARGET / "techguy-build.json", json.dumps(config, indent=2))

    manifest = {
        "schema_version": 1,
        "package": "Lumi-DM-clean-source",
        "source_commit": SOURCE_COMMIT,
        "public_downloads": "GitHub Releases only",
        "builder_owned": [
            "dependency preparation", "Electron packaging", "Python sidecar packaging",
            "installer", "uninstall registration", "signing", "release publishing",
        ],
        "excluded": [
            "run-from-source instructions", "desktop-wrapper download", "npm build scripts",
            "electron-builder configuration", "stock installer configuration",
            "generated artifacts", "old Reminal compatibility files",
        ],
    }
    write_text(TARGET / "SOURCE-MANIFEST.json", json.dumps(manifest, indent=2))
    write_text(TARGET / ".gitignore", """
__pycache__/
*.py[cod]
.pytest_cache/
node_modules/
builder_cache/
build_config/
dist/
build/
release/
out/
installer_output/
test-output/
*.log
""")

    assert not (TARGET / "electron/package.json").exists()
    assert not (TARGET / ".github").exists()
    assert not (TARGET / "scripts").exists()
    assert not (TARGET / "BUILD.md").exists()
    assert not (TARGET / "DEVELOPMENT.md").exists()
    json.loads((TARGET / "techguy-build.json").read_text(encoding="utf-8"))

    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(TARGET.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(OUTPUT_ROOT))
    digest = hashlib.sha256(ZIP_PATH.read_bytes()).hexdigest()
    write_text(ZIP_PATH.with_suffix(ZIP_PATH.suffix + ".sha256"), f"{digest}  {ZIP_PATH.name}")
    print(ZIP_PATH)
    print(digest)


if __name__ == "__main__":
    main()
