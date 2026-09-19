"""Register processed media independently of whether subjects were detected."""
from __future__ import annotations

import uuid


def register_run_source(cursor, run_id):
    # Match enrollment's lock order: run row, then library content lock.
    cursor.execute('SELECT run_id FROM media_run WHERE run_id=%s FOR UPDATE', (run_id,))
    if not cursor.fetchone():
        return None
    cursor.execute('SELECT pg_advisory_xact_lock(8675309)')
    cursor.execute('''SELECT source_sha256,source_page_url,content_type,retained_source_id,
                      handling_policy,archive_bucket,archive_object_name,archive_object_generation,
                      created_at FROM media_run WHERE run_id=%s''', (run_id,))
    digest, page, content_type, retained, policy, bucket, name, generation, created = cursor.fetchone()
    if not digest:
        return None
    source_id = retained
    if not source_id:
        cursor.execute('''SELECT source_id FROM source_asset
                          WHERE source_sha256=%s AND deleted_at IS NULL
                          ORDER BY (storage_kind='managed') DESC,created_at,source_id LIMIT 1''', (digest,))
        existing = cursor.fetchone()
        source_id = existing[0] if existing else None
    if not source_id:
        source_id = str(uuid.uuid4())
        archive = policy == 'retain_and_enroll' and bucket and name and generation
        cursor.execute('''INSERT INTO source_asset
            (source_id,external_source_ref,source_sha256,source_page_url,content_type,
             created_at,storage_kind,object_bucket,object_name,object_generation)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (source_id, f'submission:{run_id}', digest, page, content_type, created,
             'archive' if archive else 'none', bucket if archive else None,
             name if archive else None, generation if archive else None))
    if page:
        cursor.execute('INSERT INTO source_url(source_sha256,page_url) VALUES (%s,%s) ON CONFLICT DO NOTHING', (digest,page))
    cursor.execute('UPDATE media_run SET retained_source_id=%s WHERE run_id=%s', (source_id,run_id))
    return str(source_id)


def backfill(database):
    """Commit each run separately so ongoing enrollment can continue."""
    connection = database.connect()
    try:
        cursor = connection.cursor()
        cursor.execute("""SELECT run_id FROM media_run WHERE state='succeeded'
                          AND retained_source_id IS NULL AND source_sha256 IS NOT NULL
                          ORDER BY created_at,run_id""")
        ids = [str(row[0]) for row in cursor.fetchall()]
        connection.commit()
        for run_id in ids:
            register_run_source(cursor, run_id)
            connection.commit()
        return len(ids)
    finally:
        connection.close()
