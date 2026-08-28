from backend.app.change_detection import detect_research_changes


def _company_state(
    *,
    status: str = "存续",
    complete: bool = False,
    shareholders: list[tuple[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "structured-research-state-v1",
        "module_code": "company_base",
        "complete": complete,
        "total_records": len(shareholders or []),
        "fields": {
            "registration_status": status,
            "legal_representative": "示例法定代表人",
            "registered_capital": "1000 万元人民币",
        },
        "records": [
            {
                "key": name,
                "label": name,
                "fields": {"shareholding_ratio": ratio},
            }
            for name, ratio in shareholders or []
        ],
    }


def test_first_snapshot_only_establishes_baseline() -> None:
    material, archived = detect_research_changes(
        {},
        {"company_base": _company_state(shareholders=[("股东甲", "20%")])},
    )

    assert material == []
    assert archived == []


def test_partial_shareholder_pages_never_infer_an_exit() -> None:
    material, archived = detect_research_changes(
        {"company_base": _company_state(shareholders=[("股东甲", "20%"), ("股东乙", "10%")])},
        {"company_base": _company_state(shareholders=[("股东甲", "25%")])},
    )

    assert [change.change_type for change in material] == ["shareholding_ratio_changed"]
    assert material[0].before_value == "20%"
    assert material[0].after_value == "25%"
    assert archived == []


def test_complete_shareholder_pages_can_report_additions_and_removals() -> None:
    material, _ = detect_research_changes(
        {
            "company_base": _company_state(
                complete=True,
                shareholders=[("股东甲", "20%"), ("股东乙", "10%")],
            )
        },
        {
            "company_base": _company_state(
                complete=True,
                shareholders=[("股东甲", "20%"), ("股东丙", "10%")],
            )
        },
    )

    assert {change.change_type for change in material} == {
        "shareholder_added",
        "shareholder_removed",
    }


def test_low_value_count_change_is_archived_instead_of_published() -> None:
    previous = {
        "schema_version": "structured-research-state-v1",
        "module_code": "intellectual_property",
        "fields": {"software_copyrights": "10"},
        "records": [],
    }
    current = {
        **previous,
        "fields": {"software_copyrights": "11"},
    }

    material, archived = detect_research_changes(
        {"intellectual_property": previous},
        {"intellectual_property": current},
    )

    assert material == []
    assert len(archived) == 1
    assert archived[0].materiality_score == 35


def test_risk_count_change_is_a_non_publishable_lead_not_a_risk_conclusion() -> None:
    previous = {"records": [{"key": "judicial", "label": "司法记录", "fields": {"count": "1"}}]}
    current = {"records": [{"key": "judicial", "label": "司法记录", "fields": {"count": "2"}}]}

    material, _ = detect_research_changes(
        {"risk": previous},
        {"risk": current},
    )

    assert len(material) == 1
    assert material[0].publishable is False
    assert material[0].risk_severity == "none"
    assert "不构成风险责任" in material[0].uncertainties[0]
