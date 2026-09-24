import json
from types import SimpleNamespace

from backend.app.config import WebResearchPolicy
from backend.app.matter_contract import LABELS
from backend.app.research_extraction import extraction_payload
from backend.app.research_matters import TypedProposedMatters, validate_proposals


def test_production_schema_declares_types_and_prompt_limit():
    schema = TypedProposedMatters.model_json_schema()
    assert schema["properties"]["matters"]["maxItems"] == 4
    assert set(schema["$defs"]["TypedProposedMatter"]["properties"]["subtype"]["enum"]) == set(
        LABELS
    )


def test_unknown_type_has_distinct_reason():
    subject = SimpleNamespace(legal_name="虚构甲有限公司", aliases=(), legal_aliases=())
    body = "虚构甲有限公司完成A轮融资。"
    _, rejected = validate_proposals(
        subject,
        body,
        {
            "matters": [
                {
                    "subject": subject.legal_name,
                    "subtype": "invented",
                    "subject_role": "fundraiser",
                    "status": "reported",
                    "action_quote": body,
                }
            ]
        },
        legacy_compat=False,
    )
    assert rejected[0]["reason"] == "unknown_subtype"
    payload = extraction_payload(subject, body, WebResearchPolicy())
    data = json.loads(payload["messages"][1]["content"])
    assert set(data["matter_types"]) == set(LABELS)
