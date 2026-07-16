"""Link grabber — scan a webpage and extract direct download / video links."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup

_UA = "Lumi-DM/1.0"
_TIMEOUT = 15

_EXTENSIONS = {
    "zip", "rar", "7z", "gz", "tar", "bz2", "xz", "lz", "zst",
    "exe", "msi", "dmg", "pkg", "deb", "rpm", "apk", "ipa", "xapk",
    "img", "iso", "bin", "rom",
    "mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "m4v",
    "mp3", "flac", "wav", "aac", "ogg", "m4a", "opus",
    "pdf", "epub", "mobi", "azw", "azw3",
    "torrent",
    "fw", "hex", "elf", "fls", "pac", "laf",
}

_AD_DOMAINS = {
    "doubleclick.net", "googlesyndication.com", "googleadservices.com",
    "adnxs.com", "adroll.com", "criteo.com", "outbrain.com", "taboola.com",
    "advertising.com", "scorecardresearch.com", "quantserve.com",
    "moatads.com", "amazonadsystem.com", "media.net", "pubmatic.com",
    "rubiconproject.com", "openx.net", "bidswitch.net", "spotxchange.com",
}

# Video platform host fragments and URL path patterns
_VIDEO_HOSTS = {
    "youtube.com", "youtu.be", "vimeo.com", "dailymotion.com",
    "twitch.tv", "twitter.com", "x.com", "tiktok.com",
    "facebook.com", "instagram.com", "bilibili.com", "nicovideo.jp",
    "rumble.com", "odysee.com", "bitchute.com",
}
_VIDEO_PATH_RE = re.compile(
    r"(/watch|/video/|/videos/|/clip/|/v/|/embed/|/reel/|/status/"
    r"|/shorts/|/w/|/view_video\.php)",
    re.I,
)

# Pagination link text patterns
_NEXT_RE = re.compile(r"^\s*(next[\s\-_]*page?|next|older|more|\u203a|\u00bb|>)\s*$", re.I)


def _is_ad(url: str) -> bool:
    try:
        return any(d in urlparse(url).netloc.lower() for d in _AD_DOMAINS)
    except Exception:
        return False


def _is_video_url(url: str) -> bool:
    """True if url looks like a video platform page (not a direct file)."""
    try:
        p = urlparse(url)
        host = p.netloc.lower().lstrip("www.")
        if any(vh in host for vh in _VIDEO_HOSTS):
            return True
        return bool(_VIDEO_PATH_RE.search(p.path))
    except Exception:
        return False


def _find_next_page(soup: BeautifulSoup, current_url: str) -> str | None:
    """
    Try to find a 'next page' link. Checks (in order):
    1. <link rel="next"> in <head>
    2. <a rel="next">
    3. <a> whose text matches "next / › / »" etc.
    4. URL ?page=N → ?page=N+1 (only if there are matching numbered links on page)
    """
    # rel="next" in <head>
    tag = soup.find("link", rel=lambda r: r and "next" in (r if isinstance(r, list) else [r]))
    if tag and tag.get("href"):
        candidate = urljoin(current_url, str(tag["href"]))
        if candidate != current_url:
            return candidate

    # <a rel="next">
    tag = soup.find("a", rel=lambda r: r and "next" in (r if isinstance(r, list) else [r]))
    if tag and tag.get("href"):
        candidate = urljoin(current_url, str(tag["href"]))
        if candidate != current_url:
            return candidate

    # <a> whose visible text looks like "Next"
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        if _NEXT_RE.match(text):
            candidate = urljoin(current_url, str(a["href"]))
            if candidate.startswith("http") and candidate != current_url:
                return candidate

    # ?page=N → ?page=N+1  (common pagination query param)
    parsed = urlparse(current_url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    for param in ("page", "p", "pg", "paged", "pagenum", "start", "offset"):
        if param in qs:
            try:
                n = int(qs[param][0])
                # Only follow if we can find a link with page=n+1 on the current page
                next_val = str(n + 1)
                new_qs = dict(qs)
                new_qs[param] = [next_val]
                new_url = urlunparse(parsed._replace(query=urlencode(new_qs, doseq=True)))
                # Confirm: is there actually a link on the page pointing to page n+1?
                if soup.find("a", href=lambda h: h and next_val in str(h)):
                    return new_url
            except (ValueError, TypeError):
                pass

    return None


def grab_links(page_url: str) -> list[dict]:
    """Fetch a page and return downloadable links found on it."""
    try:
        resp = requests.get(page_url, timeout=_TIMEOUT, allow_redirects=True,
                            headers={"User-Agent": _UA})
        resp.raise_for_status()
    except Exception as exc:
        return [{"error": str(exc)}]

    soup = BeautifulSoup(resp.text, "html.parser")
    found: list[dict] = []
    seen: set[str] = set()

    for tag in soup.find_all("a", href=True):
        raw = str(tag["href"]).strip()
        if not raw or raw.startswith(("#", "javascript")):
            continue
        absolute = urljoin(page_url, raw)
        if not absolute.startswith("http") or _is_ad(absolute):
            continue
        ext = absolute.split("?")[0].rsplit(".", 1)[-1].lower()
        if ext not in _EXTENSIONS or absolute in seen:
            continue
        seen.add(absolute)
        label = (tag.get_text(" ", strip=True)
                 or Path(urlparse(absolute).path).name
                 or absolute)
        found.append({"url": absolute, "filename": label[:120], "ext": ext})

    return found


def crawl_pages(
    start_url: str,
    max_pages: int = 20,
    include_videos: bool = True,
    include_files: bool = True,
) -> dict:
    """
    Crawl start_url and follow pagination links (Next, rel=next, ?page=N…)
    up to max_pages pages.

    Returns:
        {
          "links":         [{"url", "filename", "ext", "type", "from_page"}],
          "pages_crawled": int,
          "pages":         [url, …],    # pages visited
        }
    """
    visited:   list[str] = []
    to_visit:  list[str] = [start_url]
    all_links: list[dict] = []
    all_seen:  set[str]  = set()

    while to_visit and len(visited) < max_pages:
        url = to_visit.pop(0)
        if url in visited:
            continue
        visited.append(url)

        try:
            resp = requests.get(url, timeout=_TIMEOUT, allow_redirects=True,
                                headers={"User-Agent": _UA})
            resp.raise_for_status()
        except Exception:
            continue

        soup = BeautifulSoup(resp.text, "html.parser")

        for tag in soup.find_all("a", href=True):
            raw = str(tag["href"]).strip()
            if not raw or raw.startswith(("#", "javascript:")):
                continue
            absolute = urljoin(url, raw)
            if not absolute.startswith("http") or _is_ad(absolute) or absolute in all_seen:
                continue

            ext  = absolute.split("?")[0].rsplit(".", 1)[-1].lower()
            text = tag.get_text(" ", strip=True) or Path(urlparse(absolute).path).name or absolute

            if include_files and ext in _EXTENSIONS:
                all_seen.add(absolute)
                all_links.append({
                    "url":       absolute,
                    "filename":  text[:120],
                    "ext":       ext,
                    "type":      "torrent" if ext == "torrent" else "http",
                    "from_page": url,
                })
            elif include_videos and _is_video_url(absolute):
                all_seen.add(absolute)
                all_links.append({
                    "url":       absolute,
                    "filename":  text[:120],
                    "ext":       "",
                    "type":      "video",
                    "from_page": url,
                })

        # Follow pagination
        nxt = _find_next_page(soup, url)
        if nxt and nxt not in visited and nxt not in to_visit:
            to_visit.insert(0, nxt)

    return {
        "links":         all_links,
        "pages_crawled": len(visited),
        "pages":         visited,
    }
