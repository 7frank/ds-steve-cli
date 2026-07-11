from __future__ import annotations

from typing import Any, Dict

from steve_cli.validation.port import CheckFailure, ValidationPort, ValidationResult


class GreatExpectationsAdapter(ValidationPort):
    def __init__(self, suite_name: str = "default", expectation_suite: Any = None, **kwargs: Any):
        try:
            import great_expectations as gx  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "great_expectations is required. Install it with: pip install great-expectations"
            ) from exc

        self._suite_name = suite_name
        self._expectation_suite = expectation_suite

    def validate(self, dataframe: Any, context: Dict[str, Any] | None = None) -> ValidationResult:
        import great_expectations as gx
        from great_expectations.dataset import PandasDataset

        if not isinstance(dataframe, PandasDataset):
            ge_df = gx.from_pandas(dataframe, expectation_suite=self._expectation_suite)
        else:
            ge_df = dataframe

        result = ge_df.validate(expectation_suite=self._expectation_suite, result_format="SUMMARY")

        total = len(result.results)
        failed_results = [r for r in result.results if not r.success]
        passed = total - len(failed_results)

        failures = [
            CheckFailure(
                check_name=r.expectation_config.expectation_type,
                message=str(r.result) if r.result else "",
                column=r.expectation_config.kwargs.get("column"),
            )
            for r in failed_results
        ]

        return ValidationResult(
            success=result.success,
            framework="great_expectations",
            checks_total=total,
            checks_passed=passed,
            checks_failed=len(failed_results),
            failures=failures,
            metadata={"engine": "pandas", "suite": self._suite_name},
        )
