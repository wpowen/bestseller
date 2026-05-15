from bestseller.services.premium_state_ledger import validate_premium_state_ledger


def test_entry_events_pass_with_required_fields() -> None:
    report = validate_premium_state_ledger(
        {
            "entry_events": [
                {
                    "chapter_number": 3,
                    "entry_id": "artifact-core",
                    "event_type": "acquired",
                    "trigger": "试炼所得",
                    "cost_paid": "身份暴露",
                }
            ]
        }
    )

    assert report.passed is True


def test_entry_event_missing_entry_id_fails() -> None:
    report = validate_premium_state_ledger(
        {
            "entry_events": [
                {
                    "chapter_number": 3,
                    "event_type": "acquired",
                    "trigger": "试炼所得",
                }
            ]
        }
    )

    assert report.passed is False
    assert "entry_event_entry_id_missing" in {finding.code for finding in report.findings}


def test_entry_upgrade_without_trigger_fails() -> None:
    report = validate_premium_state_ledger(
        {
            "entry_events": [
                {
                    "chapter_number": 3,
                    "entry_id": "artifact-core",
                    "event_type": "upgraded",
                }
            ]
        }
    )

    assert report.passed is False
    assert "entry_event_trigger_missing" in {finding.code for finding in report.findings}


def test_entry_power_change_without_cost_warns() -> None:
    report = validate_premium_state_ledger(
        {
            "entry_events": [
                {
                    "chapter_number": 3,
                    "entry_id": "artifact-core",
                    "event_type": "used",
                    "trigger": "公开使用",
                }
            ]
        }
    )

    assert report.passed is True
    finding = report.findings[0]
    assert finding.code == "entry_event_cost_missing"
    assert finding.severity == "warning"
