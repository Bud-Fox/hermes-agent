#!/usr/bin/env python3
"""Migration utility for HR-124: fix cron jobs where deliver='origin' but origin is null.

Scans cron jobs.json files across profiles and base directory, prints affected jobs,
and safely updates their deliver targets to an explicit safe default ('local' or
remaining targets). Jobs are never deleted.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path
from typing import Any, Optional


def _rewrite_deliver_target(deliver_val: Optional[str]) -> tuple[Optional[str], bool]:
    """Rewrite deliver value removing 'origin'.

    Returns (new_value, changed).
    """
    if not deliver_val:
        return deliver_val, False
    tokens = [t.strip() for t in deliver_val.split(",") if t.strip()]
    if not any(t.lower() == "origin" for t in tokens):
        return deliver_val, False

    remaining = [t for t in tokens if t.lower() != "origin"]
    new_val = ",".join(remaining) if remaining else "local"
    return new_val, True


def migrate_cron_jobs(
    base_dirs: Optional[list[str]] = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Scan and migrate cron jobs with deliver origin but null origin.

    Returns the list of modified job summaries.
    """
    if base_dirs is None:
        hermes_home = os.environ.get("HERMES_HOME", "/opt/hermes-clean/home")
        base_dirs = [
            hermes_home,
            "/opt/hermes-clean/home",
            os.path.expanduser("~/.hermes"),
            "/root/.hermes",
        ]
    # Deduplicate existing directories
    unique_dirs = []
    seen = set()
    for d in base_dirs:
        p = Path(d).resolve()
        if p.exists() and str(p) not in seen:
            seen.add(str(p))
            unique_dirs.append(str(p))

    modified_jobs = []

    for base in unique_dirs:
        pattern = os.path.join(base, "**/jobs.json")
        for file_path in glob.glob(pattern, recursive=True):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue

            file_modified = False
            for job in data.get("jobs", []):
                # Check if origin is null / unconfigured
                if job.get("origin"):
                    continue

                old_deliver = job.get("deliver")
                new_deliver, deliver_changed = _rewrite_deliver_target(old_deliver)

                old_fail = job.get("failure_deliver")
                new_fail, fail_changed = _rewrite_deliver_target(old_fail)

                if deliver_changed or fail_changed:
                    job_info = {
                        "file": file_path,
                        "id": job.get("id"),
                        "name": job.get("name"),
                        "old_deliver": old_deliver,
                        "new_deliver": new_deliver,
                        "old_failure_deliver": old_fail,
                        "new_failure_deliver": new_fail,
                    }
                    modified_jobs.append(job_info)
                    if not dry_run:
                        if deliver_changed:
                            job["deliver"] = new_deliver
                        if fail_changed:
                            job["failure_deliver"] = new_fail
                        file_modified = True

            if file_modified and not dry_run:
                # Write back to file preserving format
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.write("\n")

    return modified_jobs


def main():
    parser = argparse.ArgumentParser(description="Migrate cron jobs with null origin")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without modifying files")
    parser.add_argument("dirs", nargs="*", help="Optional directories to scan")
    args = parser.parse_args()

    dirs = args.dirs if args.dirs else None
    results = migrate_cron_jobs(base_dirs=dirs, dry_run=args.dry_run)

    if not results:
        print("No jobs found with unconfigured origin.")
        return 0

    mode = "DRY RUN: would migrate" if args.dry_run else "Migrated"
    print(f"{mode} {len(results)} job(s):")
    for r in results:
        deliv_str = f"deliver: {r['old_deliver']} -> {r['new_deliver']}"
        fail_str = f", failure_deliver: {r['old_failure_deliver']} -> {r['new_failure_deliver']}" if r['old_failure_deliver'] != r['new_failure_deliver'] else ""
        print(f"  - [{r['id']}] {r['name']!r} in {r['file']}")
        print(f"      {deliv_str}{fail_str}")

    return 0


if __name__ == "__main__":
    main()
