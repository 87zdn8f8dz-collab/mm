#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
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


def build_profile_url(platform: str, account: str) -> str:
    if platform == "twitter":
        return f"https://x.com/{account}"
    if platform == "instagram":
        return f"https://www.instagram.com/{account}/"
    raise ValueError(f"Unsupported platform: {platform}")


def is_probe_success(data: Optional[List], error: Optional[str]) -> bool:
    if error:
        return False
    if data is None:
        return False
    return len(data) > 0


def probe_accounts(
    platform: str,
    accounts: List[str],
    cookies_file: Optional[Path],
    timeout_seconds: int,
    max_workers: int,
    per_account_limit: int,
) -> Tuple[List[str], Dict[str, str]]:
    candidates = list(dict.fromkeys([account.strip() for account in accounts if account.strip()]))
    errors: Dict[str, str] = {}
    success: List[str] = []

    if not candidates:
        return success, errors

    def run_one(account: str) -> Tuple[str, bool, str]:
        url = build_profile_url(platform, account)
        data, error = run_dump_json(
            url=url,
            cookies_file=cookies_file,
            limit=per_account_limit,
            timeout_seconds=timeout_seconds,
        )
        if is_probe_success(data, error):
            return account, True, ""
        message = (error or "empty result").strip()
        return account, False, message[:500]

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = [executor.submit(run_one, account) for account in candidates]
        for future in concurrent.futures.as_completed(futures):
            account, ok, message = future.result()
            if ok:
                success.append(account)
            else:
                errors[account] = message

    success_set = set(success)
    ordered_success = [account for account in candidates if account in success_set]
    return ordered_success, errors


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

    x_existing = set(x_selected)
    ig_existing = set(ig_selected)
    fallback_x_index = 0
    fallback_ig_index = 0

    while len(x_selected) < x_target:
        progressed = False
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

    while len(ig_selected) < ig_target:
        progressed = False
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
            break

    x_extra_index = len(x_selected)
    ig_extra_index = len(ig_selected)
    while len(x_selected) + len(ig_selected) < target_total:
        progressed = False
        if len(x_selected) <= len(ig_selected):
            while x_extra_index < len(x_ranked):
                account = x_ranked[x_extra_index]
                x_extra_index += 1
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
            while ig_extra_index < len(ig_ranked):
                account = ig_ranked[ig_extra_index]
                ig_extra_index += 1
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


def choose_from_validated_candidates(
    preferred: List[str],
    validated: List[str],
    target_count: int,
) -> List[str]:
    if target_count <= 0:
        return []
    validated_set = set(validated)
    selected: List[str] = []
    seen: set[str] = set()

    for account in preferred:
        if account in validated_set and account not in seen:
            selected.append(account)
            seen.add(account)
            if len(selected) >= target_count:
                return selected

    for account in validated:
        if account in seen:
            continue
        selected.append(account)
        seen.add(account)
        if len(selected) >= target_count:
            return selected

    for account in preferred:
        if account in seen:
            continue
        selected.append(account)
        seen.add(account)
        if len(selected) >= target_count:
            return selected

    return selected[:target_count]


def write_accounts_yaml(
    output_path: Path,
    x_accounts: List[str],
    ig_accounts: List[str],
    max_items_per_account: int,
    sleep_request_seconds: float,
    command_timeout_seconds: int,
    max_workers: int,
    instagram_max_items_per_account: int,
    instagram_sleep_request_seconds: float,
    instagram_command_timeout_seconds: int,
    instagram_max_workers: int,
    retries: int,
    retry_backoff_seconds: float,
) -> None:
    payload = {
        "twitter": x_accounts,
        "instagram": ig_accounts,
        "crawl": {
            "max_items_per_account": max_items_per_account,
            "sleep_request_seconds": sleep_request_seconds,
            "command_timeout_seconds": command_timeout_seconds,
            "max_workers": max_workers,
            "instagram_max_items_per_account": instagram_max_items_per_account,
            "instagram_sleep_request_seconds": instagram_sleep_request_seconds,
            "instagram_command_timeout_seconds": instagram_command_timeout_seconds,
            "instagram_max_workers": instagram_max_workers,
            "retries": retries,
            "retry_backoff_seconds": retry_backoff_seconds,
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
    ig_probe_success: List[str],
    ig_probe_errors: Dict[str, str],
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
        "ig_probe_success_count": len(ig_probe_success),
        "ig_probe_success_accounts": ig_probe_success,
        "ig_probe_error_count": len(ig_probe_errors),
        "ig_probe_errors": ig_probe_errors,
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
    parser.add_argument("--command-timeout-seconds", type=int, default=180)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--instagram-max-items-per-account", type=int, default=6)
    parser.add_argument("--instagram-sleep-request-seconds", type=float, default=2.2)
    parser.add_argument("--instagram-command-timeout-seconds", type=int, default=180)
    parser.add_argument("--instagram-max-workers", type=int, default=2)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--retry-backoff-seconds", type=float, default=4.0)
    parser.add_argument("--ig-probe-timeout-seconds", type=int, default=20)
    parser.add_argument("--ig-probe-max-workers", type=int, default=8)
    parser.add_argument("--ig-probe-limit", type=int, default=1)
    parser.add_argument("--ig-probe-max-candidates", type=int, default=80)
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

    ig_candidate_ranked = [handle for handle, _ in ig_counter.most_common(300)]
    ig_probe_candidates = list(
        dict.fromkeys((ig_candidate_ranked + ig_accounts + FALLBACK_IG_ACCOUNTS)[: args.ig_probe_max_candidates])
    )
    ig_probe_success, ig_probe_errors = probe_accounts(
        platform="instagram",
        accounts=ig_probe_candidates,
        cookies_file=args.cookies_file,
        timeout_seconds=args.ig_probe_timeout_seconds,
        max_workers=args.ig_probe_max_workers,
        per_account_limit=args.ig_probe_limit,
    )
    ig_target = args.target_total - (args.target_total // 2)
    ig_accounts = choose_from_validated_candidates(
        preferred=ig_accounts + ig_probe_candidates,
        validated=ig_probe_success,
        target_count=ig_target,
    )

    write_accounts_yaml(
        output_path=args.output,
        x_accounts=x_accounts,
        ig_accounts=ig_accounts,
        max_items_per_account=args.max_items_per_account,
        sleep_request_seconds=args.sleep_request_seconds,
        command_timeout_seconds=args.command_timeout_seconds,
        max_workers=args.max_workers,
        instagram_max_items_per_account=args.instagram_max_items_per_account,
        instagram_sleep_request_seconds=args.instagram_sleep_request_seconds,
        instagram_command_timeout_seconds=args.instagram_command_timeout_seconds,
        instagram_max_workers=args.instagram_max_workers,
        retries=args.retries,
        retry_backoff_seconds=args.retry_backoff_seconds,
    )
    write_discovery_report(
        report_path=args.report,
        x_counter=x_counter,
        ig_counter=ig_counter,
        x_accounts=x_accounts,
        ig_accounts=ig_accounts,
        x_keywords=x_keywords,
        ig_tags=ig_tags,
        ig_probe_success=ig_probe_success,
        ig_probe_errors=ig_probe_errors,
    )

    print(f"[discover] x_unique={len(x_counter)} ig_unique={len(ig_counter)}")
    print(
        f"[discover] ig_probe_success={len(ig_probe_success)} "
        f"ig_probe_errors={len(ig_probe_errors)} checked={len(ig_probe_candidates)}"
    )
    print(f"[discover] selected x={len(x_accounts)} ig={len(ig_accounts)} total={len(x_accounts)+len(ig_accounts)}")
    print(f"[discover] output={args.output}")
    print(f"[discover] report={args.report}")


if __name__ == "__main__":
    main()
