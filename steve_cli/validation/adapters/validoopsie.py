from __future__ import annotations

from typing import Any, Callable, Dict, List

from steve_cli.validation.port import CheckFailure, ValidationPort, ValidationResult


class ValidoopsieAdapter(ValidationPort):
    def __init__(self, suite: List[Callable] | None = None, **kwargs: Any):
        try:
            import validoopsie  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "validoopsie is required. Install it with: pip install validoopsie"
            ) from exc

        self._suite = suite or []

    def validate(self, dataframe: Any, context: Dict[str, Any] | None = None) -> ValidationResult:
        import validoopsie

        vd = validoopsie.Validate(dataframe)

        for check_fn in self._suite:
            check_fn(vd)

        vd.validate()

        summary = vd.summary
        results = vd.results

        all_checks = [k for k in results if k != "Summary"]
        failed_names = set(summary.get("failed_validation", []))

        failures = [
            CheckFailure(
                check_name=name,
                message=results[name]["result"].get("message", ""),
                column=results[name].get("column"),
            )
            for name in all_checks
            if name in failed_names
        ]

        total = len(all_checks)
        failed = len(failures)

        return ValidationResult(
            success=summary.get("passed", False),
            framework="validoopsie",
            checks_total=total,
            checks_passed=total - failed,
            checks_failed=failed,
            failures=failures,
            metadata={"engine": "polars"},
        )
