from simcity_ai_mayor.phase1.probe import _mismatch_details


def test_mismatch_details_reports_first_index_and_sizes() -> None:
    index, sizes = _mismatch_details([(1280, 720), (1280, 720), (1600, 900)])
    assert index == 2
    assert sizes == [(1600, 900)]
