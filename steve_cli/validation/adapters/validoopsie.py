from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Tuple, Union

from steve_cli.validation.port import CheckFailure, ValidationPort, ValidationResult

logger = logging.getLogger(__name__)

SuiteEntry = Union[Tuple[str, Callable], Callable]


class ValidoopsieAdapter(ValidationPort):
    def __init__(self, suite: List[SuiteEntry] | None = None, **kwargs: Any):
        try:
            import validoopsie  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "validoopsie is required. Install it with: pip install validoopsie"
            ) from exc

        self._suite: List[Tuple[str, Callable]] = []
        for entry in (suite or []):
            if isinstance(entry, tuple):
                self._suite.append(entry)
            else:
                self._suite.append(("error", entry))

    def validate(self, dataframe: Any, context: Dict[str, Any] | None = None) -> ValidationResult:
        import validoopsie

        vd = validoopsie.Validate(dataframe)

        severity_by_check: Dict[str, str] = {}
        for severity, check_fn in self._suite:
            result = check_fn(vd)
            check_name = result.__class__.__name__ if result is not None else None
            if check_name:
                severity_by_check[check_name] = severity

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
                severity=severity_by_check.get(name, "error"),
            )
            for name in all_checks
            if name in failed_names
        ]

        for f in failures:
            msg = "%s [%s]: %s", f.check_name, f.column, f.message
            if f.severity == "error":
                logger.error(*msg)
            elif f.severity == "warn":
                logger.warning(*msg)
            else:
                logger.info(*msg)

        total = len(all_checks)
        failed = len(failures)
        result = ValidationResult(
            success=summary.get("passed", False),
            framework="validoopsie",
            checks_total=total,
            checks_passed=total - failed,
            checks_failed=failed,
            failures=failures,
            metadata={"engine": "polars"},
        )

        return result
