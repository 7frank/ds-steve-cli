from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class CheckFailure:
    check_name: str
    message: str
    column: str | None = None
    severity: str = "error"


@dataclass
class ValidationResult:
    success: bool
    framework: str
    checks_total: int = 0
    checks_passed: int = 0
    checks_failed: int = 0
    failures: List[CheckFailure] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return any(f.severity == "error" for f in self.failures)

    def raise_on_errors(self) -> ValidationResult:
        if self.has_errors:
            n = sum(1 for f in self.failures if f.severity == "error")
            raise ValueError(
                f"Validation failed: {n} error-level check(s) failed "
                f"({self.checks_failed}/{self.checks_total} total)"
            )
        return self

    def to_openlineage_facet(self) -> Dict[str, Any]:
        return {
            "dataQualityFacet": {
                "_producer": "steve-cli",
                "framework": self.framework,
                "success": self.success,
                "checks": {
                    "total": self.checks_total,
                    "passed": self.checks_passed,
                    "failed": self.checks_failed,
                },
                "failures": [
                    {"check": f.check_name, "message": f.message, "column": f.column, "severity": f.severity}
                    for f in self.failures
                ],
            },
            "dataQualityMetrics": {
                "rowCount": self.checks_total,
                "columnMetrics": {
                    f.column: {"nullCount": 1}
                    for f in self.failures
                    if f.column
                },
            },
        }


class ValidationPort(ABC):
    @abstractmethod
    def validate(self, dataframe: Any, context: Dict[str, Any] | None = None) -> ValidationResult:
        ...
