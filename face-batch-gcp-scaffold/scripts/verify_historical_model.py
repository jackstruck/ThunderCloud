#!/usr/bin/env python3
"""Record model equivalence only when every job track has an exact verified counterpart."""

from __future__ import annotations

import argparse
import json
import uuid

from google.cloud import secretmanager
from google.cloud.sql.connector import Connector, IPTypes


def verify_job(cursor, job_id, verified_version, actor):
    job_id = str(uuid.UUID(job_id))
    cursor.execute(
        "SELECT embedding_model_version,status FROM processing_job WHERE job_id=%s FOR UPDATE",
        (job_id,),
    )
    job = cursor.fetchone()
    if not job or job[1] != "succeeded" or job[0] == verified_version:
        raise ValueError(
            "Choose a successful historical job with a different reported model label."
        )
    reported = job[0]
    cursor.execute(
        """SELECT old.track_id,old.model_version,
             (SELECT newer.track_id FROM face_track newer JOIN processing_job pj ON pj.job_id=newer.processing_job_id
              WHERE newer.source_id=old.source_id AND newer.local_track_id=old.local_track_id
                AND newer.start_ms=old.start_ms AND newer.end_ms=old.end_ms
                AND newer.aggregate_embedding=old.aggregate_embedding
                AND newer.model_version=%s AND pj.embedding_model_version=%s AND pj.status='succeeded'
              ORDER BY newer.track_id LIMIT 1)
           FROM face_track old WHERE old.processing_job_id=%s ORDER BY old.track_id""",
        (verified_version, verified_version, job_id),
    )
    rows = cursor.fetchall()
    if not rows or any(row[1] != reported or row[2] is None for row in rows):
        raise ValueError(
            "Every historical track must have a bit-identical embedding for the same source and track interval under the verified version."
        )
    evidence = {
        "method": "bit-identical embedding, source, local track ID, start and end time",
        "matched_tracks": [
            {"historical_track_id": str(r[0]), "verified_track_id": str(r[2])}
            for r in rows
        ],
    }
    cursor.execute(
        "SELECT reported_model_version,verified_model_version FROM verified_embedding_model WHERE processing_job_id=%s",
        (job_id,),
    )
    existing = cursor.fetchone()
    if existing and list(existing) != [reported, verified_version]:
        raise ValueError("An incompatible verification already exists for this job.")
    cursor.execute(
        """INSERT INTO verified_embedding_model(processing_job_id,reported_model_version,verified_model_version,evidence,verified_by)
           VALUES (%s,%s,%s,%s::jsonb,%s) ON CONFLICT (processing_job_id) DO NOTHING""",
        (job_id, reported, verified_version, json.dumps(evidence), actor),
    )
    return {
        "processing_job_id": job_id,
        "reported_model_version": reported,
        "verified_model_version": verified_version,
        "verified_tracks": len(rows),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--job-id", required=True)
    p.add_argument("--verified-model-version", required=True)
    p.add_argument("--verified-by", required=True)
    p.add_argument("--project", default="teak-banner-dome")
    p.add_argument("--instance", default="teak-banner-dome:us-central1:face-batch-pg")
    p.add_argument("--database", default="face_index")
    p.add_argument("--password-secret", default="face-batch-postgres-admin-password")
    a = p.parse_args()
    secret = (
        secretmanager.SecretManagerServiceClient()
        .access_secret_version(
            request={
                "name": f"projects/{a.project}/secrets/{a.password_secret}/versions/latest"
            }
        )
        .payload.data.decode()
        .strip()
    )
    with Connector() as connector:
        connection = connector.connect(
            a.instance,
            "pg8000",
            user="postgres",
            password=secret,
            db=a.database,
            ip_type=IPTypes.PUBLIC,
        )
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT pg_advisory_xact_lock(8675309)")
            result = verify_job(
                cursor, a.job_id, a.verified_model_version, a.verified_by
            )
            connection.commit()
            print(json.dumps(result), flush=True)
        except Exception:
            connection.rollback()
            raise
        finally:
            secret = ""
            connection.close()


if __name__ == "__main__":
    main()
