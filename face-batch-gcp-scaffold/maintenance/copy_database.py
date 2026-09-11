"""Copy a consistent read-only database snapshot into an isolated local target."""

from __future__ import annotations

import hashlib
import tempfile

from maintenance.migrations import RELATIONS, MigrationError, schema_fingerprint


def quote(value):
    return '"' + value.replace('"', '""') + '"'


class DigestSink:
    def __init__(self):
        self.hash = hashlib.sha256()
        self.bytes = 0

    def write(self, data):
        self.hash.update(data)
        self.bytes += len(data)


def copy_database(source, target, migrations, scratch, progress=None):
    """No source writes; schema, copy data and constraints commit together locally.

    The caller owns an empty, disposable target and its resource manifest. Every
    public application table and all columns are checked, not just feature fixtures.
    """
    src, dst = source.cursor(), target.cursor()
    try:
        src.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for cursor in [src, dst]:
            cursor.execute("SET LOCAL search_path TO public")
            cursor.execute("SET LOCAL timezone TO 'UTC'")
            cursor.execute("SET LOCAL datestyle TO 'ISO, YMD'")
            cursor.execute("SET LOCAL extra_float_digits TO 3")
        dst.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
        if dst.fetchone()[0]:
            raise MigrationError("Copy target must be empty")
        for migration in migrations:
            dst.execute(migration.sql)
        original_schema = schema_fingerprint(src)
        if original_schema != schema_fingerprint(dst):
            raise MigrationError(
                "Copy target does not match source schema and extension versions"
            )
        src.execute(f"""SELECT c.relname,array_agg(a.attname ORDER BY a.attname)
            FROM pg_class c JOIN pg_attribute a ON a.attrelid=c.oid
            WHERE c.oid IN ({RELATIONS}) AND c.relkind='r' AND a.attnum>0
            AND NOT a.attisdropped GROUP BY c.relname ORDER BY c.relname""")
        tables = dict(src.fetchall())
        src.execute("""SELECT child.relname,parent.relname FROM pg_constraint con
            JOIN pg_class child ON child.oid=con.conrelid
            JOIN pg_class parent ON parent.oid=con.confrelid
            WHERE con.contype='f' AND con.connamespace='public'::regnamespace""")
        parents = {table: set() for table in tables}
        for child, parent in src.fetchall():
            if child in parents and child != parent:
                parents[child].add(parent)
        order = []
        while len(order) < len(tables):
            ready = sorted(
                table
                for table in tables
                if table not in order and parents[table] <= set(order)
            )
            if not ready:
                raise MigrationError(
                    "Copy requires a reviewed procedure for cyclic foreign keys"
                )
            order.extend(ready)
        queries, receipts = {}, {}
        dst.execute("SET CONSTRAINTS ALL DEFERRED")
        for table in order:
            src.execute(
                """SELECT a.attname FROM pg_index i
                JOIN unnest(i.indkey) WITH ORDINALITY k(attnum,position) ON true
                JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum
                WHERE i.indrelid=%s::regclass AND i.indisprimary ORDER BY k.position""",
                (table,),
            )
            keys = [row[0] for row in src.fetchall()]
            if not keys:
                raise MigrationError(
                    "Every copied application table must have a stable primary key"
                )
            columns = ",".join(quote(c) for c in tables[table])
            queries[table] = (
                f"COPY (SELECT {columns} FROM {quote(table)} ORDER BY {','.join(quote(k) for k in keys)}) TO STDOUT"
            )
            with tempfile.TemporaryFile(dir=scratch) as stream:
                src.execute(queries[table], stream=stream)
                stream.seek(0)
                copied = DigestSink()
                while data := stream.read(1024 * 1024):
                    copied.write(data)
                stream.seek(0)
                dst.execute(
                    f"COPY {quote(table)} ({columns}) FROM STDIN", stream=stream
                )
            receipts[table] = {"bytes": copied.bytes, "sha256": copied.hash.hexdigest()}
            if progress:
                progress(table, len(receipts), len(tables))
        for table in order:
            actual = DigestSink()
            dst.execute(queries[table], stream=actual)
            if receipts[table] != {
                "bytes": actual.bytes,
                "sha256": actual.hash.hexdigest(),
            }:
                raise MigrationError(
                    "Copied table differs from the source snapshot: " + table
                )
        target.commit()  # all remaining deferred constraints are checked here
        return {
            "schema_fingerprint": original_schema,
            "tables": receipts,
            "source_mode": "repeatable-read/read-only",
            "constraints": "enforced",
        }
    except BaseException:
        target.rollback()
        raise
    finally:
        source.rollback()
        src.close()
        dst.close()
