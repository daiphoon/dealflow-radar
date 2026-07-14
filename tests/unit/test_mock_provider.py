from backend.app.providers import MockResearchProvider


def test_mock_provider_is_local_synthetic_and_zero_cost() -> None:
    provider = MockResearchProvider()
    records = provider.load()

    assert len(records) == 10
    assert provider.load_count == 1
    assert provider.external_calls == 0
    assert provider.estimated_cost == 0
    assert len({record.external_record_id for record in records}) == 10
    assert all(record.company_legal_name.startswith("示例") for record in records)
    assert all(record.canonical_url.startswith("https://example.invalid/") for record in records)
    assert all(record.requires_human_review for record in records)
