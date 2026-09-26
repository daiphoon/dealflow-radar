import pytest

from backend.app.research_evaluation import QUALITY_COUNTS, scorecard


def judgment(**changes):
    return (
        dict(
            expected=4,
            output=2,
            correct_output=2,
            matched_expected=2,
            cost=1,
            seconds=3,
            **dict.fromkeys(QUALITY_COUNTS, 0),
        )
        | changes
    )


def test_partial_metrics_cannot_be_formal_acceptance():
    result = scorecard([judgment(), None])
    assert result["status"] == "partial_judgments"
    assert result["precision"] is None and result["known_set_recall"] is None
    assert result["reviewed_subset"]["precision"] == 1
    assert result["missing_count"] == 1 and result["sample_count"] == 2


@pytest.mark.parametrize("field", ["cost", "seconds"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), -1])
def test_nonfinite_or_negative_resources_rejected(field, value):
    with pytest.raises(ValueError, match="resource"):
        scorecard([judgment(**{field: value})])


def test_frozen_denominator_survives_missing_body_or_judgment():
    benchmark = [
        dict(expected=4, allowed_date_precision=["day", "unknown"]),
        dict(expected=6, allowed_date_precision=["month"]),
    ]
    result = scorecard([judgment(), None], benchmark=benchmark)
    assert result["expected_total"] == 10
    assert result["recall_bounds"] == [0.2, 0.8]
    assert result["known_set_recall"] is None
    full = scorecard([judgment(), judgment(expected=6)], benchmark=benchmark)
    assert full["status"] == "frozen_benchmark_judgments"
    assert full["known_set_recall"] == 0.4 and full["formal_metrics_available"]
    with pytest.raises(ValueError, match="benchmark"):
        scorecard([judgment(expected=1), None], benchmark=benchmark)


def test_unknown_and_zero_are_not_invented():
    result = scorecard([None, None])
    assert result["missing_count"] == 2 and result["cost"] is None
    result = scorecard([judgment(output=0, correct_output=0, matched_expected=0, cost=None)])
    assert result["cost"] is None and result["cost_per_qualified"] is None
    assert result["reviewed_subset"]["precision"] is None
    with pytest.raises(ValueError):
        scorecard([judgment(output=-1)])


def test_replay_missing_body_keeps_frozen_case_and_expected_count(tmp_path, monkeypatch):
    import json
    from unittest.mock import patch

    from scripts.research_replay import replay_file

    monkeypatch.chdir(tmp_path)
    output = tmp_path / "data/private/replay"
    source = tmp_path / "synthetic.json"
    source.write_text(
        json.dumps(
            {
                "sample_origin": "synthetic",
                "cases": [{"id": "no-body", "not_retained_reason": "unavailable"}],
                "frozen_benchmark": [
                    {"id": "no-body", "expected": 7, "allowed_date_precision": ["unknown"]}
                ],
            }
        )
    )
    with (
        patch("scripts.research_replay.subprocess.check_output", return_value="fixture"),
        patch("scripts.research_replay.ScriptDirectory.from_config") as script,
    ):
        script.return_value.get_current_head.return_value = "0036"
        replay_file(source, output)
    result = json.loads((output / "manifest.json").read_text())["scorecard"]
    assert result["expected_total"] == 7 and result["missing_count"] == 1
    assert result["known_set_recall"] is None
