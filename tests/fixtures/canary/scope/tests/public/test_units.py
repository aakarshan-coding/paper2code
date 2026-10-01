from canary_method import baseline, ema


def test_ema_first_element_is_input():
    assert ema([2.0, 4.0], 0.5)[0] == 2.0


def test_ema_known_values():
    assert ema([0.0, 1.0, 1.0], 0.5) == [0.0, 0.5, 0.75]


def test_ema_preserves_length():
    assert len(ema([1.0] * 7, 0.3)) == 7


def test_baseline_is_identity_copy():
    xs = [1.0, 2.0]
    out = baseline(xs)
    assert out == xs
    assert out is not xs
