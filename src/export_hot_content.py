#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple


VIDEO_EXTENSIONS = {"mp4", "mov", "webm", "mkv", "avi", "m4v"}


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


def first_non_empty(meta: Dict, keys: List[str]) -> str:
    for key in keys:
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def classify_content(meta: Dict, file_path: Path) -> str:
    extension = str(meta.get("extension") or "").lower().strip(".")
    if extension in VIDEO_EXTENSIONS or meta.get("video_url"):
        return "video"
    suffix = file_path.suffix.lower().strip(".")
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return "image_text"


def extract_platform_account(json_file: Path, raw_root: Path) -> Tuple[str, str]:
    relative = json_file.relative_to(raw_root)
    parts = relative.parts
    if len(parts) >= 3:
        return parts[0], parts[1]
    return "unknown", "unknown"


def extract_hot_rows(raw_root: Path) -> List[Dict]:
    rows: List[Dict] = []
    for json_file in raw_root.rglob("*.json"):
        if json_file.name.endswith("info.json"):
            continue
        try:
            meta = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue

        platform, account = extract_platform_account(json_file, raw_root)
        content_type = classify_content(meta, json_file)
        text = first_non_empty(meta, ["content", "description", "caption", "title", "tweet_text", "tweet_content"])
        view_count = to_int(meta.get("view_count") or meta.get("views") or meta.get("video_view_count"))
        like_count = to_int(meta.get("favorite_count") or meta.get("likes") or meta.get("like_count"))
        comment_count = to_int(meta.get("reply_count") or meta.get("comment_count"))
        share_count = to_int(meta.get("retweet_count") or meta.get("share_count") or meta.get("quote_count"))
        post_url = first_non_empty(meta, ["post_url", "tweet_url", "url", "shortcode_url"])
        post_id = str(
            meta.get("post_id")
            or meta.get("tweet_id")
            or meta.get("media_id")
            or meta.get("id")
            or json_file.stem
        )

        hot_score = view_count + like_count * 30 + comment_count * 50 + share_count * 80
        rows.append(
            {
                "platform": platform,
                "account": account,
                "post_id": post_id,
                "post_url": post_url,
                "content_type": content_type,
                "view_count": view_count,
                "like_count": like_count,
                "comment_count": comment_count,
                "share_count": share_count,
                "hot_score": hot_score,
                "text_preview": text[:220],
                "meta_file": str(json_file),
            }
        )
    return rows


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "platform",
        "account",
        "post_id",
        "post_url",
        "content_type",
        "view_count",
        "like_count",
        "comment_count",
        "share_count",
        "hot_score",
        "text_preview",
        "meta_file",
    ]
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_markdown(path: Path, rows: List[Dict], top_n: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 全网高热作品榜单",
        "",
        f"- 统计样本数: {len(rows)}",
        f"- TOP 导出数: {min(top_n, len(rows))}",
        "",
        "| Rank | Platform | Account | Type | Views | Likes | Comments | Shares | HotScore | Link |",
        "|---:|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for index, row in enumerate(rows[:top_n], start=1):
        url = row["post_url"] or ""
        link = f"[post]({url})" if url else "-"
        lines.append(
            f"| {index} | {row['platform']} | {row['account']} | {row['content_type']} | "
            f"{row['view_count']} | {row['like_count']} | {row['comment_count']} | "
            f"{row['share_count']} | {row['hot_score']} | {link} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出 X/IG 高阅读量作品榜")
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/archive/hot_content"))
    parser.add_argument("--top-n", type=int, default=200)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = extract_hot_rows(args.raw_root)
    rows.sort(key=lambda item: item["hot_score"], reverse=True)

    write_json(args.out_dir / "top_works.json", rows[: args.top_n])
    write_csv(args.out_dir / "top_works.csv", rows[: args.top_n])
    write_markdown(args.out_dir / "top_works.md", rows, args.top_n)

    summary = {
        "total_posts_scanned": len(rows),
        "top_n_exported": min(args.top_n, len(rows)),
        "output_dir": str(args.out_dir),
    }
    write_json(args.out_dir / "summary.json", summary)
    print(f"[hot] scanned={len(rows)} exported={summary['top_n_exported']} out={args.out_dir}")


if __name__ == "__main__":
    main()
