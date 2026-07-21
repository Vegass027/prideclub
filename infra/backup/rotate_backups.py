"""Ротация бэкапов: 7 daily + 4 weekly + 12 monthly."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

import boto3


def parse_ts(name: str) -> datetime | None:
    # backup_20250120_153045.sql.gz.age
    try:
        ts = name.split("_")[1].split(".")[0]
        return datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except (IndexError, ValueError):
        return None


def list_objects(client, bucket: str, prefix: str) -> list[dict]:
    paginator = client.get_paginator("list_objects_v2")
    objs: list[dict] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        objs.extend(page.get("Contents", []))
    return objs


def group_by_bucket(
    objects: list[dict], bucket_seconds: int, now: datetime
) -> list[tuple[datetime, list[dict]]]:
    groups: dict[int, list[dict]] = {}
    for obj in objects:
        ts = parse_ts(obj["Key"].split("/")[-1])
        if ts is None:
            continue
        bucket_id = int(ts.timestamp()) // bucket_seconds
        groups.setdefault(bucket_id, []).append(obj)
    return [
        (datetime.fromtimestamp(k * bucket_seconds, tz=timezone.utc), v)
        for k, v in groups.items()
    ]


def rotate(
    bucket: str,
    endpoint_url: str,
    keep_daily: int,
    keep_weekly: int,
    keep_monthly: int,
) -> None:
    client = boto3.client("s3", endpoint_url=endpoint_url)
    now = datetime.now(timezone.utc)

    daily = list_objects(client, bucket, "daily/")
    weekly = list_objects(client, bucket, "weekly/")
    monthly = list_objects(client, bucket, "monthly/")

    def keep_newest(objs: list[dict], keep: int) -> set[str]:
        sorted_objs = sorted(
            objs,
            key=lambda o: parse_ts(o["Key"].split("/")[-1]) or now,
            reverse=True,
        )
        return {o["Key"] for o in sorted_objs[:keep]}

    keep = set()
    keep |= keep_newest(daily, keep_daily)
    keep |= keep_newest(weekly, keep_weekly)
    keep |= keep_newest(monthly, keep_monthly)

    for objs in (daily, weekly, monthly):
        for obj in objs:
            if obj["Key"] not in keep:
                client.delete_object(Bucket=bucket, Key=obj["Key"])
                print(f"deleted: {obj['Key']}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--endpoint-url", default=None)
    parser.add_argument("--keep-daily", type=int, default=7)
    parser.add_argument("--keep-weekly", type=int, default=4)
    parser.add_argument("--keep-monthly", type=int, default=12)
    args = parser.parse_args()

    rotate(
        bucket=args.bucket,
        endpoint_url=args.endpoint_url,
        keep_daily=args.keep_daily,
        keep_weekly=args.keep_weekly,
        keep_monthly=args.keep_monthly,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())