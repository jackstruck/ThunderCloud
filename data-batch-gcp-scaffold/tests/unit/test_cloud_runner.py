import hashlib
from unittest.mock import Mock

import pytest

from maintenance.cloud_runner import INPUTS, download, run


def reference(uri, data, generation=7):
    return {
        "uri": uri,
        "generation": generation,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def client_for(data):
    client = Mock()

    def transfer(stream, **kwargs):
        assert kwargs["if_generation_match"] == 7
        stream.write(data)

    client.bucket.return_value.blob.return_value.download_to_file.side_effect = transfer
    return client


def request(data):
    return {
        "format": 1,
        "execution_id": "cloud-fixture",
        "image_digest": "sha256:" + "a" * 64,
        "output_prefix": "gs://private/executions/cloud-fixture",
        "plan_checksum": "b" * 64,
        "inputs": {
            name: reference("gs://private/inputs/" + name, data) for name in INPUTS
        },
    }


def test_corrupt_download_never_invokes_migration(tmp_path):
    invoke = Mock()
    with pytest.raises(ValueError, match="checksum and size"):
        run(
            request(b"expected"), tmp_path, client=client_for(b"corrupt"), invoke=invoke
        )
    invoke.assert_not_called()
    assert not list(tmp_path.rglob("*.partial"))
    assert not (tmp_path / "inputs" / "plan").exists()


def test_bundle_and_resume_use_exact_inputs(tmp_path):
    payload = b"fixture"
    bundle = request(payload)
    bundle["resume_progress"] = reference(
        bundle["output_prefix"] + "/progress.json", payload
    )
    invoke = Mock()
    client = client_for(payload)
    run(bundle, tmp_path, client=client, invoke=invoke)
    args = invoke.call_args.args[0]
    assert args[0] == "apply-migration" and "--resume" in args
    assert invoke.call_args.kwargs["artifact_store"].generations["progress.json"] == 7
    for name in INPUTS:
        assert (tmp_path / "inputs" / name).read_bytes() == payload
    assert client.bucket.return_value.blob.call_count == len(INPUTS) + 1
    assert all(
        call.kwargs["generation"] == 7
        for call in client.bucket.return_value.blob.call_args_list
    )


def test_download_publishes_only_verified_private_file(tmp_path):
    payload = b"protected fixture"
    path = tmp_path / "inputs" / "backup"
    download(client_for(payload), reference("gs://private/backup", payload), path)
    assert path.read_bytes() == payload
    assert path.stat().st_mode & 0o777 == 0o600


def test_cloud_entry_point_propagates_migration_exit_code(tmp_path, monkeypatch):
    import maintenance.cloud_runner as runner

    def fetched(_client, _reference, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")

    monkeypatch.setattr(runner, "download", fetched)
    monkeypatch.setattr(runner, "run", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr("google.cloud.storage.Client", Mock())
    assert (
        runner.main(
            [
                "--request-uri",
                "gs://private/request",
                "--request-generation",
                "7",
                "--request-sha256",
                "a" * 64,
                "--request-bytes",
                "2",
                "--work",
                str(tmp_path),
            ]
        )
        == 1
    )
