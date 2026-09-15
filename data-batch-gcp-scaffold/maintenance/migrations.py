"""One ordered, checksum-checked migration process for setup and upgrades."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

LOCK = (1413696594, 1296648018)
LEDGER = "platform_schema_migration"


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str


def ordered_migrations(root: Path) -> list[Migration]:
    paths = [
        root / "scripts/db_schema.sql",
        *sorted((root / "migrations").glob("*.sql")),
    ]
    result = []
    for version, path in enumerate(paths):
        if version and not re.match(rf"{version:03d}_[a-z0-9_]+\.sql$", path.name):
            raise MigrationError("Migration files must have contiguous unique versions")
        raw = path.read_bytes()
        result.append(
            Migration(version, path.name, raw.decode(), hashlib.sha256(raw).hexdigest())
        )
    return result


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def manifest(migrations):
    return [
        {"version": m.version, "name": m.name, "checksum": m.checksum}
        for m in migrations
    ]


# Read logical definitions rather than OIDs, physical layout, or table contents.
# Extension-owned definitions are identified separately by extension/version.
# Exclude only our ledger and its dependents; include unexpected application objects.
RELATIONS = """
 SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relname <> 'platform_schema_migration'
 AND c.relkind IN ('r','p','v','m','S','f')
 AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass
                 AND d.objid=c.oid AND d.deptype='e')
"""
CATALOG = {
    "extensions": "SELECT extname,extversion FROM pg_extension ORDER BY 1",
    "relations": f"""SELECT c.relname,c.relkind,c.relpersistence,c.relrowsecurity,
        c.relforcerowsecurity,c.reloptions,pg_get_partkeydef(c.oid)
        FROM pg_class c WHERE c.oid IN ({RELATIONS}) ORDER BY 1""",
    "columns": f"""SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),
        a.attnotnull,a.attidentity,a.attgenerated,pg_get_expr(d.adbin,d.adrelid),
        co.collname FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        LEFT JOIN pg_collation co ON co.oid=a.attcollation
        WHERE c.oid IN ({RELATIONS}) AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,2""",
    "constraints": f"""SELECT c.relname,p.conname,pg_get_constraintdef(p.oid),p.convalidated
        FROM pg_constraint p JOIN pg_class c ON c.oid=p.conrelid
        WHERE c.oid IN ({RELATIONS}) ORDER BY 1,2""",
    "indexes": f"""SELECT c.relname,i.relname,pg_get_indexdef(x.indexrelid),
        x.indisvalid,x.indisready FROM pg_index x JOIN pg_class c ON c.oid=x.indrelid
        JOIN pg_class i ON i.oid=x.indexrelid WHERE c.oid IN ({RELATIONS}) ORDER BY 1,2""",
    "triggers": f"""SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid),t.tgenabled
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        WHERE c.oid IN ({RELATIONS}) AND NOT t.tgisinternal ORDER BY 1,2""",
    "functions": """SELECT p.proname,pg_get_function_identity_arguments(p.oid),
        pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prokind IN ('f','p') AND NOT EXISTS
        (SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass
         AND d.objid=p.oid AND d.deptype='e') ORDER BY 1,2""",
    "views": f"""SELECT c.relname,pg_get_viewdef(c.oid) FROM pg_class c
        WHERE c.oid IN ({RELATIONS}) AND c.relkind IN ('v','m') ORDER BY 1""",
    "policies": """SELECT tablename,policyname,permissive,roles,cmd,qual,with_check
        FROM pg_policies WHERE schemaname='public' ORDER BY 1,2""",
    "enums": """SELECT t.typname,e.enumlabel,e.enumsortorder::text FROM pg_type t
        JOIN pg_namespace n ON n.oid=t.typnamespace JOIN pg_enum e ON e.enumtypid=t.oid
        WHERE n.nspname='public' ORDER BY 1,e.enumsortorder""",
    "sequences": """SELECT sequencename,data_type,start_value::text,min_value::text,
        max_value::text,increment_by::text,cycle,cache_size::text FROM pg_sequences
        WHERE schemaname='public' ORDER BY 1""",
}


def schema_fingerprint(cursor):
    # Explicit path also makes pg_get_* output deterministic between callers.
    cursor.execute("SET LOCAL search_path TO public, pg_catalog")
    result = {}
    for name, query in CATALOG.items():
        cursor.execute(query)
        result[name] = cursor.fetchall()
    return digest(result)


def reference_check(reference, migrations, through):
    if reference is None or reference.get("format") != 1:
        raise MigrationError(
            "A verified schema reference is required to adopt an existing schema"
        )
    expected = manifest(migrations[: through + 1])
    if reference.get("migrations", [])[: through + 1] != expected:
        raise MigrationError("Schema reference migration checksums do not match")
    schemas = reference.get("schemas", {})
    for version in range(through + 1):
        if not re.fullmatch(r"[0-9a-f]{64}", schemas.get(str(version), "")):
            raise MigrationError("Schema reference is missing a version fingerprint")


def build_reference(connection, migrations):
    """Only on an empty disposable DB; run the same implementation as upgrades."""
    cursor = connection.cursor()
    try:
        cursor.execute(RELATIONS)
        if cursor.fetchone():
            raise MigrationError("Schema reference requires an empty database")
        cursor.execute("SELECT to_regclass('public.platform_schema_migration')")
        if cursor.fetchone()[0]:
            raise MigrationError("Schema reference requires an empty database")
    finally:
        connection.rollback()
        cursor.close()
    schemas = {}
    apply_migrations(
        connection,
        migrations,
        checkpoint=lambda m, fp: schemas.update({str(m.version): fp}),
    )
    return {"format": 1, "migrations": manifest(migrations), "schemas": schemas}


def apply_migrations(
    connection,
    migrations,
    *,
    reference=None,
    adopt_through=None,
    checkpoint=None,
    source_bucket=None,
):
    """Lock the session; atomically commit each SQL step and its checksum ledger.

    A caller owns a fresh connection. On failure, committed steps remain resumable;
    an uncommitted step is rolled back. Never automatically reverse earlier steps.
    """
    if not migrations or [m.version for m in migrations] != list(
        range(len(migrations))
    ):
        raise MigrationError("Migration manifest is not an ordered prefix")
    cursor = connection.cursor()
    locked = False
    try:
        if source_bucket is not None:
            cursor.execute(
                "SELECT set_config('thundercloud.source_bucket',%s,false)",
                (source_bucket,),
            )
        cursor.execute("SELECT pg_try_advisory_lock(%s,%s)", LOCK)
        locked = cursor.fetchone()[0]
        if not locked:
            raise MigrationError("Another migration execution holds the lock")
        cursor.execute("SELECT to_regclass('public.platform_schema_migration')")
        tracked = cursor.fetchone()[0] is not None
        if not tracked:
            cursor.execute(RELATIONS)
            existing = cursor.fetchone() is not None
            if existing:
                if type(adopt_through) is not int or not 0 <= adopt_through < len(
                    migrations
                ):
                    raise MigrationError(
                        "Existing schema requires an explicit adoption version"
                    )
                reference_check(reference, migrations, adopt_through)
                if (
                    schema_fingerprint(cursor)
                    != reference["schemas"][str(adopt_through)]
                ):
                    raise MigrationError(
                        "Installed schema differs from the verified reference; no versions recorded"
                    )
            elif adopt_through is not None:
                raise MigrationError("Cannot adopt migrations on an empty database")
            cursor.execute("""CREATE TABLE public.platform_schema_migration (
                version integer PRIMARY KEY CHECK(version>=0),
                name text NOT NULL, checksum char(64) NOT NULL,
                schema_fingerprint char(64) NOT NULL,
                adopted boolean NOT NULL DEFAULT false,
                applied_at timestamptz NOT NULL DEFAULT now(),
                applied_by text NOT NULL DEFAULT current_user
            )""")
            cursor.execute("REVOKE ALL ON public.platform_schema_migration FROM PUBLIC")
            if existing:
                for m in migrations[: adopt_through + 1]:
                    cursor.execute(
                        """INSERT INTO public.platform_schema_migration
                        (version,name,checksum,schema_fingerprint,adopted) VALUES (%s,%s,%s,%s,true)""",
                        (
                            m.version,
                            m.name,
                            m.checksum,
                            reference["schemas"][str(m.version)],
                        ),
                    )
            connection.commit()
        cursor.execute(
            "SELECT version,name,checksum,schema_fingerprint FROM public.platform_schema_migration ORDER BY version"
        )
        rows = cursor.fetchall()
        if len(rows) > len(migrations):
            raise MigrationError("Database has migrations newer than this manifest")
        for index, row in enumerate(rows):
            m = migrations[index]
            if list(row[:3]) != [m.version, m.name, m.checksum]:
                raise MigrationError("Applied migration history or checksum changed")
        if rows and schema_fingerprint(cursor) != rows[-1][3]:
            raise MigrationError(
                "Installed schema changed after its last committed migration"
            )
        connection.commit()
        for m in migrations[len(rows) :]:
            cursor.execute(m.sql)
            fp = schema_fingerprint(cursor)
            if reference is not None:
                reference_check(reference, migrations, m.version)
                if fp != reference["schemas"][str(m.version)]:
                    raise MigrationError(
                        "Migration result differs from the verified schema reference"
                    )
            cursor.execute(
                """INSERT INTO public.platform_schema_migration
                (version,name,checksum,schema_fingerprint) VALUES (%s,%s,%s,%s)""",
                (m.version, m.name, m.checksum, fp),
            )
            connection.commit()
            if checkpoint:
                checkpoint(m, fp)
        return {
            "version": migrations[-1].version,
            "manifest_checksum": digest(manifest(migrations)),
            "schema_fingerprint": schema_fingerprint(cursor),
        }
    except BaseException:
        connection.rollback()
        raise
    finally:
        # PostgreSQL releases the session lock on disconnect as well.
        connection.rollback()
        if locked:
            cursor.execute("SELECT pg_advisory_unlock(%s,%s)", LOCK)
            connection.commit()
        cursor.close()
