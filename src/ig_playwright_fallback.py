#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright


DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
POST_LINK_PATTERN = re.compile(r"^https://www\.instagram\.com/(p|reel)/[^/]+/?$")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
IG_APP_ID = "936619743392459"


def parse_netscape_cookies(cookie_file: Path) -> List[Dict]:
    cookies: List[Dict] = []
    if not cookie_file.exists():
        return cookies
    for raw_line in cookie_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        http_only = False
        if line.startswith("#HttpOnly_"):
            http_only = True
            line = line[len("#HttpOnly_") :]
        elif line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain, _, path, secure, expires, name, value = parts
        if "instagram.com" not in domain:
            continue
        normalized_domain = domain if domain.startswith(".") else f".{domain}"
        expires_value = -1
        if expires.lstrip("-").isdigit():
            expires_value = float(expires)
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": normalized_domain,
                "path": path or "/",
                "httpOnly": http_only,
                "secure": secure.upper() == "TRUE",
                "expires": expires_value,
            }
        )
    return cookies


def parse_meta(html: str) -> Dict[str, str]:
    def find(pattern: str) -> str:
        matched = re.search(pattern, html, flags=re.IGNORECASE)
        if not matched:
            return ""
        return (matched.group(1) or "").strip()

    title = find(r'<meta property="og:title" content="([^"]*)"')
    description = find(r'<meta property="og:description" content="([^"]*)"')
    video_url = find(r'<meta property="og:video" content="([^"]*)"')
    image_url = find(r'<meta property="og:image" content="([^"]*)"')
    if not video_url:
        raw_video = find(r'"video_url":"([^"]+)"')
        if raw_video:
            video_url = raw_video.replace("\\u0026", "&").replace("\\/", "/")
    if not image_url:
        raw_image = find(r'"display_url":"([^"]+)"')
        if raw_image:
            image_url = raw_image.replace("\\u0026", "&").replace("\\/", "/")
    media_url = video_url or image_url
    return {
        "title": title,
        "description": description,
        "content": description,
        "media_url": media_url,
    }


def suffix_from_url(media_url: str) -> str:
    suffix = Path(urlparse(media_url).path).suffix.lower()
    if suffix in VIDEO_EXTENSIONS | IMAGE_EXTENSIONS:
        return suffix
    if "mp4" in media_url or "video" in media_url:
        return ".mp4"
    return ".jpg"


def context_cookie_header(cookies: List[Dict]) -> str:
    pairs = []
    for cookie in cookies:
        name = cookie.get("name", "")
        value = cookie.get("value", "")
        if name and value:
            pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def extract_links_from_html(html: str) -> List[str]:
    links: List[str] = []
    for post_type, code in re.findall(r'"/(p|reel)/([A-Za-z0-9_-]{5,})/', html):
        links.append(f"https://www.instagram.com/{post_type}/{code}/")
    return links


def collect_links_via_profile_api(account: str, cookie_header: str, max_items: int) -> List[str]:
    url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={account}"
    headers = {
        "User-Agent": DEFAULT_UA,
        "X-IG-App-ID": IG_APP_ID,
        "Accept": "*/*",
        "Referer": f"https://www.instagram.com/{account}/",
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
    try:
        response = requests.get(url, headers=headers, timeout=25)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return []

    user = ((payload.get("data") or {}).get("user") or {})
    edges = ((user.get("edge_owner_to_timeline_media") or {}).get("edges") or [])
    links: List[str] = []
    for edge in edges:
        node = edge.get("node") or {}
        shortcode = str(node.get("shortcode") or "").strip()
        if not shortcode:
            continue
        links.append(f"https://www.instagram.com/p/{shortcode}/")
        if len(links) >= max_items:
            break
    return links


def collect_post_links(page, account: str, max_items: int, cookie_header: str) -> Tuple[List[str], str]:
    profile_url = f"https://www.instagram.com/{account}/"
    page.goto(profile_url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(2500)
    current_url = page.url
    html = page.content()
    links: List[str] = []
    for anchor in page.query_selector_all("a[href]"):
        href = anchor.get_attribute("href") or ""
        if not href:
            continue
        full = href if href.startswith("http") else f"https://www.instagram.com{href}"
        if not POST_LINK_PATTERN.match(full):
            continue
        links.append(full.rstrip("/") + "/")
    if len(links) < max_items:
        links.extend(extract_links_from_html(html))
    if len(links) < max_items:
        links.extend(collect_links_via_profile_api(account=account, cookie_header=cookie_header, max_items=max_items))
    deduped = list(dict.fromkeys(links))
    return deduped[:max_items], current_url


def run(account: str, output_dir: Path, cookies_file: Path | None, max_items: int, headless: bool) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    cookies = parse_netscape_cookies(cookies_file) if cookies_file else []
    cookie_header = context_cookie_header(cookies)
    saved = 0

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(user_agent=DEFAULT_UA, viewport={"width": 1280, "height": 960})
        if cookies:
            context.add_cookies(cookies)

        page = context.new_page()
        links, current_url = collect_post_links(page, account=account, max_items=max_items, cookie_header=cookie_header)
        print(
            f"[ig-fallback-debug] account={account} profile_url={current_url} links={len(links)} "
            f"cookies={'yes' if cookies else 'no'}"
        )
        page.close()

        session = requests.Session()
        session.headers.update({"User-Agent": DEFAULT_UA})
        if cookie_header:
            session.headers["Cookie"] = cookie_header

        for link in links:
            post_page = context.new_page()
            try:
                post_page.goto(link, wait_until="domcontentloaded", timeout=90000)
                post_page.wait_for_timeout(1000)
                html = post_page.content()
            except Exception:
                post_page.close()
                continue
            post_page.close()

            meta = parse_meta(html)
            media_url = meta.get("media_url", "")
            if not media_url:
                continue
            post_id = urlparse(link).path.strip("/").split("/")[-1]
            suffix = suffix_from_url(media_url)
            media_path = output_dir / f"{post_id}{suffix}"
            json_path = output_dir / f"{post_id}{suffix}.json"

            try:
                response = session.get(media_url, timeout=30)
                response.raise_for_status()
            except Exception:
                continue

            media_path.write_bytes(response.content)
            payload = {
                "platform": "instagram",
                "account": account,
                "post_id": post_id,
                "post_url": link,
                "title": meta.get("title", ""),
                "description": meta.get("description", ""),
                "content": meta.get("content", ""),
                "media_url": media_url,
                "source": "ig_playwright_fallback",
            }
            json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            saved += 1

        browser.close()
    return saved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Instagram Playwright fallback")
    parser.add_argument("--account", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cookies-file", type=Path, default=None)
    parser.add_argument("--max-items", type=int, default=6)
    parser.add_argument("--headless", action="store_true", default=False)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    saved = run(
        account=args.account,
        output_dir=args.output_dir,
        cookies_file=args.cookies_file,
        max_items=args.max_items,
        headless=args.headless,
    )
    print(f"[ig-fallback] account={args.account} saved={saved} out={args.output_dir}")
    if saved <= 0:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
