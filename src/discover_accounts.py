#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml


DEFAULT_X_KEYWORDS = [
    "命理",
    "五行",
    "修行",
    "玄学",
    "道家",
    "风水",
    "八字",
    "占星",
    "塔罗",
    "灵性",
    "taoism",
    "bazi",
    "fengshui",
    "astrology",
    "tarot",
    "spiritual awakening",
]

DEFAULT_IG_TAGS = [
    "命理",
    "五行",
    "修行",
    "玄学",
    "道家",
    "风水",
    "八字",
    "占星",
    "塔罗",
    "灵性",
    "taoism",
    "bazi",
    "fengshui",
    "astrology",
    "tarot",
    "spirituality",
]

FALLBACK_X_ACCOUNTS = [
    "AstrologyZone",
    "ChaniNicholas",
    "theastrotwins",
    "astrologyzone",
    "tarotdotcom",
    "AstrologyHub",
    "horoscopecom",
    "thepattern",
    "CoStarAstrology",
    "yasminboland",
    "astrologyanswers",
    "moonomens",
    "CafeAstrology",
    "astrologyking",
    "spiritlibrary",
    "mindbodygreen",
    "BiddyTarot",
    "DailyOM",
    "RisingWoman",
    "soulguide",
    "fengshui",
    "taoism",
    "meditation",
    "zenhabits",
    "tricyclemag",
]

FALLBACK_IG_ACCOUNTS = [
    "astrologyzone",
    "chaninicholas",
    "theastrotwins",
    "costarastrology",
    "astrologyanswers",
    "moonomens",
    "yasminboland",
    "biddytarot",
    "tarotdotcom",
    "cafeastrology",
    "mindbodygreen",
    "risingwoman",
    "spiritlibrary",
    "dailyom",
    "omstars",
    "headspace",
    "calm",
    "zenhabits",
    "tricyclemag",
    "taoism",
    "fengshui",
    "meditation",
    "energyhealing",
    "soulpath",
    "spirituality",
]


X_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{2,30}$")
IG_HANDLE_RE = re.compile(r"^[A-Za-z0-9._]{2,30}$")


def run_dump_json(
    url: str,
    cookies_file: Optional[Path],
    limit: int,
    timeout_seconds: int,
) -> Tuple[Optional[List], Optional[str]]:
    cmd = ["gallery-dl", "--range", f"1-{limit}", "--dump-json"]
    if cookies_file and cookies_file.exists():
        cmd.extend(["--cookies", str(cookies_file)])
    cmd.append(url)
    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return None, f"request timeout after {timeout_seconds}s"
    if result.returncode != 0:
        return None, result.stderr[-3000:]
    payload = result.stdout.strip()
    if not payload:
        return [], None
    try:
        return json.loads(payload), None
    except Exception as error:
        return None, f"JSON parse failed: {error}"


def discover_x_accounts(
    keywords: List[str],
    cookies_file: Optional[Path],
    per_query_limit: int,
    timeout_seconds: int,
) -> Counter:
    counter: Counter = Counter()
    for keyword in keywords:
        query = urllib.parse.quote(keyword)
        url = f"https://x.com/search?q={query}&src=typed_query&f=live"
        data, error = run_dump_json(url, cookies_file, per_query_limit, timeout_seconds)
        if error:
            print(f"[x][warn] {keyword}: {error}")
            continue

        matched = 0
        for item in data or []:
            if not (isinstance(item, list) and len(item) >= 2 and isinstance(item[1], dict)):
                continue
            meta = item[1]
            author = meta.get("author") or meta.get("user") or {}
            handle = str(author.get("name") or "").strip()
            if X_HANDLE_RE.match(handle):
                counter[handle] += 1
                matched += 1
        print(f"[x] {keyword}: +{matched}")
    return counter


def discover_ig_accounts(
    tags: List[str],
    cookies_file: Optional[Path],
    per_tag_limit: int,
    timeout_seconds: int,
) -> Counter:
    counter: Counter = Counter()
    for tag in tags:
        encoded = urllib.parse.quote(tag)
        url = f"https://www.instagram.com/explore/tags/{encoded}/"
        data, error = run_dump_json(url, cookies_file, per_tag_limit, timeout_seconds)
        if error:
            print(f"[ig][warn] {tag}: {error}")
            continue

        matched = 0
        for item in data or []:
            if not (isinstance(item, list) and len(item) >= 2 and isinstance(item[1], dict)):
                continue
            meta = item[1]
            username = str(meta.get("username") or "").strip()
            if IG_HANDLE_RE.match(username):
                counter[username] += 1
                matched += 1
        print(f"[ig] {tag}: +{matched}")
    return counter


def choose_accounts(x_counter: Counter, ig_counter: Counter, target_total: int) -> Tuple[List[str], List[str]]:
    x_ranked = [handle for handle, _ in x_counter.most_common(300)]
    ig_ranked = [handle for handle, _ in ig_counter.most_common(300)]

    x_target = target_total // 2
    ig_target = target_total - x_target
    x_selected = x_ranked[:x_target]
    ig_selected = ig_ranked[:ig_target]

    x_index = len(x_selected)
    ig_index = len(ig_selected)
    while len(x_selected) + len(ig_selected) < target_total:
        progressed = False
        if len(x_selected) <= len(ig_selected):
            if x_index < len(x_ranked):
                x_selected.append(x_ranked[x_index])
                x_index += 1
                progressed = True
            elif ig_index < len(ig_ranked):
                ig_selected.append(ig_ranked[ig_index])
                ig_index += 1
                progressed = True
        else:
            if ig_index < len(ig_ranked):
                ig_selected.append(ig_ranked[ig_index])
                ig_index += 1
                progressed = True
            elif x_index < len(x_ranked):
                x_selected.append(x_ranked[x_index])
                x_index += 1
                progressed = True
        if not progressed:
            break

    x_existing = set(x_selected)
    ig_existing = set(ig_selected)
    fallback_x_index = 0
    fallback_ig_index = 0
    while len(x_selected) + len(ig_selected) < target_total:
        progressed = False
        if len(x_selected) <= len(ig_selected):
            while fallback_x_index < len(FALLBACK_X_ACCOUNTS):
                account = FALLBACK_X_ACCOUNTS[fallback_x_index]
                fallback_x_index += 1
                if account in x_existing:
                    continue
                x_selected.append(account)
                x_existing.add(account)
                progressed = True
                break
            if not progressed:
                while fallback_ig_index < len(FALLBACK_IG_ACCOUNTS):
                    account = FALLBACK_IG_ACCOUNTS[fallback_ig_index]
                    fallback_ig_index += 1
                    if account in ig_existing:
                        continue
                    ig_selected.append(account)
                    ig_existing.add(account)
                    progressed = True
                    break
        else:
            while fallback_ig_index < len(FALLBACK_IG_ACCOUNTS):
                account = FALLBACK_IG_ACCOUNTS[fallback_ig_index]
                fallback_ig_index += 1
                if account in ig_existing:
                    continue
                ig_selected.append(account)
                ig_existing.add(account)
                progressed = True
                break
            if not progressed:
                while fallback_x_index < len(FALLBACK_X_ACCOUNTS):
                    account = FALLBACK_X_ACCOUNTS[fallback_x_index]
                    fallback_x_index += 1
                    if account in x_existing:
                        continue
                    x_selected.append(account)
                    x_existing.add(account)
                    progressed = True
                    break
        if not progressed:
            break

    return x_selected, ig_selected


def write_accounts_yaml(
    output_path: Path,
    x_accounts: List[str],
    ig_accounts: List[str],
    max_items_per_account: int,
    sleep_request_seconds: float,
) -> None:
    payload = {
        "twitter": x_accounts,
        "instagram": ig_accounts,
        "crawl": {
            "max_items_per_account": max_items_per_account,
            "sleep_request_seconds": sleep_request_seconds,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def write_discovery_report(
    report_path: Path,
    x_counter: Counter,
    ig_counter: Counter,
    x_accounts: List[str],
    ig_accounts: List[str],
    x_keywords: List[str],
    ig_tags: List[str],
) -> None:
    report = {
        "x_keywords": x_keywords,
        "ig_tags": ig_tags,
        "x_discovered_unique": len(x_counter),
        "ig_discovered_unique": len(ig_counter),
        "x_selected": x_accounts,
        "ig_selected": ig_accounts,
        "x_score": dict(x_counter),
        "ig_score": dict(ig_counter),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="自动发现 X/IG 垂类账号并生成 accounts.yaml")
    parser.add_argument("--output", type=Path, default=Path("config/accounts.discovered.yaml"))
    parser.add_argument("--report", type=Path, default=Path("data/archive/discovery/discovery_report.json"))
    parser.add_argument("--target-total", type=int, default=50)
    parser.add_argument("--per-query-limit", type=int, default=35)
    parser.add_argument("--timeout-seconds", type=int, default=45)
    parser.add_argument("--max-items-per-account", type=int, default=20)
    parser.add_argument("--sleep-request-seconds", type=float, default=1.5)
    parser.add_argument("--cookies-file", type=Path, default=None)
    parser.add_argument("--x-keywords", type=str, default="|".join(DEFAULT_X_KEYWORDS))
    parser.add_argument("--ig-tags", type=str, default="|".join(DEFAULT_IG_TAGS))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    x_keywords = [item.strip() for item in args.x_keywords.split("|") if item.strip()]
    ig_tags = [item.strip() for item in args.ig_tags.split("|") if item.strip()]

    x_counter = discover_x_accounts(
        x_keywords,
        args.cookies_file,
        args.per_query_limit,
        args.timeout_seconds,
    )
    ig_counter = discover_ig_accounts(
        ig_tags,
        args.cookies_file,
        args.per_query_limit,
        args.timeout_seconds,
    )
    x_accounts, ig_accounts = choose_accounts(x_counter, ig_counter, args.target_total)

    write_accounts_yaml(
        output_path=args.output,
        x_accounts=x_accounts,
        ig_accounts=ig_accounts,
        max_items_per_account=args.max_items_per_account,
        sleep_request_seconds=args.sleep_request_seconds,
    )
    write_discovery_report(
        report_path=args.report,
        x_counter=x_counter,
        ig_counter=ig_counter,
        x_accounts=x_accounts,
        ig_accounts=ig_accounts,
        x_keywords=x_keywords,
        ig_tags=ig_tags,
    )

    print(f"[discover] x_unique={len(x_counter)} ig_unique={len(ig_counter)}")
    print(f"[discover] selected x={len(x_accounts)} ig={len(ig_accounts)} total={len(x_accounts)+len(ig_accounts)}")
    print(f"[discover] output={args.output}")
    print(f"[discover] report={args.report}")


if __name__ == "__main__":
    main()
