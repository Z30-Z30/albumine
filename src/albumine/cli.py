"""Command-line interface for AlbuMine.

Runs the pipeline directly, without the task queue — handy for the initial
bulk-digitising of an album and for debugging. The web UI and the ARQ worker
share the same :class:`albumine.pipeline.Pipeline`.

Usage::

    albumine-cli scan [--force]   # process everything in the input folder
    albumine-cli health           # check the configured AI provider
    albumine-cli list             # list processed scan records
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from sqlmodel import Session, select

from albumine.ai import AIProviderError, build_provider
from albumine.config import EnhancementLevel, Settings, get_settings
from albumine.db import ScanRecord, create_db_engine, init_db, make_session_factory
from albumine.logging import configure_logging
from albumine.pipeline import Pipeline


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint. Returns a process exit code."""
    parser = argparse.ArgumentParser(prog="albumine-cli", description="AlbuMine CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser(
        "scan", help="Process every scan pair in the input folder"
    )
    scan_parser.add_argument(
        "--force", action="store_true", help="Re-process pairs already marked done"
    )
    scan_parser.add_argument(
        "--level",
        choices=[level.value for level in EnhancementLevel],
        default=None,
        help="Image-enhancement level (defaults to ALBUMINE_DEFAULT_ENHANCEMENT_LEVEL)",
    )
    subparsers.add_parser("health", help="Check the configured AI provider")
    subparsers.add_parser("list", help="List processed scan records")

    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    try:
        return asyncio.run(_dispatch(args, settings))
    except AIProviderError as exc:
        print(f"AI-Provider-Fehler: {exc}", file=sys.stderr)
        return 2


async def _dispatch(args: argparse.Namespace, settings: Settings) -> int:
    if args.command == "scan":
        level = EnhancementLevel(args.level) if args.level else None
        return await _cmd_scan(settings, force=args.force, level=level)
    if args.command == "health":
        return await _cmd_health(settings)
    if args.command == "list":
        return _cmd_list(settings)
    return 1  # pragma: no cover - argparse enforces a valid command


async def _cmd_scan(
    settings: Settings, *, force: bool, level: EnhancementLevel | None
) -> int:
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    engine = create_db_engine(settings.database_url)
    init_db(engine)
    provider = build_provider(settings)
    pipeline = Pipeline(settings, provider, make_session_factory(engine))

    try:
        results = await pipeline.process_directory(force=force, enhancement_level=level)
    finally:
        await provider.aclose()

    if not results:
        print(f"Keine Scans in {settings.input_dir} gefunden.")
        return 0

    for result in results:
        location = result.output_path or "—"
        print(f"  [{result.status:13}] {result.pair_id}  -> {location}")
        if result.error:
            print(f"                  Fehler: {result.error}")

    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    summary = ", ".join(f"{count}x {status}" for status, count in sorted(counts.items()))
    print(f"\n{len(results)} Paar(e) verarbeitet: {summary}")
    return 0


async def _cmd_health(settings: Settings) -> int:
    provider = build_provider(settings)
    try:
        health = await provider.health_check()
    finally:
        await provider.aclose()

    mark = "OK" if health.healthy else "FEHLER"
    print(f"[{mark}] {health.provider}: {health.detail}")
    return 0 if health.healthy else 1


def _cmd_list(settings: Settings) -> int:
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    engine = create_db_engine(settings.database_url)
    init_db(engine)
    with Session(engine) as session:
        records = session.exec(
            select(ScanRecord).order_by(ScanRecord.updated_at.desc())
        ).all()

    if not records:
        print("Noch keine verarbeiteten Scans in der Datenbank.")
        return 0

    for record in records:
        date = record.date_iso or "kein Datum"
        print(
            f"  [{record.status:13}] {record.pair_id}  {date}  "
            f"{record.output_path or '—'}"
        )
    print(f"\n{len(records)} Eintrag/Einträge.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
