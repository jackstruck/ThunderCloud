from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

from .config import Settings
from .probe_service import ProbeRun, ProbeService


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat().replace("+00:00", "Z")
    raise TypeError(f"cannot serialize {type(value).__name__}")


def write_review(run: ProbeRun, destination: Path | None) -> tuple[dict, Path]:
    if destination is None:
        review_dir = Path(tempfile.mkdtemp(prefix="face-probe-"))
        os.chmod(review_dir, 0o700)
    else:
        review_dir = destination.absolute()
        review_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    for crop in run.crops:
        target = review_dir / crop.relative_path
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(crop.content)
    result = dict(run.result)
    result["review_directory"] = str(review_dir)
    payload = (
        json.dumps(result, default=_json_default, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    result_path = review_dir / "result.json"
    descriptor = os.open(result_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(payload)
    return result, result_path


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="face-probe")
    commands = root.add_subparsers(dest="command", required=True)
    submit = commands.add_parser("submit")
    source = submit.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path)
    source.add_argument("--crop-dir", type=Path)
    submit.add_argument("--top-k", type=int, default=10)
    submit.add_argument("--review-output-dir", type=Path)
    return root


def main(argv=None) -> None:
    args = parser().parse_args(argv)
    service = None
    try:
        service = ProbeService(Settings.from_env())
        run = (
            service.submit_crop_directory(args.crop_dir, args.top_k)
            if args.crop_dir is not None
            else service.submit_local(args.file, args.top_k)
        )
        result, _ = write_review(run, args.review_output_dir)
        print(
            json.dumps(
                result, default=_json_default, sort_keys=True, separators=(",", ":")
            )
        )
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - sanitized top-level command boundary
        print(
            json.dumps(
                {"status": "failed", "error_code": type(exc).__name__.upper()[:64]},
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    finally:
        if service is not None:
            service.close()


if __name__ == "__main__":
    main()
