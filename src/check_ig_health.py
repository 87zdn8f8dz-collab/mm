#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from discover_accounts import FALLBACK_IG_ACCOUNTS, run_probe_command


def load_config_accounts(config_path: Path) -> List[str]:
    if not config_path.exists():
        return []
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    raw_accounts = payload.get("instagram", []) or []
    return [str(item).strip().lstrip("@") for item in raw_accounts if str(item).strip()]


def build_candidates(config_accounts: List[str], target_count: int) -> List[str]:
    merged = config_accounts + FALLBACK_IG_ACCOUNTS
    deduped: List[str] = []
    seen: set[str] = set()
    for account in merged:
        if account in seen:
            continue
        seen.add(account)
        deduped.append(account)
        if len(deduped) >= target_count:
            break
    return deduped


def run_healthcheck(
    accounts: List[str],
    cookies_file: Path | None,
    timeout_seconds: int,
    max_workers: int,
    per_account_limit: int,
) -> Tuple[List[str], Dict[str, str]]:
    success: List[str] = []
    errors: Dict[str, str] = {}

    def run_one(account: str) -> Tuple[str, bool, str]:
        ok, message = run_probe_command(
            platform="instagram",
            account=account,
            cookies_file=cookies_file,
            timeout_seconds=timeout_seconds,
            per_account_limit=per_account_limit,
        )
        return account, ok, message

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = [executor.submit(run_one, account) for account in accounts]
        for future in concurrent.futures.as_completed(futures):
            account, ok, message = future.result()
            if ok:
                success.append(account)
            else:
                errors[account] = message

    success_set = set(success)
    ordered_success = [account for account in accounts if account in success_set]
    return ordered_success, errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IG 登录态健康检查")
    parser.add_argument("--config", type=Path, default=Path("config/accounts.example.yaml"))
    parser.add_argument("--report", type=Path, default=Path("data/archive/ig_health/ig_health_report.json"))
    parser.add_argument("--target-count", type=int, default=25)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--max-workers", type=int, default=6)
    parser.add_argument("--probe-limit", type=int, default=1)
    parser.add_argument("--require-success-min", type=int, default=1)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cookies_path_text = os.environ.get("GALLERY_DL_COOKIES_FILE", "").strip()
    cookies_file = Path(cookies_path_text) if cookies_path_text else None

    config_accounts = load_config_accounts(args.config)
    candidates = build_candidates(config_accounts=config_accounts, target_count=args.target_count)
    success, errors = run_healthcheck(
        accounts=candidates,
        cookies_file=cookies_file,
        timeout_seconds=args.timeout_seconds,
        max_workers=args.max_workers,
        per_account_limit=args.probe_limit,
    )

    status = "healthy" if len(success) >= args.require_success_min else "unhealthy"
    report = {
        "status": status,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "cookies_file_present": bool(cookies_file and cookies_file.exists()),
        "config_path": str(args.config),
        "candidate_count": len(candidates),
        "success_count": len(success),
        "require_success_min": args.require_success_min,
        "success_accounts": success,
        "error_count": len(errors),
        "errors": errors,
    }

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[ig-health] status={status} checked={len(candidates)} "
        f"success={len(success)} report={args.report}"
    )

    if len(success) < args.require_success_min:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
