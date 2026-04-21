#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import imagehash
import yaml
from PIL import Image, ImageFilter


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


@dataclass
class Asset:
    path: Path
    kind: str
    width: int
    height: int
    file_size: int
    full_hash: imagehash.ImageHash
    center_hash: imagehash.ImageHash
    corner_edge_density: float
    platform: str
    account: str

    @property
    def resolution(self) -> int:
        return max(1, self.width * self.height)

    @property
    def quality_tuple(self) -> Tuple[int, int, float]:
        return (self.resolution, self.file_size, -self.corner_edge_density)


def run_cmd(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def load_config(config_path: Path) -> Dict:
    with config_path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def normalize_accounts(raw_config: Dict) -> Dict[str, List[str]]:
    twitter_accounts = raw_config.get("twitter", []) or []
    instagram_accounts = raw_config.get("instagram", []) or []
    return {
        "twitter": [account.strip().lstrip("@") for account in twitter_accounts if account.strip()],
        "instagram": [account.strip().lstrip("@") for account in instagram_accounts if account.strip()],
    }


def build_profile_url(platform: str, account: str) -> str:
    if platform == "twitter":
        return f"https://x.com/{account}"
    if platform == "instagram":
        return f"https://www.instagram.com/{account}/"
    raise ValueError(f"Unsupported platform: {platform}")


def crawl_accounts(raw_root: Path, config: Dict) -> None:
    account_map = normalize_accounts(config)
    crawl_config = config.get("crawl", {}) or {}
    max_items = int(crawl_config.get("max_items_per_account", 40))
    sleep_request_seconds = float(crawl_config.get("sleep_request_seconds", 1.2))
    cookies_file = os.environ.get("GALLERY_DL_COOKIES_FILE")

    if shutil.which("gallery-dl") is None:
        raise RuntimeError("gallery-dl 未安装，请先执行: pip install -r requirements.txt")

    for platform, accounts in account_map.items():
        for account in accounts:
            output_dir = raw_root / platform / account
            output_dir.mkdir(parents=True, exist_ok=True)
            profile_url = build_profile_url(platform, account)

            cmd = [
                "gallery-dl",
                "--dest",
                str(output_dir),
                "--write-metadata",
                "--write-info-json",
                "--range",
                f"1-{max_items}",
                "--sleep-request",
                str(sleep_request_seconds),
                profile_url,
            ]
            if cookies_file and Path(cookies_file).exists():
                cmd.extend(["--cookies", cookies_file])

            print(f"[crawl] {platform}/{account} -> {profile_url}")
            result = run_cmd(cmd, check=False)
            if result.returncode != 0:
                print(f"[warn] 抓取失败: {platform}/{account}")
                print(result.stderr[-4000:])


def corner_edge_density(image: Image.Image) -> float:
    gray = image.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    width, height = edges.size
    pixels = edges.load()

    corner_w = max(12, int(width * 0.18))
    corner_h = max(12, int(height * 0.18))
    regions = [
        (0, 0, corner_w, corner_h),
        (width - corner_w, 0, width, corner_h),
        (0, height - corner_h, corner_w, height),
        (width - corner_w, height - corner_h, width, height),
    ]

    densities: List[float] = []
    threshold = 32
    for left, top, right, bottom in regions:
        total = max(1, (right - left) * (bottom - top))
        edge_count = 0
        for y in range(top, bottom):
            for x in range(left, right):
                if pixels[x, y] > threshold:
                    edge_count += 1
        densities.append(edge_count / total)

    return float(sum(densities) / len(densities))


def center_crop_pil(img: Image.Image, ratio: float = 0.8) -> Image.Image:
    width, height = img.size
    crop_w = int(width * ratio)
    crop_h = int(height * ratio)
    left = (width - crop_w) // 2
    top = (height - crop_h) // 2
    return img.crop((left, top, left + crop_w, top + crop_h))


def probe_video_resolution(video_path: Path) -> Tuple[int, int]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=s=x:p=0",
        str(video_path),
    ]
    result = run_cmd(cmd, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return (0, 0)
    try:
        width_str, height_str = result.stdout.strip().split("x")
        return int(width_str), int(height_str)
    except Exception:
        return (0, 0)


def extract_video_keyframe(video_path: Path, temp_dir: Path) -> Optional[Path]:
    frame_path = temp_dir / f"{video_path.stem}_frame.jpg"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        "thumbnail,scale=640:-1",
        "-frames:v",
        "1",
        str(frame_path),
    ]
    result = run_cmd(cmd, check=False)
    if result.returncode != 0 or not frame_path.exists():
        return None
    return frame_path


def parse_platform_account(path: Path, raw_root: Path) -> Tuple[str, str]:
    relative = path.relative_to(raw_root)
    parts = relative.parts
    if len(parts) >= 2:
        return parts[0], parts[1]
    return ("unknown", "unknown")


def iter_media_files(raw_root: Path) -> Iterable[Path]:
    for file_path in raw_root.rglob("*"):
        if not file_path.is_file():
            continue
        extension = file_path.suffix.lower()
        if extension in IMAGE_EXTENSIONS or extension in VIDEO_EXTENSIONS:
            yield file_path


def build_asset(file_path: Path, raw_root: Path, temp_dir: Path) -> Optional[Asset]:
    extension = file_path.suffix.lower()
    platform, account = parse_platform_account(file_path, raw_root)
    file_size = file_path.stat().st_size

    try:
        if extension in IMAGE_EXTENSIONS:
            image = Image.open(file_path).convert("RGB")
            width, height = image.size
            return Asset(
                path=file_path,
                kind="image",
                width=width,
                height=height,
                file_size=file_size,
                full_hash=imagehash.phash(image),
                center_hash=imagehash.phash(center_crop_pil(image)),
                corner_edge_density=corner_edge_density(image),
                platform=platform,
                account=account,
            )

        if extension in VIDEO_EXTENSIONS:
            frame_path = extract_video_keyframe(file_path, temp_dir)
            if frame_path is None:
                return None
            image = Image.open(frame_path).convert("RGB")
            width, height = probe_video_resolution(file_path)
            return Asset(
                path=file_path,
                kind="video",
                width=width,
                height=height,
                file_size=file_size,
                full_hash=imagehash.phash(image),
                center_hash=imagehash.phash(center_crop_pil(image)),
                corner_edge_density=corner_edge_density(image),
                platform=platform,
                account=account,
            )
    except Exception as error:
        print(f"[warn] 解析失败 {file_path}: {error}")
        return None

    return None


def hamming_distance(left: imagehash.ImageHash, right: imagehash.ImageHash) -> int:
    return int(left - right)


def deduplicate_assets(
    assets: List[Asset],
    rejected_root: Path,
    center_threshold: int = 6,
    full_threshold: int = 4,
) -> List[Asset]:
    grouped: Dict[str, List[List[Asset]]] = {"image": [], "video": []}

    for asset in assets:
        groups = grouped[asset.kind]
        matched_group: Optional[List[Asset]] = None
        for group in groups:
            representative = group[0]
            if hamming_distance(asset.center_hash, representative.center_hash) <= center_threshold:
                matched_group = group
                break
        if matched_group is None:
            groups.append([asset])
        else:
            matched_group.append(asset)

    kept_assets: List[Asset] = []
    rejected_root.mkdir(parents=True, exist_ok=True)

    for kind, groups in grouped.items():
        for group in groups:
            if len(group) == 1:
                kept_assets.append(group[0])
                continue

            group.sort(key=lambda item: item.quality_tuple, reverse=True)
            winner = group[0]
            kept_assets.append(winner)

            for loser in group[1:]:
                if hamming_distance(winner.full_hash, loser.full_hash) <= full_threshold or hamming_distance(
                    winner.center_hash, loser.center_hash
                ) <= center_threshold:
                    target = rejected_root / loser.platform / loser.account / kind
                    target.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(loser.path), str(target / loser.path.name))

    return kept_assets


def metadata_candidates(asset_path: Path) -> List[Path]:
    return [
        asset_path.with_suffix(asset_path.suffix + ".json"),
        asset_path.with_suffix(".json"),
    ]


def extract_caption_from_metadata(meta_path: Path) -> str:
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return ""

    keys = [
        "content",
        "description",
        "caption",
        "text",
        "title",
        "tweet_content",
        "tweet_text",
    ]
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def organize_assets(kept_assets: List[Asset], archive_root: Path) -> None:
    image_root = archive_root / "images"
    video_root = archive_root / "videos"
    text_root = archive_root / "captions"

    caption_hashes: set[str] = set()

    for asset in kept_assets:
        destination_base = image_root if asset.kind == "image" else video_root
        destination_dir = destination_base / asset.platform / asset.account
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination_path = destination_dir / asset.path.name
        shutil.copy2(asset.path, destination_path)

        caption = ""
        for meta_path in metadata_candidates(asset.path):
            if meta_path.exists():
                caption = extract_caption_from_metadata(meta_path)
                if caption:
                    break

        if caption:
            signature = sha1_text(caption)
            if signature in caption_hashes:
                continue
            caption_hashes.add(signature)

            caption_dir = text_root / asset.platform / asset.account
            caption_dir.mkdir(parents=True, exist_ok=True)
            caption_file = caption_dir / f"{asset.path.stem}.txt"
            caption_file.write_text(caption + "\n", encoding="utf-8")


def run_pipeline(config_path: Path, data_root: Path) -> None:
    raw_root = data_root / "raw"
    archive_root = data_root / "archive"
    rejected_root = data_root / "rejected"

    raw_root.mkdir(parents=True, exist_ok=True)
    archive_root.mkdir(parents=True, exist_ok=True)
    rejected_root.mkdir(parents=True, exist_ok=True)

    config = load_config(config_path)
    crawl_accounts(raw_root, config)

    with tempfile.TemporaryDirectory(prefix="crawler_frames_") as temp_name:
        temp_dir = Path(temp_name)
        assets: List[Asset] = []
        for media_file in iter_media_files(raw_root):
            asset = build_asset(media_file, raw_root, temp_dir)
            if asset:
                assets.append(asset)

        kept_assets = deduplicate_assets(assets, rejected_root)
        organize_assets(kept_assets, archive_root)

        print(f"[done] 原始素材总数: {len(assets)}")
        print(f"[done] 去重后素材数: {len(kept_assets)}")
        print(f"[done] 归档目录: {archive_root}")
        print(f"[done] 剔除目录: {rejected_root}")

        fail_on_empty = os.environ.get("FAIL_ON_EMPTY", "1").strip().lower() in {"1", "true", "yes"}
        if fail_on_empty and len(kept_assets) == 0:
            raise RuntimeError("抓取结果为空：请检查账号配置和 cookies 登录态是否有效。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="云端社媒素材抓取+去重+归档")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/accounts.example.yaml"),
        help="账号配置 YAML 路径",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data"),
        help="数据输出根目录",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_pipeline(args.config, args.data_root)


if __name__ == "__main__":
    main()
