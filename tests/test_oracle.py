from ens_data.oracle import fee_for, round_series, solidity_length


def update(block, tx, index, round_id, answer):
    return dict(block_number=block, transaction_index=tx, log_index=index,
                topic1="0x" + answer.to_bytes(32, "big", signed=True).hex(), topic2=hex(round_id))


def test_feed_history_requires_every_round_and_exact_end_state():
    phase = 6 << 64
    before = [phase + 10, 100]
    after = [phase + 12, 120]
    rows = [update(100, 1, 5, 11, 110), update(101, 2, 10, 12, 120)]
    series = round_series(before, after, rows, 100)
    assert [r[2] for r in series] == [100, 110, 120]
    assert series[1][0] == (100, 1, 5)  # retain transaction/log ordering, not just day/block
    assert round_series(before, after, rows[1:], 100) is None
    assert round_series(before, [phase + 12, 121], rows, 100) is None
    assert round_series(before, [(7 << 64) + 12, 120], rows, 100) is None


def test_deployed_solidity_length_and_exact_integer_fee():
    assert solidity_length("abc".encode()) == 3
    assert solidity_length("猫猫猫".encode()) == 3
    assert solidity_length("👨‍👩‍👧".encode()) == 5  # code points, not grapheme clusters
    assert fee_for(b"cengizter", 31536000, 165063643000) == 3029134647177018
    assert fee_for(b"abc", 31536000, 165063643000) > fee_for(b"abcd", 31536000, 165063643000)
