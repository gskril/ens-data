"""Package release-only evidence, excluding every Git-tracked data file.

Run with no active pipeline writer: python scripts/package_release.py OUTPUT_DIR
Requires tar, pigz, and split. Produces 1 GiB archive parts and checksums.
"""
import hashlib
import json
import sqlite3
import subprocess
import sys
import tarfile
from contextlib import closing
from pathlib import Path


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    root = Path(__file__).resolve().parents[1]
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((root / 'data/manifest.json').read_text())
    assert manifest['status'] == 'complete' and manifest['last_error'] is None
    journal = root / 'data/journal.sqlite'
    assert not Path(str(journal) + '-wal').exists(), 'Stop writer and checkpoint SQLite before packaging'
    print('Checking SQLite structural integrity and event count', flush=True)
    with closing(sqlite3.connect(f'file:{journal}?mode=ro', uri=True)) as db:
        assert db.execute('PRAGMA quick_check').fetchall() == [('ok',)]
        assert db.execute('SELECT count(*) FROM events').fetchone()[0] == manifest['event_count']
    tracked = set(subprocess.check_output(['git', 'ls-files', '-z', 'data'], cwd=root).decode().split('\0'))
    files = sorted([root / 'data/events.csv.gz', journal] + [p for p in (root / 'data/raw').rglob('*') if p.is_file()])
    names = [str(p.relative_to(root)) for p in files]
    assert not tracked.intersection(names), 'Release data must not duplicate tracked files'
    assert all(not p.is_symlink() for p in files)
    assert all(not p.name.endswith(('.tmp', '-wal', '-shm')) for p in files)
    print('Hashing release evidence', flush=True)
    expected = {name: sha256(path) for name, path in zip(names, files)}
    assert expected['data/events.csv.gz'] == manifest['files_sha256']['events.csv.gz']
    # Use the frozen snapshot date for readable download names.
    from datetime import datetime, timezone
    date = datetime.fromtimestamp(manifest['end_timestamp'], timezone.utc).date().isoformat()
    prefix = f'snapshot-{date}-evidence.tar.gz.part-'
    assert not list(output.glob(prefix + '*')), 'Output parts already exist'
    listing = output / 'archive-files.tmp'
    listing.write_bytes(b'\0'.join(name.encode() for name in names) + b'\0')
    print(f'Packaging {len(files)} files', flush=True)
    tar = subprocess.Popen(['tar', '--null', '-T', str(listing), '-cf', '-'], cwd=root, stdout=subprocess.PIPE)
    compressor = subprocess.Popen(['pigz', '-p', '2', '-6'], stdin=tar.stdout, stdout=subprocess.PIPE)
    tar.stdout.close()
    splitter = subprocess.Popen(['split', '-b', '1G', '-d', '-a', '3', '-', str(output / prefix)], stdin=compressor.stdout)
    compressor.stdout.close()
    codes = [splitter.wait(), compressor.wait(), tar.wait()]
    assert codes == [0, 0, 0], codes
    listing.unlink()
    parts = sorted(output.glob(prefix + '*'))
    verify_archive(root, output, manifest, expected, parts)


def verify_archive(root, output, manifest, expected, parts):
    print('Checking every archived file against its source hash', flush=True)
    cat = subprocess.Popen(['cat', *map(str, parts)], stdout=subprocess.PIPE)
    decompressor = subprocess.Popen(['pigz', '-dc'], stdin=cat.stdout, stdout=subprocess.PIPE)
    cat.stdout.close()
    seen = set()
    with tarfile.open(fileobj=decompressor.stdout, mode='r|') as archive:
        for member in archive:
            assert member.name in expected and member.name not in seen, member.name
            if member.islnk():
                assert member.linkname in seen, member.linkname
                digest = expected[member.linkname]
            else:
                assert member.isfile(), member.name
                digest = hashlib.file_digest(archive.extractfile(member), 'sha256').hexdigest()
            assert digest == expected[member.name], member.name
            seen.add(member.name)
    # Drain the trailer to verify gzip CRC as well as all tar members.
    while decompressor.stdout.read(1024 * 1024):
        pass
    assert decompressor.wait() == 0 and cat.wait() == 0
    assert seen == set(expected)
    (output / 'SHA256SUMS').write_text(''.join(f'{sha256(p)}  {p.name}\n' for p in parts))
    report = dict(status='passed', source_git_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root).decode().strip(),
                  start_block=manifest['start_block'], end_block=manifest['end_block'],
                  end_block_hash=manifest['end_block_hash'], event_count=manifest['event_count'],
                  archive_file_count=len(expected), archive_bytes=sum(p.stat().st_size for p in parts),
                  checks=['SQLite structural integrity and event count', 'every archived file SHA-256 matches source',
                          'gzip integrity', 'no Git-tracked data files in archive', 'only raw evidence, journal, and event audit'],
                  files_sha256=expected)
    (output / 'ARCHIVE_VALIDATION.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'files_sha256'}), flush=True)


if __name__ == '__main__':
    main()
