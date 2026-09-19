import base64
import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import google_crc32c
import pytest
from bulk_download.download import (
    Downloaded,
    _validate_hls,
    ffmpeg_executable,
    media_fingerprint,
)
from bulk_download.storage import StorageAdapter
from bulk_download.verification import VerificationError


def test_packet_fingerprint_ignores_container_tags_but_not_content(tmp_path):
    first, tagged, different = [tmp_path / name for name in ('first.mp4', 'tagged.mp4', 'different.mp4')]
    for path, color in [(first, 'black'), (different, 'white')]:
        subprocess.run([ffmpeg_executable(), '-v', 'error', '-f', 'lavfi', '-i',
                        f'color=c={color}:s=32x32:r=5', '-t', '1', '-c:v', 'libx264', str(path)], check=True)
    subprocess.run([ffmpeg_executable(), '-v', 'error', '-i', str(first), '-c', 'copy',
                    '-metadata', 'comment=different container metadata', str(tagged)], check=True)
    assert first.read_bytes() != tagged.read_bytes()
    assert media_fingerprint(first) == media_fingerprint(tagged)
    assert media_fingerprint(first) != media_fingerprint(different)


def test_reuse_reports_archived_hash_and_size_without_overwriting(tmp_path):
    key = b'k' * 32
    adapter = StorageAdapter.__new__(StorageAdapter)
    adapter.csek = key
    adapter.config = SimpleNamespace(chunk_bytes=8)
    archived = b'archived container'
    blob = Mock(size=len(archived), content_type='video/mp4', generation=123,
                crc32c=base64.b64encode(google_crc32c.Checksum(archived).digest()).decode())
    blob._properties = {'customerEncryption': {'encryptionAlgorithm':'AES256',
        'keySha256':base64.b64encode(hashlib.sha256(key).digest()).decode()}}
    blob.download_to_filename.side_effect = lambda name, **kwargs: Path(name).write_bytes(archived)
    fresh = tmp_path/'fresh.part';fresh.write_bytes(b'new container')
    media = Downloaded(fresh, 'video/mp4', '.mp4', fresh.stat().st_size, 'fresh-hash')
    with patch('bulk_download.storage.media_fingerprint', return_value=b'identical packets'):
        reused = adapter.reuse_existing(blob, media, allow_remux=True)
        assert reused.sha256 == hashlib.sha256(archived).hexdigest()
        assert reused.size == len(archived)
    blob.download_to_filename.assert_called_once_with(str(fresh.with_suffix('.existing.mp4')), if_generation_match=123, checksum='crc32c')
    blob.upload_from_filename.assert_not_called()
    assert not fresh.with_suffix('.existing.mp4').exists()
    with patch('bulk_download.storage.media_fingerprint', side_effect=[b'old', b'new']), pytest.raises(VerificationError, match='packets or timing differ'):
        adapter.reuse_existing(blob, media, allow_remux=True)
    with pytest.raises(VerificationError):
        adapter.reuse_existing(blob, media, allow_remux=False)
    blob._properties['customerEncryption']['keySha256'] = 'wrong'
    with patch('bulk_download.storage.media_fingerprint', return_value=b'identical packets'), pytest.raises(VerificationError, match='encryption_key'):
        adapter.reuse_existing(blob, media, allow_remux=True)


def test_reserved_color_fallback_still_requires_complete_decode_and_duration(tmp_path):
    path = tmp_path/'media.part';path.write_bytes(b'original')
    bad = SimpleNamespace(returncode=1, stderr=b'Invalid color space', stdout=b'')
    good = SimpleNamespace(returncode=0, stderr=b'', stdout=b'out_time_us=10000000\n')
    with patch('bulk_download.download._decode_check', side_effect=[bad,good]) as decode, patch('bulk_download.download.subprocess.run', return_value=SimpleNamespace(returncode=0)):
        _validate_hls(path, 10)
        assert decode.call_count == 2
        assert path.read_bytes() == b'original'
        assert not path.with_suffix('.validation.mp4').exists()
    with patch('bulk_download.download._decode_check', side_effect=[bad,good]), patch('bulk_download.download.subprocess.run', return_value=SimpleNamespace(returncode=0)), pytest.raises(VerificationError, match='Expected 30.000s; decoded 10.000s'):
        _validate_hls(path, 30)
    with patch('bulk_download.download._decode_check', side_effect=[bad,bad]), patch('bulk_download.download.subprocess.run', return_value=SimpleNamespace(returncode=0)), pytest.raises(VerificationError):
        _validate_hls(path, 10)
