#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
POST_LINK_PATTERN = re.compile(r"^https://www\.instagram\.com/(p|reel)/[^/]+/?$")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
IG_APP_ID = "936619743392459"
PROFILE_INFO_ENDPOINT = "https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"


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


def build_profile_headers(account: str, cookie_header: str = "") -> Dict[str, str]:
    headers = {
        "User-Agent": DEFAULT_UA,
        "X-IG-App-ID": IG_APP_ID,
        "Accept": "*/*",
        "Referer": f"https://www.instagram.com/{account}/",
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
    return headers


def curl_get_bytes(url: str, headers: Dict[str, str], timeout_seconds: int) -> Tuple[int, bytes]:
    cmd = [
        "curl",
        "-L",
        "--silent",
        "--show-error",
        "--max-time",
        str(timeout_seconds),
    ]
    for key, value in headers.items():
        cmd.extend(["-H", f"{key}: {value}"])
    cmd.extend(["-w", "\n__STATUS__:%{http_code}\n", url])
    try:
        result = subprocess.run(cmd, check=False, capture_output=True)
    except Exception:
        return 0, b""
    if result.returncode != 0:
        return 0, b""
    body = result.stdout or b""
    marker = b"\n__STATUS__:"
    index = body.rfind(marker)
    if index == -1:
        return 0, body
    payload = body[:index]
    status_line = body[index + len(marker) :].strip().decode("utf-8", errors="ignore")
    try:
        status_code = int(status_line)
    except Exception:
        status_code = 0
    return status_code, payload


def curl_get_text(url: str, headers: Dict[str, str], timeout_seconds: int) -> Tuple[int, str]:
    status_code, payload = curl_get_bytes(url=url, headers=headers, timeout_seconds=timeout_seconds)
    return status_code, payload.decode("utf-8", errors="ignore")


def fetch_profile_payload(
    account: str,
    cookie_header: str,
    retries: int = 3,
    sleep_seconds: float = 2.2,
) -> Optional[Dict]:
    url = PROFILE_INFO_ENDPOINT.format(username=quote(account))
    headers_without_cookie = build_profile_headers(account=account, cookie_header="")
    headers_with_cookie = build_profile_headers(account=account, cookie_header=cookie_header) if cookie_header else None

    for attempt in range(1, retries + 1):
        for headers in [headers_without_cookie, headers_with_cookie]:
            if headers is None:
                continue
            status_code, payload_text = curl_get_text(url=url, headers=headers, timeout_seconds=30)
            if status_code == 200:
                try:
                    payload = json.loads(payload_text)
                except Exception:
                    payload = None
                if isinstance(payload, dict):
                    return payload
            if status_code in {401, 403}:
                continue
            if status_code == 429:
                time.sleep(sleep_seconds * attempt)
                continue
        time.sleep(min(1.2 * attempt, 4.0))
    return None


def extract_caption(node: Dict) -> str:
    edges = ((node.get("edge_media_to_caption") or {}).get("edges") or [])
    if not edges:
        return ""
    first = edges[0].get("node") or {}
    return str(first.get("text") or "").strip()


def build_media_jobs(account: str, nodes: List[Dict], max_items: int) -> List[Dict]:
    jobs: List[Dict] = []
    for node in nodes:
        shortcode = str(node.get("shortcode") or "").strip()
        if not shortcode:
            continue
        caption = extract_caption(node)
        taken_at = int(node.get("taken_at_timestamp") or 0)
        children = ((node.get("edge_sidecar_to_children") or {}).get("edges") or [])

        if children:
            for index, edge in enumerate(children):
                child = edge.get("node") or {}
                media_url = str(child.get("video_url") or child.get("display_url") or "").strip()
                if not media_url:
                    continue
                post_type = "reel" if bool(child.get("is_video")) else "p"
                jobs.append(
                    {
                        "post_id": f"{shortcode}_{index + 1}",
                        "post_url": f"https://www.instagram.com/{post_type}/{shortcode}/",
                        "media_url": media_url,
                        "title": caption,
                        "description": caption,
                        "content": caption,
                        "timestamp": taken_at,
                        "is_video": bool(child.get("is_video")),
                    }
                )
                if len(jobs) >= max_items:
                    return jobs
            continue

        media_url = str(node.get("video_url") or node.get("display_url") or "").strip()
        if not media_url:
            continue
        post_type = "reel" if bool(node.get("is_video")) else "p"
        jobs.append(
            {
                "post_id": shortcode,
                "post_url": f"https://www.instagram.com/{post_type}/{shortcode}/",
                "media_url": media_url,
                "title": caption,
                "description": caption,
                "content": caption,
                "timestamp": taken_at,
                "is_video": bool(node.get("is_video")),
            }
        )
        if len(jobs) >= max_items:
            return jobs
    return jobs


def collect_media_jobs(account: str, cookie_header: str, max_items: int) -> List[Dict]:
    payload = fetch_profile_payload(account=account, cookie_header=cookie_header)
    if not payload:
        return []
    user = ((payload.get("data") or {}).get("user") or {})
    edges = ((user.get("edge_owner_to_timeline_media") or {}).get("edges") or [])
    nodes = [edge.get("node") or {} for edge in edges]
    return build_media_jobs(account=account, nodes=nodes, max_items=max_items)


def sanitize_text(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    return re.sub(r"\s+", " ", text)[:5000]


def download_media(media_url: str, headers: Dict[str, str]) -> Optional[bytes]:
    status_code, payload_bytes = curl_get_bytes(url=media_url, headers=headers, timeout_seconds=45)
    if status_code != 200:
        return None
    return payload_bytes


def run(account: str, output_dir: Path, cookies_file: Path | None, max_items: int, headless: bool) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    cookies = parse_netscape_cookies(cookies_file) if cookies_file else []
    cookie_header = context_cookie_header(cookies)
    saved = 0
    _ = headless
    media_headers = {"User-Agent": DEFAULT_UA, "Accept": "*/*"}
    if cookie_header:
        media_headers["Cookie"] = cookie_header

    jobs = collect_media_jobs(account=account, cookie_header=cookie_header, max_items=max_items)
    print(
        f"[ig-fallback-debug] account={account} jobs={len(jobs)} "
        f"cookies={'yes' if cookies else 'no'} mode=public-api"
    )

    for job in jobs:
        media_url = str(job.get("media_url") or "").strip()
        if not media_url:
            continue
        media_blob = download_media(media_url=media_url, headers=media_headers)
        if not media_blob:
            continue

        post_id = str(job.get("post_id") or "unknown")
        suffix = suffix_from_url(media_url)
        media_path = output_dir / f"{post_id}{suffix}"
        json_path = output_dir / f"{post_id}{suffix}.json"

        media_path.write_bytes(media_blob)
        payload = {
            "platform": "instagram",
            "account": account,
            "post_id": post_id,
            "post_url": str(job.get("post_url") or f"https://www.instagram.com/{account}/"),
            "title": sanitize_text(str(job.get("title") or "")),
            "description": sanitize_text(str(job.get("description") or "")),
            "content": sanitize_text(str(job.get("content") or "")),
            "media_url": media_url,
            "is_video": bool(job.get("is_video")),
            "timestamp": int(job.get("timestamp") or 0),
            "source": "ig_public_api_fallback",
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        saved += 1

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
