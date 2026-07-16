#!/usr/bin/env python3
"""Generate multi-resolution icons from the project's Resources image(s).

Outputs:
- browser-extension/icons/icon16|48|128.png
- static/favicon-*.png and static/manifest.webmanifest
- assets/windows/Lumi-DM.ico
- android/mipmap-*/ic_launcher.png
- ios/AppIcon.appiconset/ + Contents.json
- macos/icons.iconset/ (can be converted to .icns on macOS with iconutil)

If no explicit `my_logo.png` is present, the script will pick the largest PNG in `Resouces/`.
"""
import io
import os
import struct
import sys
import json
from pathlib import Path

try:
    from PIL import Image
except Exception:
    print("Pillow not installed. Run: python -m pip install pillow")
    raise


ROOT = Path(__file__).resolve().parents[1]
RES_DIR = ROOT / 'Resouces'
if not RES_DIR.exists():
    print(f"Resources folder not found: {RES_DIR}")
    sys.exit(1)

# Prefer explicit filename
preferred = RES_DIR / 'my_logo.png'
if preferred.exists():
    src_path = preferred
else:
    # pick the largest PNG in the folder
    pngs = list(RES_DIR.glob('*.png'))
    if not pngs:
        print(f'No PNG images in {RES_DIR}. Please add a high-res logo (PNG or SVG).')
        sys.exit(1)
    src_path = max(pngs, key=lambda p: p.stat().st_size)

img = Image.open(src_path).convert('RGBA')
W, H = img.size
print(f'Selected source: {src_path} ({W}x{H})')
if max(W, H) < 512:
    print('WARNING: source image is smaller than 512px. For best results provide >=1024px or an SVG.')


def ensure(d: Path):
    d.mkdir(parents=True, exist_ok=True)


def make_ico(source_img: "Image.Image", sizes=(16, 32, 48, 64, 128, 256)) -> bytes:
    """Build a valid multi-size ICO from a PIL Image without relying on Pillow's broken ICO saver."""
    frames = []
    for s in sizes:
        frame = source_img.resize((s, s), Image.LANCZOS).convert('RGBA')
        buf = io.BytesIO()
        frame.save(buf, format='PNG')
        frames.append(buf.getvalue())

    n = len(frames)
    # ICO header (6 bytes) + n directory entries (16 bytes each)
    data_offset = 6 + 16 * n
    header = struct.pack('<HHH', 0, 1, n)

    dir_entries = b''
    image_data = b''
    for s, png in zip(sizes, frames):
        w = s if s < 256 else 0   # ICO stores 256 as 0
        h = s if s < 256 else 0
        dir_entries += struct.pack('<BBBBHHII',
            w, h, 0, 0, 1, 32,
            len(png), data_offset + len(image_data))
        image_data += png

    return header + dir_entries + image_data


# Output paths
out_browser = ROOT / 'browser-extension' / 'icons'
out_static = ROOT / 'static'
out_assets = ROOT / 'assets'
out_windows = out_assets / 'windows'
out_android = ROOT / 'android'
out_ios = ROOT / 'ios' / 'AppIcon.appiconset'
out_macos = ROOT / 'macos' / 'icons.iconset'

for d in (out_browser, out_static, out_assets, out_windows, out_android, out_ios, out_macos):
    ensure(d)


# 1) Browser extension icons
for s in (16, 48, 128):
    r = img.resize((s, s), Image.LANCZOS)
    dest = out_browser / f'icon{s}.png'
    r.save(dest)
    print('Wrote', dest)

# 2) Static favicons
for s in (16, 32, 48, 96, 192, 256, 512):
    r = img.resize((s, s), Image.LANCZOS)
    dest = out_static / f'favicon-{s}.png'
    r.save(dest)

# 3) Windows .ico — manually built to guarantee 256x256 is present
ico_path = out_windows / 'Lumi-DM.ico'
ico_path.write_bytes(make_ico(img, sizes=(16, 32, 48, 64, 128, 256)))
print('Wrote', ico_path)

# 4) Android mipmap launcher icons
android_map = {
    'mipmap-mdpi': 48,
    'mipmap-hdpi': 72,
    'mipmap-xhdpi': 96,
    'mipmap-xxhdpi': 144,
    'mipmap-xxxhdpi': 192,
}
for folder, size in android_map.items():
    d = out_android / folder
    ensure(d)
    dest = d / 'ic_launcher.png'
    img.resize((size, size), Image.LANCZOS).save(dest)
    print('Wrote', dest)

# 5) macOS iconset (iconutil can convert .iconset -> .icns on macOS)
mac_map = {
    'icon_16x16.png': 16,
    'icon_16x16@2x.png': 32,
    'icon_32x32.png': 32,
    'icon_32x32@2x.png': 64,
    'icon_128x128.png': 128,
    'icon_128x128@2x.png': 256,
    'icon_256x256.png': 256,
    'icon_256x256@2x.png': 512,
    'icon_512x512.png': 512,
    'icon_512x512@2x.png': 1024,
}
for name, size in mac_map.items():
    dest = out_macos / name
    img.resize((size, size), Image.LANCZOS).save(dest)
    print('Wrote', dest)

# 6) iOS AppIcon.appiconset (Contents.json + PNGs)
ios_sizes = [
    (20, [1, 2, 3]),
    (29, [1, 2, 3]),
    (40, [1, 2, 3]),
    (60, [2, 3]),
    (76, [1, 2]),
    (83.5, [2]),
]
contents = {"images": [], "info": {"version": 1, "author": "xcode"}}
for base, scales in ios_sizes:
    for scale in scales:
        px = int(base * scale)
        # make filename safe (replace decimal point)
        base_str = str(base).replace('.', '_')
        fname = f'Icon-{base_str}@{scale}x.png'
        img.resize((px, px), Image.LANCZOS).save(out_ios / fname)
        contents['images'].append({
            'idiom': 'iphone',
            'size': str(base).rstrip('0').rstrip('.') if isinstance(base, float) else str(base),
            'scale': f'{scale}x',
            'filename': fname,
        })
        print('Wrote', out_ios / fname)

with open(out_ios / 'Contents.json', 'w', encoding='utf8') as fh:
    json.dump(contents, fh, indent=2)
    print('Wrote', out_ios / 'Contents.json')

# 7) Web manifest
manifest = {
    'name': 'Reminal Download Manager',
    'short_name': 'Lumi DM',
    'icons': [
        {'src': f'/static/favicon-192.png', 'sizes': '192x192', 'type': 'image/png'},
        {'src': f'/static/favicon-512.png', 'sizes': '512x512', 'type': 'image/png'},
    ],
    'start_url': '/',
    'display': 'standalone',
    'background_color': '#111317',
    'theme_color': '#4f9ef8'
}
with open(out_static / 'manifest.webmanifest', 'w', encoding='utf8') as fh:
    json.dump(manifest, fh, indent=2)
    print('Wrote', out_static / 'manifest.webmanifest')

print('\nDone. Review outputs and, if on macOS, run:')
print('  iconutil -c icns', out_macos)
print('\nIf the source image is low-res, provide a larger PNG or SVG in Resouces/ for better results.')
