from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from bulk_download import cli


def test_supplied_manifest_state_avoids_reparsing(monkeypatch, tmp_path):
    config = SimpleNamespace(temp_dir=tmp_path/'media', manifest_file=tmp_path/'manifest', http=None)
    # An unreadable old manifest proves the incremental indexes are used.
    config.manifest_file.write_text('not json\n')
    done = {'https://hotscope.tv/video/previous'}
    discover = Mock(return_value=([], []))
    monkeypatch.setattr(cli, 'load_csek', lambda _: b'key')
    monkeypatch.setattr(cli, 'StorageAdapter', Mock())
    monkeypatch.setattr(cli, 'Fetcher', MagicMock())
    monkeypatch.setattr(cli, '_discover_all', discover)
    assert cli.run(config, manifest_state=(done, {})) == 0
    assert discover.call_args.args[-1] is done
