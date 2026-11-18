"""Known-answer tests of the download step, offline: a local file served by URL is fetched and checked against
its checksum; a wrong checksum keeps nothing; archives unpack only the wanted
members into the loader's layout; a member name that would leave the target folder is refused."""
import hashlib
import io
import json
import tarfile
import zipfile

import pytest

from mocapref import download as dl


def _serve(tmp_path, payload: bytes):
    src = tmp_path / 'served.bin'
    src.write_bytes(payload)
    return src.as_uri()


def test_fetch_checks_and_keeps_a_correct_file(tmp_path):
    payload = bytes(range(256)) * 1000
    url = _serve(tmp_path, payload)
    md5 = hashlib.md5(payload).hexdigest()
    out = dl.fetch(url, tmp_path / 'a' / 'file.bin', md5=md5, size=len(payload))
    assert out.read_bytes() == payload
    assert dl.fetch(url, out, md5=md5) == out  # existing and correct: kept


def test_fetch_wrong_checksum_keeps_nothing(tmp_path):
    url = _serve(tmp_path, b'abc')
    with pytest.raises(IOError):
        dl.fetch(url, tmp_path / 'b.bin', sha256='0' * 64)
    assert not (tmp_path / 'b.bin').exists() and not (tmp_path / 'b.bin.part').exists()


def test_existing_file_that_does_not_match_is_refused(tmp_path):
    (tmp_path / 'c.bin').write_bytes(b'old')
    with pytest.raises(IOError):
        dl.fetch(_serve(tmp_path, b'new'), tmp_path / 'c.bin', md5=hashlib.md5(b'new').hexdigest())


def test_extract_zip_prefix_and_strip(tmp_path):
    archive = tmp_path / 'x.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('researchdata/P01_S01/SYNC_DATA/a.csv', 'a')
        z.writestr('researchdata/Supplementary.xlsx', 'x')
    dl.extract_zip(archive, tmp_path / 'out', prefix='researchdata/P', strip='researchdata/')
    assert (tmp_path / 'out/P01_S01/SYNC_DATA/a.csv').read_text() == 'a'
    assert not (tmp_path / 'out/Supplementary.xlsx').exists()


def test_extract_refuses_unsafe_names(tmp_path):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('../escape.txt', 'x')
    with pytest.raises(IOError):
        dl.extract_zip(archive, tmp_path / 'out')


def test_extract_tar_strips_top_folder(tmp_path):
    archive = tmp_path / 'x.tar.gz'
    with tarfile.open(archive, 'w:gz') as t:
        for name in ('files/Subject01/walking/m.trc', 'files/.tmb/thumb.png'):
            info = tarfile.TarInfo(name)
            info.size = 1
            t.addfile(info, io.BytesIO(b'1'))
    dl.extract_tar(archive, tmp_path / 'out', prefix='files/', strip='files/')
    assert (tmp_path / 'out/Subject01/walking/m.trc').exists()
    assert not (tmp_path / 'out/.tmb').exists()


def test_download_marks_complete_and_reports_manual_steps(tmp_path, monkeypatch):
    calls = []
    source = dl.DATASETS['reemc']
    monkeypatch.setitem(dl.DATASETS, 'reemc', dl.Source(source.host, source.doi, source.licence, source.files,
                                                        lambda dest, o: calls.append(dest)))
    status = dl.download(['reemc', 'mfimt_d01'], tmp_path)
    assert status['reemc'] == 'done' and status['mfimt_d01'].startswith('manual step')
    assert json.loads((tmp_path / 'reemc' / dl.DONE).read_text())['doi'] == source.doi
    assert dl.download('reemc', tmp_path)['reemc'].startswith('skipped') and len(calls) == 1


def test_every_paper_dataset_has_a_source():
    from mocapref.datasets import PAPER_DATASETS
    assert set(dl.DATASETS) == set(PAPER_DATASETS)
