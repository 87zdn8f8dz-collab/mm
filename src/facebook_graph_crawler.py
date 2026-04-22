#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import requests


GRAPH_API_BASE = "https://graph.facebook.com/v23.0"
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
POST_FIELDS = ",".join(
    [
        "id",
        "message",
        "created_time",
        "permalink_url",
        "full_picture",
        "shares",
        "comments.summary(true).limit(0)",
        "reactions.summary(true).limit(0)",
        (
            "attachments{media_type,type,url,target,"
            "media,subattachments{media_type,type,url,target,media}}"
        ),
    ]
)
IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
VIDEO_CONTENT_TYPES = {"video/mp4", "video/quicktime", "video/webm", "video/x-matroska"}


def to_int(value) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return 0
    try:
        return int(float(text))
    except Exception:
        return 0


def safe_slug(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip())
    cleaned = cleaned.strip("._")
    return cleaned or "item"


def infer_extension(media_url: str, content_type: str, is_video: bool) -> str:
    parsed = urlparse(media_url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix in {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
        ".bmp",
        ".mp4",
        ".mov",
        ".mkv",
        ".webm",
        ".avi",
        ".m4v",
    }:
        return suffix

    normalized = (content_type or "").split(";")[0].strip().lower()
    guessed = mimetypes.guess_extension(normalized) if normalized else None
    if guessed:
        return guessed
    if normalized in IMAGE_CONTENT_TYPES:
        return ".jpg"
    if normalized in VIDEO_CONTENT_TYPES:
        return ".mp4"
    return ".mp4" if is_video else ".jpg"


def graph_get(
    session: requests.Session,
    path: str,
    access_token: str,
    params: Optional[Dict] = None,
    timeout_seconds: int = 30,
    retries: int = 3,
    sleep_seconds: float = 2.0,
) -> Tuple[Optional[Dict], Optional[str]]:
    query = dict(params or {})
    query["access_token"] = access_token
    url = f"{GRAPH_API_BASE}/{path.lstrip('/')}"

    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, params=query, timeout=timeout_seconds)
        except Exception as error:
            last_error = f"request error: {error}"
            time.sleep(sleep_seconds * attempt)
            continue

        if response.status_code == 200:
            try:
                payload = response.json()
            except Exception as error:
                last_error = f"json decode error: {error}"
                time.sleep(sleep_seconds * attempt)
                continue
            if isinstance(payload, dict) and payload.get("error"):
                details = payload.get("error") or {}
                last_error = f"{details.get('type')}: {details.get('message')}"
                time.sleep(sleep_seconds * attempt)
                continue
            return payload if isinstance(payload, dict) else {}, None

        last_error = f"http={response.status_code}"
        if response.status_code in {429, 500, 502, 503, 504}:
            time.sleep(sleep_seconds * attempt)
            continue
        break
    return None, last_error or "unknown error"


def resolve_page_info(
    session: requests.Session,
    account: str,
    access_token: str,
    timeout_seconds: int,
) -> Tuple[str, str]:
    default_name = account
    payload, error = graph_get(
        session=session,
        path=account,
        access_token=access_token,
        params={"fields": "id,name"},
        timeout_seconds=timeout_seconds,
    )
    if payload:
        page_id = str(payload.get("id") or account).strip()
        page_name = str(payload.get("name") or default_name).strip()
        return page_id, page_name
    if account.isdigit():
        return account, default_name
    raise RuntimeError(f"无法解析 Facebook 页面: {account} ({error})")


def flatten_attachments(attachments: Dict) -> Iterable[Dict]:
    queue: List[Dict] = list((attachments or {}).get("data") or [])
    while queue:
        item = queue.pop(0)
        if not isinstance(item, dict):
            continue
        yield item
        sub = (item.get("subattachments") or {}).get("data") or []
        if isinstance(sub, list):
            queue.extend(sub)


def fetch_video_source(
    session: requests.Session,
    target_id: str,
    access_token: str,
    timeout_seconds: int,
) -> str:
    payload, _ = graph_get(
        session=session,
        path=target_id,
        access_token=access_token,
        params={"fields": "source,picture,permalink_url"},
        timeout_seconds=timeout_seconds,
        retries=2,
    )
    if not payload:
        return ""
    source = str(payload.get("source") or "").strip()
    if source:
        return source
    picture = str(payload.get("picture") or "").strip()
    return picture


def extract_media_items(
    session: requests.Session,
    post: Dict,
    access_token: str,
    timeout_seconds: int,
) -> List[Dict]:
    media_items: List[Dict] = []
    seen: set[str] = set()

    full_picture = str(post.get("full_picture") or "").strip()
    if full_picture:
        media_items.append({"url": full_picture, "is_video": False})
        seen.add(full_picture)

    attachments = post.get("attachments") or {}
    for attachment in flatten_attachments(attachments):
        media_type = str(attachment.get("media_type") or attachment.get("type") or "").lower()
        target = attachment.get("target") or {}
        target_id = str(target.get("id") or "").strip()
        media = attachment.get("media") or {}
        image_url = str(((media.get("image") or {}).get("src") or "")).strip()
        attachment_url = str(attachment.get("url") or "").strip()

        is_video = "video" in media_type
        candidate_url = image_url or attachment_url
        if is_video and target_id:
            resolved_video = fetch_video_source(
                session=session,
                target_id=target_id,
                access_token=access_token,
                timeout_seconds=timeout_seconds,
            )
            if resolved_video:
                candidate_url = resolved_video

        if not candidate_url or candidate_url in seen:
            continue
        seen.add(candidate_url)
        media_items.append({"url": candidate_url, "is_video": is_video})
    return media_items


def fetch_posts(
    session: requests.Session,
    page_id: str,
    access_token: str,
    max_items: int,
    timeout_seconds: int,
) -> List[Dict]:
    payload, error = graph_get(
        session=session,
        path=f"{page_id}/posts",
        access_token=access_token,
        params={"fields": POST_FIELDS, "limit": max(1, min(100, max_items))},
        timeout_seconds=timeout_seconds,
    )
    if not payload:
        raise RuntimeError(f"获取 Facebook 帖子失败: page_id={page_id}, error={error}")
    posts = payload.get("data") or []
    if not isinstance(posts, list):
        return []
    return [item for item in posts if isinstance(item, dict)]


def first_line(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped:
        return ""
    return stripped.splitlines()[0].strip()


def download_media(
    session: requests.Session,
    media_url: str,
    timeout_seconds: int,
) -> Tuple[Optional[bytes], str]:
    try:
        response = session.get(media_url, timeout=timeout_seconds)
    except Exception:
        return None, ""
    if response.status_code != 200:
        return None, response.headers.get("Content-Type", "")
    return response.content, response.headers.get("Content-Type", "")


def crawl_account(
    account: str,
    output_dir: Path,
    access_token: str,
    max_items: int,
    timeout_seconds: int,
    sleep_seconds: float,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_UA, "Accept": "*/*"})

    page_id, page_name = resolve_page_info(
        session=session,
        account=account,
        access_token=access_token,
        timeout_seconds=timeout_seconds,
    )
    posts = fetch_posts(
        session=session,
        page_id=page_id,
        access_token=access_token,
        max_items=max_items,
        timeout_seconds=timeout_seconds,
    )

    saved = 0
    for post in posts:
        if saved >= max_items:
            break

        post_id = str(post.get("id") or "").strip()
        if not post_id:
            continue
        permalink_url = str(post.get("permalink_url") or f"https://www.facebook.com/{post_id}").strip()
        message = str(post.get("message") or "").strip()
        title = first_line(message)[:160]
        reactions = to_int(((post.get("reactions") or {}).get("summary") or {}).get("total_count"))
        comments = to_int(((post.get("comments") or {}).get("summary") or {}).get("total_count"))
        shares = to_int((post.get("shares") or {}).get("count"))
        created_time = str(post.get("created_time") or "").strip()

        media_items = extract_media_items(
            session=session,
            post=post,
            access_token=access_token,
            timeout_seconds=timeout_seconds,
        )
        if not media_items:
            continue

        post_slug = safe_slug(post_id.replace("_", "-"))
        for index, media in enumerate(media_items, start=1):
            if saved >= max_items:
                break
            media_url = str(media.get("url") or "").strip()
            if not media_url:
                continue
            is_video = bool(media.get("is_video"))

            blob, content_type = download_media(session=session, media_url=media_url, timeout_seconds=timeout_seconds)
            if not blob:
                continue
            suffix = infer_extension(media_url=media_url, content_type=content_type, is_video=is_video)
            file_stem = f"{post_slug}_{index}"
            media_path = output_dir / f"{file_stem}{suffix}"
            metadata_path = output_dir / f"{file_stem}{suffix}.json"

            media_path.write_bytes(blob)
            payload = {
                "platform": "facebook",
                "account": account,
                "page_id": page_id,
                "page_name": page_name,
                "post_id": post_id,
                "post_url": permalink_url,
                "title": title,
                "description": message[:5000],
                "content": message[:5000],
                "media_url": media_url,
                "is_video": is_video,
                "created_time": created_time,
                "like_count": reactions,
                "comment_count": comments,
                "share_count": shares,
                "view_count": 0,
                "source": "facebook_graph_api",
            }
            metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            saved += 1
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    print(
        f"[facebook] account={account} page_id={page_id} posts={len(posts)} "
        f"saved={saved} out={output_dir}"
    )
    if saved <= 0:
        raise RuntimeError(f"facebook 抓取为空: {account}")
    return saved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Facebook Graph API crawler")
    parser.add_argument("--account", required=True, help="Facebook page username 或 page id")
    parser.add_argument("--output-dir", type=Path, required=True, help="输出目录")
    parser.add_argument("--max-items", type=int, default=10, help="最多下载媒体条数")
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument("--sleep-seconds", type=float, default=1.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    access_token = os.environ.get("FACEBOOK_GRAPH_ACCESS_TOKEN", "").strip()
    if not access_token:
        raise SystemExit("missing FACEBOOK_GRAPH_ACCESS_TOKEN")
    crawl_account(
        account=args.account,
        output_dir=args.output_dir,
        access_token=access_token,
        max_items=max(1, args.max_items),
        timeout_seconds=max(10, args.timeout_seconds),
        sleep_seconds=max(0.0, args.sleep_seconds),
    )


if __name__ == "__main__":
    main()
