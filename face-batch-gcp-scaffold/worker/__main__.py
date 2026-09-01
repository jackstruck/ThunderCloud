from __future__ import annotations

import argparse
import json

from .process import run
from .telemetry import configure_logging


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Process one staged historical face video"
    )
    parser.add_argument("--gcs-uri", required=True)
    parser.add_argument("--external-source-ref", required=True)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--staging-generation", type=int)
    parser.add_argument("--source-metadata-json", default="{}")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    metadata = json.loads(args.source_metadata_json)
    if not isinstance(metadata, dict):
        raise TypeError("source metadata must be a JSON object")
    configure_logging()
    run(
        gcs_uri=args.gcs_uri,
        external_source_ref=args.external_source_ref,
        expected_sha256=args.expected_sha256,
        job_id=args.job_id,
        staging_generation=args.staging_generation,
        source_metadata=metadata,
    )


if __name__ == "__main__":
    main()
