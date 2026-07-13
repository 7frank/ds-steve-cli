from __future__ import annotations

from typing import Any, Dict, List

from steve_cli.validation.port import CheckFailure, ValidationPort, ValidationResult


class GreatExpectationsAdapter(ValidationPort):
    def __init__(self, expectations: List[Any], suite_name: str = "default", **kwargs: Any):
        try:
            import great_expectations as gx  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "great_expectations is required. Install it with: pip install great-expectations"
            ) from exc

        self._expectations = expectations
        self._suite_name = suite_name

    def validate(self, dataframe: Any, context: Dict[str, Any] | None = None) -> ValidationResult:
        import great_expectations as gx
        import pandas as pd

        if not isinstance(dataframe, pd.DataFrame):
            try:
                dataframe = dataframe.to_pandas()
            except AttributeError:
                raise TypeError("GreatExpectationsAdapter requires a pandas or polars DataFrame")

        ctx = gx.get_context(mode="ephemeral")
        suite = ctx.suites.add(gx.ExpectationSuite(
            name=self._suite_name,
            expectations=self._expectations,
        ))
        ds = ctx.data_sources.add_pandas(f"_ds_{self._suite_name}")
        da = ds.add_dataframe_asset("_asset")
        batch_def = da.add_batch_definition_whole_dataframe("_batch")
        vd = ctx.validation_definitions.add(gx.ValidationDefinition(
            name=f"_vd_{self._suite_name}",
            data=batch_def,
            suite=suite,
        ))

        result = vd.run(batch_parameters={"dataframe": dataframe})

        total = len(result.results)
        failed_results = [r for r in result.results if not r.success]
        passed = total - len(failed_results)

        failures = [
            CheckFailure(
                check_name=r.expectation_config.type,
                message=str(r.result) if r.result else "",
                column=r.expectation_config.kwargs.get("column"),
            )
            for r in failed_results
        ]

        assertions = [
            {
                "check": r.expectation_config.type,
                "column": r.expectation_config.kwargs.get("column"),
                "success": r.success,
            }
            for r in result.results
        ]

        return ValidationResult(
            success=result.success,
            framework="great_expectations",
            checks_total=total,
            checks_passed=passed,
            checks_failed=len(failed_results),
            failures=failures,
            assertions=assertions,
            metadata={"engine": "pandas", "suite": self._suite_name},
        )
