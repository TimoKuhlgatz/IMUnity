"""Download the datasets from their original depositors into the layout the loaders read.

    from imunity.download import download
    download()                              # all 18 datasets (imunity.datasets.ALL_DATASETS)
    download('paper')                       # the 15 the paper reports (imunity.datasets.PAPER_DATASETS)
    download(['is2b', 'dfjimu'])            # a subset
    download('compliant_terrain', all_participants=True)

    uv run python -m imunity.download is2b dfjimu [--root <folder>] [--all-participants] [--keep-archives]

Target: `<root>/<dataset>/`, root = IMUNITY_DATA, else `data/` in the repository (the folder the loaders read).

What is fetched: only the files the loaders read, each from the host where its depositors published it (Zenodo,
figshare, Mendeley Data, Harvard Dataverse, TUdatalib, Yareta, Science Data Bank, the Leibniz University Hannover
data repository, GitHub). By default the participant subset our results are based on: Delaware compliant terrain
SUB01-SUB02 (`all_participants=True` adds SUB03-SUB20, about 54 GB, not used in the paper) and Camargo part 1
(AB06-AB14; parts 2 and 3 are not read by the loader). DATASETS gives host, DOI, files and licence per dataset.
Trento's `Data/` folder also holds the authors' processed products that the helper readers `bonfiglio_final` and
`bonfiglio_final_deposited` read (B_dotComplete, 30. Results/qGS_Optotrack); nothing extra is fetched for them.

Integrity: every file is checked against the checksum its host publishes (MD5 or SHA-256), or against the SHA-256
of the copy our results were computed on where the host publishes none; the two GitHub sources are pinned to a
commit, which fixes their content. A failed check stops that dataset and
keeps nothing. Downloads resume after an interruption (`<file>.part`). A finished dataset carries
`.download_complete.json` and is skipped on the next call.

Two datasets cannot be fetched by a script:
    opensense   SimTK lets only logged-in users download (free account). Download `study20-latest.tar.gz` from
                https://simtk.org/plugins/datashare/?group_id=2164 (doi:10.18735/2f9k-ca43), put it in
                `<root>/opensense/` and call download('opensense') again: it is checked and unpacked.
    mfimt_d01   not publicly deposited (yet). Put `chain_experiment_d01.npz` into `<root>/mfimt/`; it is checked.

After Geneva (grouvel) is downloaded, its IMU streams are rebuilt from the raw session logs (73 s on 10 processes),
which the corrected pipeline reads for the mis-assembled streams (error 10); `prepare=False` skips this.

Hip-ROM-Y is a RAR5 archive: unpacking needs `bsdtar` (libarchive >= 3.4), 7-Zip >= 21 (`7zz`), `unrar` or
`unar` on the PATH.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .datasets.base import DATA_ROOT, PREPARED

USER_AGENT = 'imunity-download (python urllib)'
CHUNK = 1 << 22
DONE = '.download_complete.json'


# ---------------------------------------------------------------------------------------------------------------
# transfer and checks

def _request(url: str, data: bytes | None = None, headers: dict | None = None, method: str | None = None):
    req = urllib.request.Request(url, data=data, method=method, headers={'User-Agent': USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(req, timeout=300)


def _json(url: str):
    with _request(url, headers={'Accept': 'application/json'}) as r:
        return json.load(r)


def _hash(path: Path, algorithm: str) -> str:
    h = hashlib.new(algorithm)
    with open(path, 'rb') as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _check(path: Path, md5: str | None = None, sha256: str | None = None, size: int | None = None) -> None:
    if size is not None and path.stat().st_size != size:
        raise IOError(f'{path.name}: {path.stat().st_size} bytes, the host states {size}')
    for algorithm, expected in (('md5', md5), ('sha256', sha256)):
        if expected and _hash(path, algorithm) != expected.lower():
            raise IOError(f'{path.name}: {algorithm} differs from the published {expected}')


def fetch(url: str, path: Path, md5: str | None = None, sha256: str | None = None, size: int | None = None) -> Path:
    """Download `url` to `path` (resuming a `.part` file), then check size and checksum. An existing `path` that
    passes the checks is kept without downloading again."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _check(path, md5, sha256, size)  # a file that does not match is not silently replaced
        return path
    part = path.with_name(path.name + '.part')
    have = part.stat().st_size if part.exists() else 0
    headers = {'Range': f'bytes={have}-'} if have else {}
    print(f'  {path.name}: downloading{" (resuming)" if have else ""} from {url}', flush=True)
    with _request(url, headers=headers) as r:
        mode = 'ab' if have and r.status == 206 else 'wb'
        with open(part, mode) as f:
            shutil.copyfileobj(r, f, CHUNK)
    try:
        _check(part, md5, sha256, size)
    except IOError:
        part.unlink()
        raise
    part.rename(path)
    return path


def _members(names: list[str], prefix: str, strip: str) -> list[tuple[str, str]]:
    """(member, target relative path) for every file member under `prefix`, with `strip` removed."""
    out = []
    for name in names:
        if name.endswith('/') or not name.startswith(prefix) or '/.tmb/' in name:
            continue
        rel = name[len(strip):] if name.startswith(strip) else name
        if rel.startswith('/') or '..' in Path(rel).parts:
            raise IOError(f'unsafe member name {name!r}')
        out.append((name, rel))
    return out


def extract_zip(archive: Path, dest: Path, prefix: str = '', strip: str = '') -> None:
    """Unpack the members of a zip archive under `prefix` into `dest`, removing `strip` from their names (the zip
    CRC of every member is checked while it is read)."""
    with zipfile.ZipFile(archive) as z:
        for member, rel in _members(z.namelist(), prefix, strip):
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(member) as src, open(target, 'wb') as out:
                shutil.copyfileobj(src, out, CHUNK)


def extract_tar(archive: Path, dest: Path, prefix: str = '', strip: str = '') -> None:
    with tarfile.open(archive) as t:
        wanted = dict(_members([m.name for m in t.getmembers() if m.isfile()], prefix, strip))
        for member in t.getmembers():
            if member.name in wanted:
                target = dest / wanted[member.name]
                target.parent.mkdir(parents=True, exist_ok=True)
                with t.extractfile(member) as src, open(target, 'wb') as out:
                    shutil.copyfileobj(src, out, CHUNK)


def extract_rar(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    tools = {'bsdtar': ['bsdtar', '-xf', str(archive), '-C', str(dest)],
             '7zz': ['7zz', 'x', '-y', f'-o{dest}', str(archive)],
             'unrar': ['unrar', 'x', '-o+', str(archive), f'{dest}/'],
             'unar': ['unar', '-f', '-o', str(dest), str(archive)]}
    for tool, command in tools.items():
        if shutil.which(tool):
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
            return
    raise RuntimeError(f'{archive.name} is a RAR5 archive; install bsdtar (libarchive), 7-Zip (7zz), unrar or unar')


def _remove(path: Path, keep: bool) -> None:
    if not keep and path.exists():
        path.unlink()


# ---------------------------------------------------------------------------------------------------------------
# hosts

def zenodo(record: int, names: list[str], dest: Path) -> list[Path]:
    """Files of one Zenodo record version, checked against Zenodo's MD5."""
    files = {f['key']: f for f in _json(f'https://zenodo.org/api/records/{record}')['files']}
    out = []
    for name in names:
        f = files[name]
        algorithm, value = f['checksum'].split(':')
        out.append(fetch(f['links']['self'], dest / name, size=f['size'], **{algorithm: value}))
    return out


def figshare(article: int, names: list[str], dest: Path) -> list[Path]:
    """Files of one figshare article, checked against figshare's MD5."""
    files = {f['name']: f for f in _json(f'https://api.figshare.com/v2/articles/{article}/files?page_size=1000')}
    return [fetch(files[n]['download_url'], dest / n, md5=files[n]['supplied_md5'], size=files[n]['size'])
            for n in names]


# ---------------------------------------------------------------------------------------------------------------
# one function per dataset: fetch into `dest` (= <root>/<dataset>) in the layout its loader reads

@dataclass
class Options:
    all_participants: bool = False
    keep_archives: bool = False
    prepare: bool = True
    processes: int = 10


def _tuk6min(dest: Path, o: Options) -> None:
    (archive,) = zenodo(10253111, ['HDF5_6_min_walking_test.zip'], dest)
    extract_zip(archive, dest / '_extracted')
    _remove(archive, o.keep_archives)


def _opensense(dest: Path, o: Options) -> None:
    archive = dest / 'study20-latest.tar.gz'
    if not archive.exists():
        raise ManualStep(f'SimTK requires a (free) login. Download study20-latest.tar.gz from '
                         f'https://simtk.org/plugins/datashare/?group_id=2164 (doi:10.18735/2f9k-ca43) into {dest}/ '
                         'and call download("opensense") again.')
    _check(archive, sha256='d15f9a29ad18621ad34e761b31eb01377449f06984a0848222227c5ef8828b90')
    extract_tar(archive, dest / '_extracted', prefix='files/', strip='files/')
    _remove(archive, o.keep_archives)


def _fullbody17(dest: Path, o: Options) -> None:
    for archive in zenodo(19632046, [f'S{i:02d}.zip' for i in range(1, 13)], dest):
        # S01 and S02 hold a folder S<nn>/, S03-S12 their files at the top: each into its own folder
        extract_zip(archive, dest / '_extracted' / archive.stem)
        _remove(archive, o.keep_archives)


def _camargo(dest: Path, o: Options) -> None:
    """Part 1 of 3 (AB06-AB14); the loader reads the subject zips directly."""
    listing = _json('https://data.mendeley.com/public-api/datasets/fcgm3chfff/files?folder_id=root&version=1')
    for f in listing:
        if f['filename'].startswith('AB') and f['filename'].endswith('.zip'):
            c = f['content_details']
            fetch(c['download_url'], dest / f['filename'], sha256=c['sha256_hash'], size=c['size'])


def _wmcg(dest: Path, o: Options) -> None:
    (archive,) = figshare(29328746, ['WMCG_dataset.zip'], dest)  # 1 of the deposit's files (3 GB of 143 GB)
    extract_zip(archive, dest / '_extracted')
    _remove(archive, o.keep_archives)


def _compliant_terrain(dest: Path, o: Options) -> None:
    participants = range(1, 21) if o.all_participants else range(1, 3)  # the paper used SUB01-SUB02
    figshare(27180288, [f'SUB{i:02d}.mat' for i in participants], dest)


def _hip_rom_y(dest: Path, o: Options) -> None:
    archive = fetch('https://download.scidb.cn/download?fileId=40a1e3ee577955fd2e673fe00ade1cb7'
                    '&path=/V5/hip_ROM_dataset.rar&fileName=hip_ROM_dataset.rar', dest / 'hip_ROM_dataset.rar',
                    sha256='f79a7b9a2f2403e3a7b2377baf95c6e2f26807422a7f6b318de13023f0751329', size=1487160391)
    extract_rar(archive, dest / '_extracted')
    _remove(archive, o.keep_archives)


IS2B_ARTICLES = {14170700: 'Vertical_plane_movements', 14170712: 'Horizontal_plane_movement',
                 14170715: 'Mixed_plane_movement', 14170718: 'Functional_movements', 14170721: 'Gyroscope_bias',
                 14170724: 'Magnetometer_calibration', 14170727: 'CT-data', 14170733: 'Inertial_sensor_alignment'}


def _is2b(dest: Path, o: Options) -> None:
    for article, title in IS2B_ARTICLES.items():
        files = _json(f'https://api.figshare.com/v2/articles/{article}/files?page_size=1000')
        figshare(article, [f['name'] for f in files], dest / f'{article}_{title}')


GROUVEL_AIP = 'https://access.yareta.unige.ch/access/metadata/e72b9ec2-d097-45ac-8126-d04035407f51'


def _grouvel(dest: Path, o: Options) -> None:
    """Yareta packs the whole deposit on request (2.8 GB); only the synchronised and raw IMU files are unpacked,
    each checked against the deposit's own MD5 manifest."""
    archive = dest / 'grouvel_dip.zip'
    if not archive.exists():
        with _request(f'{GROUVEL_AIP}/prepare-download', data=b'{}', method='POST',
                      headers={'Content-Type': 'application/json'}) as r:
            order = r.read().decode().strip().strip('"')
        for _ in range(240):
            with _request(f'https://access.yareta.unige.ch/access/metadata/{order}/download-status') as r:
                if r.read().decode().strip().strip('"') == 'READY':
                    break
            time.sleep(15)
        fetch(f'{GROUVEL_AIP}/download?orderId={order}', archive)
    extract_zip(archive, dest, prefix='researchdata/P', strip='researchdata/')
    manifest = _json(f'{GROUVEL_AIP}/data?page=0&size=1000')['_data']
    for entry in manifest:
        rel = entry['metadata']['file']['fullName'].lstrip('/')
        if rel.startswith('P') and (dest / rel).exists():
            _check(dest / rel, md5=entry['metadata']['checksums']['MD5'], size=entry['metadata']['file']['size'])
    _remove(archive, o.keep_archives)
    if o.prepare:
        out = PREPARED / 'grouvel' / 'raw-rebuild-v1'
        print(f'  rebuilding the Geneva IMU streams from the raw logs into {out}', flush=True)
        subprocess.run([sys.executable, '-m', 'imunity.datasets.readers.scripts.rebuild_geneva_from_raw',
                        '--out', str(out), '--workers', str(o.processes)], check=True)


def _bonfiglio(dest: Path, o: Options) -> None:
    (archive,) = zenodo(10935873, ['Optotrak vs DOT.zip'], dest)
    extract_zip(archive, dest / '_extracted', prefix='Optotrak vs DOT/Data/')
    _remove(archive, o.keep_archives)


TUDATALIB = 'https://tudatalib.ulb.tu-darmstadt.de/server/api/core/bitstreams'


def _grimmer_stairs(dest: Path, o: Options) -> None:
    """The loader reads the two zips directly."""
    for name, uuid, md5, size in (
            ('Preprocessed.zip', 'ffc932af-a7c6-43a1-aa4f-66c74b2ed531', 'cfa8571fb12713cf9f6e1db1a656e238', 4520656121),
            ('Processed.zip', 'ff22aab0-a21d-492d-981d-04b8f6e2bae9', '077e533fdfb5ac8828abad2087e3a2f6', 4226620934)):
        fetch(f'{TUDATALIB}/{uuid}/content', dest / name, md5=md5, size=size)


def _ultramocap(dest: Path, o: Options) -> None:
    (archive,) = figshare(28751156, ['ULTra-MoCap-processed.zip'], dest)  # the processed release only
    extract_zip(archive, dest / '_extracted')
    _remove(archive, o.keep_archives)


def _diodem(dest: Path, o: Options) -> None:
    """The `dataset/` folder of the Dataverse record (no videos, CAD files or build scripts)."""
    api = 'https://dataverse.harvard.edu/api'
    listing = _json(f'{api}/datasets/:persistentId/versions/:latest/files?persistentId=doi:10.7910/DVN/SGJLZA')
    for entry in listing['data']:
        folder, f = entry.get('directoryLabel', ''), entry['dataFile']
        if folder == 'dataset' or folder.startswith('dataset/'):
            fetch(f'{api}/access/datafile/{f["id"]}', dest / folder / f['filename'], md5=f['md5'],
                  size=f['filesize'])


DFJIMU_COMMIT = '33fff51c9f63545efa4d54d0d86b5e1b30c2b226'  # pinned: a git commit fixes the content of every file


def _dfjimu(dest: Path, o: Options) -> None:
    for name in [f'data_1D_0{i}' for i in (1, 2, 3, 4, 5)] + [f'data_2D_0{i}' for i in (1, 2, 3, 5, 7)] + \
            [f'data_3D_0{i}' for i in (1, 2, 3, 4, 5)]:
        fetch(f'https://raw.githubusercontent.com/daniel-om-weber/dfjimu/{DFJIMU_COMMIT}/data/{name}.mat',
              dest / f'{name}.mat')


def _mfimt_d01(dest: Path, o: Options) -> None:
    path = dest / 'chain_experiment_d01.npz'
    if not path.exists():
        raise ManualStep(f'not publicly deposited; place chain_experiment_d01.npz in {dest}/')
    _check(path, sha256='b8d398326cf43196d251506f909a98d370a508b24b5f405c6efc3792fea81978')


def _reemc(dest: Path, o: Options) -> None:
    zenodo(10253284, ['ROBOT_TURNING_0003.h5', 'ROBOT_CIRCLE_RUN_0003.h5', 'ROBOT_CIRCLE_RUN_0004.h5'], dest)


def _openaxes(dest: Path, o: Options) -> None:
    archive = fetch('https://data.uni-hannover.de/dataset/a5637b98-acb4-4487-bf1b-a32b42425be4/resource/'
                    'fdebcc7b-26cb-43f5-81ac-9b604ddae767/download/openaxes-example-robot-dataset.zip',
                    dest / 'openaxes-example-robot-dataset.zip',
                    sha256='78f31d1967842e693c2efb6ee0530e162cd509e4fa786745a485d502f2aa6da7', size=423808715)
    extract_zip(archive, dest / '_extracted', prefix='measure_raw-2022-09-15/')
    _remove(archive, o.keep_archives)


REPOIMU_COMMIT = '24145fc4778705177f3b6333dc2c967909240f9e'


def _repoimu(dest: Path, o: Options) -> None:
    archive = fetch(f'https://codeload.github.com/agnieszkaszczesna/RepoIMU/zip/{REPOIMU_COMMIT}',
                    dest / 'RepoIMU.zip')
    strip = f'RepoIMU-{REPOIMU_COMMIT}/'
    for folder in ('Pendulum', 'TStick'):
        extract_zip(archive, dest, prefix=strip + folder + '/', strip=strip)
    _remove(archive, o.keep_archives)


# ---------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Source:
    host: str
    doi: str
    licence: str
    files: str
    get: Callable[[Path, Options], None] = field(repr=False)
    folder: str = ''


DATASETS: dict[str, Source] = {
    'tuk6min': Source('Zenodo', '10.5281/zenodo.10253111', 'CC BY 4.0', 'HDF5_6_min_walking_test.zip', _tuk6min),
    'opensense': Source('SimTK (login required)', '10.18735/2f9k-ca43', 'see SimTK project page',
                        'study20-latest.tar.gz (placed by hand)', _opensense),
    'fullbody17': Source('Zenodo', '10.5281/zenodo.19632046', 'CC BY 4.0', 'S01-S12.zip', _fullbody17),
    'camargo': Source('Mendeley Data', '10.17632/fcgm3chfff.1', 'CC BY 4.0', 'AB06-AB14.zip (part 1)', _camargo),
    'wmcg': Source('figshare+', '10.25452/figshare.plus.29328746.v2', 'CC BY 4.0', 'WMCG_dataset.zip', _wmcg),
    'compliant_terrain': Source('figshare', '10.6084/m9.figshare.27180288.v1', 'CC BY 4.0',
                                'SUB01.mat, SUB02.mat (all: SUB01-SUB20)', _compliant_terrain),
    'hip_rom_y': Source('Science Data Bank', '10.57760/sciencedb.27443', 'CC BY 4.0', 'hip_ROM_dataset.rar (V5)',
                        _hip_rom_y),
    'is2b': Source('figshare', '10.6084/m9.figshare.c.5328773', 'CC BY 4.0', '8 articles of the collection', _is2b),
    'grouvel': Source('Yareta (Univ. Geneva)', '10.26037/yareta:xkxgaw6ewjdhfntdhtj7upepxy', 'CC BY 4.0',
                      'P??_S01/SYNC_DATA, P??_S01/RAW_DATA', _grouvel),
    'bonfiglio': Source('Zenodo', '10.5281/zenodo.10935873', 'CC BY 4.0', 'Optotrak vs DOT.zip (Data/)',
                        _bonfiglio),
    'grimmer_stairs': Source('TUdatalib (TU Darmstadt)', '10.48328/tudatalib-1182', 'CC BY 4.0',
                             'Preprocessed.zip, Processed.zip', _grimmer_stairs),
    'ultramocap': Source('figshare', '10.6084/m9.figshare.28751156.v1', 'CC BY 4.0', 'ULTra-MoCap-processed.zip',
                         _ultramocap),
    'diodem': Source('Harvard Dataverse', '10.7910/DVN/SGJLZA', 'see Dataverse record', 'dataset/', _diodem),
    'dfjimu': Source('GitHub (dfjimu package)', '-', 'see repository', 'data/*.mat (15 files)', _dfjimu),
    'mfimt_d01': Source('not deposited', '-', '-', 'chain_experiment_d01.npz (placed by hand)', _mfimt_d01,
                        folder='mfimt'),
    'reemc': Source('Zenodo', '10.5281/zenodo.10253284', 'CC BY 4.0', '3 .h5 files', _reemc),
    'openaxes': Source('Leibniz Univ. Hannover data repository', '10.25835/psqqs9w0', 'CC BY-SA 3.0',
                       'openaxes-example-robot-dataset.zip (measure_raw-2022-09-15/)', _openaxes),
    'repoimu': Source('GitHub (first author)', '-', 'see repository', 'Pendulum/, TStick/', _repoimu),
}


class ManualStep(RuntimeError):
    """The dataset needs a step by hand (a login or an undeposited file); the message says which."""


def download(datasets: str | list[str] = 'all', root: str | Path | None = None, *, all_participants: bool = False,
             keep_archives: bool = False, prepare: bool = True, processes: int = 10, force: bool = False
             ) -> dict[str, str]:
    """Fetch datasets from their depositors into `<root>/<dataset>/`: 'all' (the 18 of ALL_DATASETS), 'paper' (the
    15 of PAPER_DATASETS), one name or a list. Returns {dataset: 'done' | 'skipped (...)'
    | 'manual step: ...' | 'failed: ...'}; one dataset failing does not stop the others."""
    root = Path(root) if root is not None else DATA_ROOT
    if datasets == 'all':
        names = list(DATASETS)
    elif datasets == 'paper':
        from .datasets import PAPER_DATASETS
        names = list(PAPER_DATASETS)
    else:
        names = [datasets] if isinstance(datasets, str) else list(datasets)
    unknown = set(names) - set(DATASETS)
    if unknown:
        raise KeyError(f'unknown datasets {sorted(unknown)}; available: {", ".join(DATASETS)}')
    options = Options(all_participants, keep_archives, prepare, processes)
    status = {}
    for name in names:
        source = DATASETS[name]
        dest = root / (source.folder or name)
        marker = dest / DONE
        if marker.exists() and not force and json.loads(marker.read_text()).get('all_participants', False) >= \
                all_participants:
            status[name] = 'skipped (already downloaded)'
            continue
        print(f'{name}: {source.host}, doi:{source.doi}', flush=True)
        dest.mkdir(parents=True, exist_ok=True)
        try:
            source.get(dest, options)
        except ManualStep as e:
            status[name] = f'manual step: {e}'
            print(f'  {status[name]}', flush=True)
            continue
        except Exception as e:  # noqa: BLE001 - report and continue with the next dataset
            status[name] = f'failed: {type(e).__name__}: {e}'
            print(f'  {status[name]}', flush=True)
            continue
        marker.write_text(json.dumps(dict(host=source.host, doi=source.doi, files=source.files,
                                          all_participants=all_participants,
                                          date=time.strftime('%Y-%m-%d')), indent=1))
        status[name] = 'done'
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Download the datasets from their original depositors.')
    parser.add_argument('datasets', nargs='*', default=['all'],
                        help=f'any of: {", ".join(DATASETS)}; "all" (default, all 18) or "paper" (the 15 the paper '
                             'reports)')
    parser.add_argument('--root', type=Path, default=None, help=f'target folder (default {DATA_ROOT})')
    parser.add_argument('--all-participants', action='store_true',
                        help='Delaware compliant terrain: all 20 participants, not only the 2 the paper used')
    parser.add_argument('--keep-archives', action='store_true', help='keep archives after unpacking')
    parser.add_argument('--no-prepare', action='store_true', help='do not rebuild the Geneva IMU streams')
    parser.add_argument('--processes', type=int, default=10)
    parser.add_argument('--force', action='store_true', help='fetch again even if marked complete')
    a = parser.parse_args(argv)
    status = download(a.datasets[0] if a.datasets in (['all'], ['paper']) else a.datasets, a.root, all_participants=a.all_participants,
                      keep_archives=a.keep_archives, prepare=not a.no_prepare, processes=a.processes, force=a.force)
    for name, s in status.items():
        print(f'{name:18s} {s}')
    return 0 if all(s == 'done' or s.startswith('skipped') for s in status.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
