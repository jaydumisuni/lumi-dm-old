#!/usr/bin/env python3
"""Build a clean multi-resolution Windows icon from the Lumi master PNG.

The source artwork is cropped by its visible alpha bounds, fitted onto a square
canvas without changing its aspect ratio, centred, and rendered at the sizes
Windows uses for title bars, the taskbar, Explorer and installers.
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageFilter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "static" / "favicon-256.png"
OUTPUT_ICO = PROJECT_ROOT / "assets" / "windows" / "Lumi-DM.ico"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
MASTER_SIZE = 1024
ALPHA_THRESHOLD = 8


def source_path() -> Path:
    override = os.environ.get("LUMIDM_ICON_SOURCE")
    return Path(override).expanduser().resolve() if override else DEFAULT_SOURCE


def padding_ratio() -> float:
    raw = os.environ.get("LUMIDM_ICON_PADDING", "0.08")
    try:
        value = float(raw)
    except ValueError as exc:
        raise SystemExit("LUMIDM_ICON_PADDING must be a decimal such as 0.08") from exc

    if not 0 <= value < 0.4:
        raise SystemExit("LUMIDM_ICON_PADDING must be between 0 and 0.4")
    return value


def visible_bounds(image: Image.Image) -> tuple[int, int, int, int]:
    alpha = image.getchannel("A")
    mask = alpha.point(lambda value: 255 if value >= ALPHA_THRESHOLD else 0)
    return mask.getbbox() or (0, 0, image.width, image.height)


def build_master(image: Image.Image, padding: float) -> Image.Image:
    cropped = image.crop(visible_bounds(image))
    usable = max(1, round(MASTER_SIZE * (1 - padding * 2)))

    scale = min(usable / cropped.width, usable / cropped.height)
    target_size = (
        max(1, round(cropped.width * scale)),
        max(1, round(cropped.height * scale)),
    )
    fitted = cropped.resize(target_size, Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (MASTER_SIZE, MASTER_SIZE), (0, 0, 0, 0))
    offset = (
        (MASTER_SIZE - fitted.width) // 2,
        (MASTER_SIZE - fitted.height) // 2,
    )
    canvas.alpha_composite(fitted, offset)
    return canvas


def render_size(master: Image.Image, size: int) -> Image.Image:
    rendered = master.resize((size, size), Image.Resampling.LANCZOS)

    # A mild small-size sharpen keeps the neon edge readable in Windows title
    # bars and the taskbar without changing the original artwork.
    if size <= 48:
        rendered = rendered.filter(
            ImageFilter.UnsharpMask(radius=0.45, percent=120, threshold=2)
        )
    return rendered


def main() -> None:
    source = source_path()
    if not source.is_file():
        raise SystemExit(f"Icon source was not found: {source}")

    try:
        image = Image.open(source).convert("RGBA")
    except Exception as exc:
        raise SystemExit(f"Could not read icon source {source}: {exc}") from exc

    master = build_master(image, padding_ratio())
    frames = [render_size(master, size) for size in ICON_SIZES]

    OUTPUT_ICO.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT_ICO.with_name(f"{OUTPUT_ICO.name}.tmp")

    try:
        # Supplying the individual frames prevents Windows from stretching one
        # large image for every UI slot.
        frames[-1].save(
            temporary,
            format="ICO",
            sizes=[(size, size) for size in ICON_SIZES],
            append_images=frames[:-1],
        )
        temporary.replace(OUTPUT_ICO)
    finally:
        if temporary.exists():
            temporary.unlink()

    with Image.open(OUTPUT_ICO) as built:
        embedded = sorted(built.info.get("sizes", ()))

    expected = sorted((size, size) for size in ICON_SIZES)
    if embedded != expected:
        raise SystemExit(
            f"Generated ICO is missing sizes. Expected {expected}, got {embedded}"
        )

    print(f"Prepared {OUTPUT_ICO.relative_to(PROJECT_ROOT)} from {source.name}")
    print("Embedded sizes:", ", ".join(f"{w}x{h}" for w, h in embedded))


if __name__ == "__main__":
    main()
