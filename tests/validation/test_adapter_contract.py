from __future__ import annotations

import pytest
import polars as pl

from steve_cli.validation.port import DataQualityError, ValidationResult
from steve_cli.validation.adapters.validoopsie import ValidoopsieAdapter

try:
    import great_expectations  # noqa: F401
    import pandas as pd
    from steve_cli.validation.adapters.great_expectations import GreatExpectationsAdapter
    HAS_GE = True
except ImportError:
    HAS_GE = False

VALID_DF = pl.DataFrame({
    "customer_id": ["1", "2", "3"],
    "amount":      [10.0, 20.0, 30.0],
    "country":     ["DE", "US", "FR"],
})

INVALID_DF = pl.DataFrame({
    "customer_id": ["1", None, "3"],
    "amount":      [10.0, -1.0, 30.0],
    "country":     ["DE", "US", "FRX"],
})


def validoopsie_all_pass() -> ValidoopsieAdapter:
    return ValidoopsieAdapter(suite=[
        ("error", lambda vd: vd.NullValidation.ColumnNotBeNull("customer_id")),
        ("error", lambda vd: vd.ValuesValidation.ColumnValuesToBeBetween("amount", min_value=0.01)),
        ("error", lambda vd: vd.StringValidation.LengthToBeEqualTo("country", value=2)),
    ])


def validoopsie_with_failures() -> ValidoopsieAdapter:
    return ValidoopsieAdapter(suite=[
        ("error", lambda vd: vd.NullValidation.ColumnNotBeNull("customer_id")),
        ("warn",  lambda vd: vd.ValuesValidation.ColumnValuesToBeBetween("amount", min_value=0.01)),
        ("info",  lambda vd: vd.StringValidation.LengthToBeEqualTo("country", value=2)),
    ])


class TestValidoopsieAdapter:
    def test_all_pass(self):
        result = validoopsie_all_pass().validate(VALID_DF)
        assert isinstance(result, ValidationResult)
        assert result.success is True
        assert result.checks_failed == 0
        assert result.failures == []

    def test_result_fields_present(self):
        result = validoopsie_all_pass().validate(VALID_DF)
        assert result.framework == "validoopsie"
        assert result.checks_total > 0
        assert result.checks_passed == result.checks_total
        assert isinstance(result.assertions, list)
        assert len(result.assertions) == result.checks_total

    def test_assertions_all_true_on_valid(self):
        result = validoopsie_all_pass().validate(VALID_DF)
        assert all(a["success"] for a in result.assertions)

    def test_failure_has_column(self):
        result = validoopsie_with_failures().validate(INVALID_DF)
        assert result.checks_failed > 0
        for f in result.failures:
            assert f.column is not None

    def test_failure_severity_respected(self):
        result = validoopsie_with_failures().validate(INVALID_DF)
        severities = {f.column: f.severity for f in result.failures}
        if "customer_id" in severities:
            assert severities["customer_id"] == "error"
        if "amount" in severities:
            assert severities["amount"] == "warn"
        if "country" in severities:
            assert severities["country"] == "info"

    def test_raise_on_errors_raises_for_error_severity(self):
        result = validoopsie_with_failures().validate(INVALID_DF)
        with pytest.raises(DataQualityError) as exc_info:
            result.raise_on_errors()
        assert "customer_id" in str(exc_info.value)

    def test_raise_on_errors_no_raise_when_only_warn(self):
        adapter = ValidoopsieAdapter(suite=[
            ("warn", lambda vd: vd.ValuesValidation.ColumnValuesToBeBetween("amount", min_value=0.01)),
        ])
        result = adapter.validate(INVALID_DF)
        result.raise_on_errors()

    def test_assertions_count_matches_checks_total(self):
        result = validoopsie_with_failures().validate(INVALID_DF)
        assert len(result.assertions) == result.checks_total

    def test_assertions_include_passing_and_failing(self):
        result = validoopsie_with_failures().validate(INVALID_DF)
        successes = [a for a in result.assertions if a["success"]]
        failures  = [a for a in result.assertions if not a["success"]]
        assert len(successes) + len(failures) == result.checks_total


@pytest.mark.skipif(not HAS_GE, reason="great_expectations not installed")
class TestGreatExpectationsAdapter:
    def _valid_pd(self):
        return pd.DataFrame({
            "customer_id": ["1", "2", "3"],
            "amount":      [10.0, 20.0, 30.0],
            "country":     ["DE", "US", "FR"],
        })

    def _invalid_pd(self):
        return pd.DataFrame({
            "customer_id": ["1", None, "3"],
            "amount":      [10.0, -1.0, 30.0],
            "country":     ["DE", "US", "FRX"],
        })

    def _make_adapter(self, suite_name: str = "test") -> GreatExpectationsAdapter:
        import great_expectations as gx
        return GreatExpectationsAdapter(
            suite_name=suite_name,
            expectations=[
                gx.expectations.ExpectColumnValuesToNotBeNull(column="customer_id"),
                gx.expectations.ExpectColumnValuesToBeBetween(column="amount", min_value=0.01),
            ],
        )

    def test_all_pass(self):
        result = self._make_adapter().validate(self._valid_pd())
        assert isinstance(result, ValidationResult)
        assert result.success is True
        assert result.checks_failed == 0

    def test_result_fields_present(self):
        result = self._make_adapter().validate(self._valid_pd())
        assert result.framework == "great_expectations"
        assert result.checks_total > 0
        assert result.checks_passed == result.checks_total

    def test_failure_has_column(self):
        result = self._make_adapter("test2").validate(self._invalid_pd())
        assert result.checks_failed > 0
        for f in result.failures:
            assert f.column is not None

    def test_raise_on_errors_raises(self):
        result = self._make_adapter("test3").validate(self._invalid_pd())
        with pytest.raises(DataQualityError):
            result.raise_on_errors()
