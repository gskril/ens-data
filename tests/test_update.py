import json

import polars as pl
import pytest

from ens_data.daily_prices import DailyPrices
from ens_data.pipeline import Pipeline


class Chain:
    def __init__(self):
        self.finalized = 105
        self.changed = False

    def call(self, method, params):
        assert method == 'eth_chainId'
        return '0x1'

    def block(self, number):
        number = self.finalized if number == 'finalized' else number
        return dict(number=hex(number), timestamp=hex(1700000000 + number * 12),
                    hash=f'0x{number:064x}' if not self.changed else '0xbad')


def pipeline(tmp_path, monkeypatch):
    p = Pipeline(tmp_path, 'unused', chunk_size=4)
    p.rpc = Chain()
    seen = []

    def process(first, stop):
        seen.append((first, stop))
        with p.db:
            p.db.execute('INSERT INTO chunks VALUES (?,?)', (first, stop))
            p.commit_expiries(first, stop, {})
        return 0

    monkeypatch.setattr(p, 'process_chunk', process)
    monkeypatch.setattr('ens_data.daily_prices.enrich', lambda p: None)
    p.run(100, 105)
    return p, seen


def test_update_starts_after_short_last_chunk_and_resumes_without_overlap(tmp_path, monkeypatch):
    p, seen = pipeline(tmp_path, monkeypatch)
    assert seen == [(100, 104), (104, 106)]
    (tmp_path / 'daily_eth_usd.csv').write_text('stale previous cutoff')
    p.rpc.finalized = 114
    p.update()
    assert seen == [(100, 104), (104, 106), (106, 110), (110, 114), (114, 115)]
    assert not (tmp_path / 'daily_eth_usd.csv').exists()
    manifest = json.loads((tmp_path / 'manifest.json').read_text())
    assert manifest['status'] == 'complete'
    assert manifest['extensions'][0]['previous_end_block'] == 105
    assert manifest['end_block'] == 114
    assert manifest['usd_conversion']['status'] == 'not_indexed'
    p.run(100, None)
    assert len(seen) == 5
    p.rpc.finalized = 117
    p.update()
    assert seen[-1] == (115, 118)
    assert len(seen) == 6


def test_interrupted_update_keeps_target_and_committed_prefix(tmp_path, monkeypatch):
    p, seen = pipeline(tmp_path, monkeypatch)
    process = p.process_chunk

    def interrupt(first, stop):
        if first == 110:
            raise RuntimeError('interrupted')
        return process(first, stop)

    monkeypatch.setattr(p, 'process_chunk', interrupt)
    p.rpc.finalized = 114
    with pytest.raises(RuntimeError, match='interrupted'):
        p.update()
    assert json.loads((tmp_path / 'manifest.json').read_text())['status'] == 'partial'
    monkeypatch.setattr(p, 'process_chunk', process)
    p.rpc.finalized = 120
    p.update()
    assert seen == [(100, 104), (104, 106), (106, 110), (110, 114), (114, 115)]
    assert json.loads((tmp_path / 'manifest.json').read_text())['end_block'] == 114


@pytest.mark.parametrize('failure', ['hash', 'unfinalized', 'backward', 'expiry', 'gap'])
def test_update_rejects_invalid_anchor_range_or_coverage(tmp_path, monkeypatch, failure):
    p, _ = pipeline(tmp_path, monkeypatch)
    original = p.db.execute("SELECT value FROM meta WHERE key='run'").fetchone()[0]
    end = None
    if failure == 'hash':
        p.rpc.changed = True
    elif failure == 'unfinalized':
        end = 106
    elif failure == 'backward':
        end = 104
    elif failure == 'expiry':
        p.db.execute('DELETE FROM expiry_chunks WHERE start=104')
    else:
        p.db.execute('DELETE FROM chunks WHERE start=100')
    p.db.commit()
    with pytest.raises(ValueError):
        p.update(end)
    assert p.db.execute("SELECT value FROM meta WHERE key='run'").fetchone()[0] == original


def test_price_log_extension_only_fetches_new_blocks(tmp_path, monkeypatch):
    p = Pipeline(tmp_path, 'unused')
    directory = p.raw / 'daily_price_logs'
    directory.mkdir()
    pl.DataFrame({'block_number': [100, 104]}).write_parquet(directory / 'phase-7-100-106.parquet')
    requested = []

    def collect(dataset, key, **kwargs):
        requested.append(kwargs['blocks'])
        return pl.DataFrame({'block_number': [106, 108]})

    monkeypatch.setattr(p, 'collect', collect)
    prices = DailyPrices(p, {})
    rows = prices.logs(7, '0xfeed', 100, 110)
    assert requested == [['106:110']]
    assert [r['block_number'] for r in rows] == [100, 104, 106, 108]


def test_price_phases_discover_new_phase_after_cached_anchor(tmp_path, monkeypatch):
    from ens_data.oracle import FEED
    p = Pipeline(tmp_path, 'unused')
    p.rpc = Chain()
    prices = DailyPrices(p, dict(end_block=120, end_block_hash=p.rpc.block(120)['hash']))
    old = dict(phase=1, address='0x' + '11' * 20, activation_block=100, activation_timestamp=1700001200)
    (prices.directory / 'phases.json').write_text(json.dumps(dict(
        feed=FEED, end_block=105, end_block_hash=p.rpc.block(105)['hash'], phases=[old])))
    monkeypatch.setattr(prices, 'integer', lambda address, method, block: 2 if block >= 110 else (1 if block >= 100 else 0))
    monkeypatch.setattr(prices, 'call', lambda *a: bytes.fromhex('00' * 12 + '22' * 20))
    phases = prices.phases()
    assert phases[0] == old
    assert phases[1]['activation_block'] == 110
    assert phases[1]['address'] == '0x' + '22' * 20
