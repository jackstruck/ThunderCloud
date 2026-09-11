"""Build, run, inspect and clean execution-owned local maintenance containers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

from maintenance.cli import atomic_json, now
from maintenance.migrations import digest

ROOT = Path(__file__).resolve().parents[1]
LABEL = "thundercloud.execution"


def docker(*args, check=True):
    return subprocess.run(
        ["docker", *args], check=check, capture_output=True, text=True
    )


def inspect(name):
    result = docker("inspect", name, check=False)
    if result.returncode:
        # A daemon failure must not masquerade as a removed resource.
        docker("info", "--format", "{{.ID}}")
        if "no such" in result.stderr.lower():
            return None
        raise RuntimeError("Docker inspection failed; resource state is unknown")
    return json.loads(result.stdout)[0]


def build(directory, base_build=None):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="thundercloud-maintenance-build-"
    ) as temporary:
        staging = Path(temporary)
        files = {}
        selected = [
            ROOT / p for p in ["pyproject.toml", "README.md", "Dockerfile.maintenance"]
        ]
        for folder in ["worker", "maintenance", "scripts", "tests", "migrations", "ui"]:
            selected.extend(
                p
                for p in (ROOT / folder).rglob("*")
                if p.is_file()
                and p.suffix
                in {".py", ".sql", ".sh", ".js", ".css", ".html", ".json", ".yaml"}
                and "__pycache__" not in p.parts
            )
        for source in sorted(selected):
            if source.is_symlink():
                raise RuntimeError("Build inputs cannot be symlinks")
            relative = str(source.relative_to(ROOT))
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            files[relative] = {
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "mode": oct(target.stat().st_mode & 0o777),
            }
        fingerprint = {"source_checksum": digest(files), "files": files}
        atomic_json(staging / "build-manifest.json", fingerprint)
        atomic_json(directory / "build-manifest.json", fingerprint)
        recipe = staging / "Dockerfile.maintenance"
        base_image = None
        if base_build is not None:
            original = json.loads((base_build / "build-manifest.json").read_text())
            receipt = json.loads((base_build / "build-receipt.json").read_text())
            if digest(original["files"]) != original["source_checksum"]:
                raise RuntimeError("Base build manifest changed")
            for dependency in ["Dockerfile.maintenance"]:
                if files[dependency] != original["files"][dependency]:
                    raise RuntimeError(
                        "Dependencies changed; a full maintenance build is required"
                    )
            base_image = receipt["image_id"]
            actual = json.loads(docker("image", "inspect", base_image).stdout)[0]
            if (
                actual["Config"]["Labels"].get("thundercloud.source")
                != original["source_checksum"]
            ):
                raise RuntimeError("Base image does not match its source receipt")
            if files["pyproject.toml"] != original["files"]["pyproject.toml"]:
                previous_project = json.loads(
                    docker(
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--cpus",
                        "0.25",
                        "--label",
                        "thundercloud.purpose=dependency-inspection",
                        "--label",
                        LABEL + "=" + directory.name,
                        "--entrypoint",
                        "python",
                        base_image,
                        "-c",
                        "import json,tomllib; from pathlib import Path; print(json.dumps(tomllib.loads(Path('/app/pyproject.toml').read_text())))",
                    ).stdout
                )
                current_project = tomllib.loads(
                    (staging / "pyproject.toml").read_text()
                )
                # Entry point retirement does not change installed dependencies.
                previous_project["project"].pop("scripts", None)
                current_project["project"].pop("scripts", None)
                if previous_project != current_project:
                    raise RuntimeError(
                        "Dependencies or packaging configuration changed; full build required"
                    )
            base_tag = (
                "thundercloud-maintenance:base-"
                + base_image.removeprefix("sha256:")[:16]
            )
            docker("tag", base_image, base_tag)
            recipe = directory / "Dockerfile.refresh"
            recipe.write_text(
                f'FROM {base_tag}\nWORKDIR /app\nRUN rm -rf /app\nCOPY . /app\nRUN python -m pip install --no-deps . && chmod -R a+rX /app\nENTRYPOINT ["python", "-m", "maintenance.cli"]\n'
            )
            atomic_json(
                directory / "base-image.json",
                {
                    "image_id": base_image,
                    "tag": base_tag,
                    "source_checksum": original["source_checksum"],
                    "purpose": "reuse verified dependencies",
                },
            )
        with (directory / "build.log").open("w") as log:
            subprocess.run(
                [
                    "docker",
                    "build",
                    "--label",
                    "thundercloud.purpose=maintenance",
                    "--label",
                    LABEL + "=" + directory.name,
                    "--label",
                    "thundercloud.source=" + fingerprint["source_checksum"],
                    "--iidfile",
                    str(directory / "image-id"),
                    "-f",
                    str(recipe),
                    str(staging),
                ],
                check=True,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        image = (directory / "image-id").read_text().strip()
        atomic_json(
            directory / "build-receipt.json",
            {
                "image_id": image,
                "base_image_id": base_image,
                "source_checksum": fingerprint["source_checksum"],
                "built_at": now(),
                "execution_id": directory.name,
                "purpose": "maintenance",
                "scope": "local immutable image ID; registry digest recorded when pushed",
            },
        )
        print(image)


def checked_resource(resource, execution):
    actual = inspect(resource.get("id") or resource["name"])
    if actual is None:
        return None
    if (
        actual["Name"].lstrip("/") != resource["name"]
        or actual["Config"]["Labels"].get(LABEL) != execution
    ):
        raise RuntimeError("Resource ownership does not match the execution manifest")
    return actual


def status(directory):
    resources = json.loads((directory / "resources.json").read_text())
    runner = checked_resource(resources["runner"], resources["execution_id"])
    report_path = directory / "report.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else None
    outcome = "interrupted/incomplete"
    if runner and runner["State"]["Running"]:
        outcome = "running"
    elif runner and runner["State"]["Status"] == "exited":
        code = runner["State"]["ExitCode"]
        if (
            code == 0
            and report
            and report["status"] == "succeeded"
            and report["execution_id"] == resources["execution_id"]
        ):
            outcome = "succeeded"
        elif code != 0:
            outcome = "failed"
    elif runner is None and (directory / "cleanup.json").exists():
        # Recover a cleanup interrupted after container removal from the state
        # and checksums exported before removal. Missing exports remain incomplete.
        receipt = json.loads((directory / "cleanup.json").read_text())
        expected = receipt.get("artifacts", {})
        state_path = directory / "container-state.json"
        valid = all(
            path.is_file()
            and hashlib.sha256(path.read_bytes()).hexdigest() == expected.get(path.name)
            for path in [state_path, report_path]
        )
        if valid:
            exported = json.loads(state_path.read_text())
            if exported["Status"] == "exited" and not exported["Running"]:
                if exported["ExitCode"] != 0:
                    outcome = "failed"
                elif (
                    report
                    and report["status"] == "succeeded"
                    and report["execution_id"] == resources["execution_id"]
                ):
                    outcome = "succeeded"
    result = {
        "execution_id": resources["execution_id"],
        "status": outcome,
        "report": str(report_path) if report else None,
        "container_exit_code": runner["State"]["ExitCode"]
        if runner and not runner["State"]["Running"]
        else None,
    }
    return result


def cleanup(directory):
    path = directory / "resources.json"
    resources = json.loads(path.read_text())
    outcome = status(directory)
    if outcome["status"] == "running":
        raise RuntimeError("Execution is still running; cleanup cannot interrupt it")
    receipt = {"execution_status": outcome["status"], "artifacts": {}, "removed": []}
    # Preserve terminal container state and diagnostic logs before removing it.
    runner = checked_resource(resources["runner"], resources["execution_id"])
    if runner:
        result = docker("logs", runner["Id"])
        (directory / "container.log").write_text(result.stdout + result.stderr)
        atomic_json(directory / "container-state.json", runner["State"])
    for file in sorted(directory.rglob("*")):
        if file.is_file() and file.name not in {"resources.json", "cleanup.json"}:
            receipt["artifacts"][str(file.relative_to(directory))] = hashlib.sha256(
                file.read_bytes()
            ).hexdigest()
    if not receipt["artifacts"]:
        raise RuntimeError("No diagnostic artifacts were exported")
    atomic_json(directory / "cleanup.json", {**receipt, "status": "incomplete"})
    for kind in ["runner", "database"]:
        actual = checked_resource(resources[kind], resources["execution_id"])
        if actual:
            docker("rm", "--force", "--volumes", actual["Id"])
            if inspect(actual["Id"]) is not None:
                raise RuntimeError("Container removal not confirmed")
        receipt["removed"].append(resources[kind])
    receipt.update(
        status="complete",
        completed_at=now(),
        retained=[
            {
                "resource": "local image and protected artifacts",
                "owner": "ThunderCloud implementation",
                "reason": "validation and reusable build evidence",
                "deadline": resources["retention_deadline"],
            }
        ],
    )
    atomic_json(directory / "cleanup.json", receipt)
    resources["cleanup"] = receipt
    atomic_json(path, resources)
    return receipt


def start(
    directory,
    execution,
    image,
    postgres_image,
    deadline,
    procedure="validate",
    backup=None,
    backup_receipt=None,
    source_bucket=None,
):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", execution):
        raise ValueError("Invalid execution identifier")
    if procedure == "rehearse-migration":
        from maintenance.rehearsal import verify_backup

        if backup is None or backup_receipt is None:
            raise ValueError("Rehearsal requires a protected backup and receipt")
        backup, backup_receipt = (
            backup.resolve(strict=True),
            backup_receipt.resolve(strict=True),
        )
        verify_backup(backup, json.loads(backup_receipt.read_text()))
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    image_id = docker("image", "inspect", image, "--format", "{{.Id}}").stdout.strip()
    database_id = docker(
        "image", "inspect", postgres_image, "--format", "{{.Id}}"
    ).stdout.strip()
    resources = {
        "execution_id": execution,
        "image_id": image_id,
        "retention_deadline": deadline,
        "database": {
            "name": execution + "-postgres",
            "image": database_id,
            "cpus": "0.75",
        },
        "runner": {
            "name": execution + "-" + procedure,
            "image": image_id,
            "cpus": "0.25",
        },
        "procedure": procedure,
    }
    path = directory / "resources.json"
    atomic_json(path, resources)
    db = docker(
        "create",
        "--name",
        resources["database"]["name"],
        "--cpus",
        resources["database"]["cpus"],
        "--label",
        LABEL + "=" + execution,
        "--label",
        "thundercloud.purpose=" + procedure,
        "--network",
        "none",
        "--tmpfs",
        "/var/lib/postgresql/data",
        "-e",
        "POSTGRES_HOST_AUTH_METHOD=trust",
        database_id,
    ).stdout.strip()
    resources["database"]["id"] = db
    atomic_json(path, resources)
    docker("start", db)
    for _ in range(30):
        if (
            docker("exec", db, "pg_isready", "-U", "postgres", check=False).returncode
            == 0
        ):
            break
        # docker exec bounds each readiness observation; waits never imply readiness.
        import time

        time.sleep(1)
    else:
        raise RuntimeError(
            "Disposable PostgreSQL did not become ready; inspect and clean this manifest"
        )
    mounts, arguments = [], []
    if procedure == "rehearse-migration":
        mounts = [
            "--mount",
            f"type=bind,source={backup},target=/inputs/backup.dump,readonly",
            "--mount",
            f"type=bind,source={backup_receipt},target=/inputs/backup-receipt.json,readonly",
        ]
        arguments = [
            "--backup",
            "/inputs/backup.dump",
            "--backup-receipt",
            "/inputs/backup-receipt.json",
        ]
        resources["inputs"] = {
            "backup": str(backup),
            "receipt": str(backup_receipt),
            "cleanup": "retain protected inputs",
            "source_bucket": source_bucket,
        }
        if source_bucket is not None:
            arguments.extend(["--source-bucket", source_bucket])
        atomic_json(path, resources)
    run = docker(
        "create",
        "--name",
        resources["runner"]["name"],
        "--cpus",
        resources["runner"]["cpus"],
        "--label",
        LABEL + "=" + execution,
        "--label",
        "thundercloud.purpose=" + procedure,
        "--network",
        "container:" + db,
        "--shm-size",
        "256m",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--mount",
        f"type=bind,source={directory},target=/artifacts",
        *mounts,
        image_id,
        procedure,
        "--execution-id",
        execution,
        "--artifacts",
        "/artifacts",
        "--image-digest",
        image_id,
        *arguments,
    ).stdout.strip()
    resources["runner"]["id"] = run
    atomic_json(path, resources)
    docker("start", run)
    return status(directory)


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ["build", "status", "cleanup", "start", "run"]:
        p = sub.add_parser(command)
        p.add_argument("--artifacts", type=Path, required=True)
        if command == "build":
            p.add_argument("--base-build", type=Path)
        if command in {"start", "run"}:
            p.add_argument(
                "--procedure",
                choices=["validate", "rehearse-migration"],
                default="validate",
            )
            p.add_argument("--backup", type=Path)
            p.add_argument("--backup-receipt", type=Path)
            p.add_argument("--source-bucket")
            p.add_argument("--execution-id", required=True)
            p.add_argument("--image", required=True)
            p.add_argument("--postgres-image", default="pgvector/pgvector:pg17")
            p.add_argument("--retention-deadline", required=True)
    args = parser.parse_args(argv)
    directory = args.artifacts.resolve()
    if args.command == "build":
        build(directory, args.base_build)
    elif args.command in {"start", "run"}:
        print(
            json.dumps(
                start(
                    directory,
                    args.execution_id,
                    args.image,
                    args.postgres_image,
                    args.retention_deadline,
                    args.procedure,
                    args.backup,
                    args.backup_receipt,
                    args.source_bucket,
                )
            )
        )
        if args.command == "run":
            resources = json.loads((directory / "resources.json").read_text())
            docker("wait", resources["runner"]["id"])
            outcome = status(directory)
            cleanup(directory)
            print(json.dumps(outcome))
            if outcome["status"] != "succeeded":
                raise SystemExit(1)
    else:
        print(
            json.dumps(
                {"status": status, "cleanup": cleanup}[args.command](directory),
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
