from __future__ import annotations

from typing import Any, Dict

from steve_cli.validation.port import ValidationPort, ValidationResult


class NullValidationAdapter(ValidationPort):
    def validate(self, dataframe: Any, context: Dict[str, Any] | None = None) -> ValidationResult:
        return ValidationResult(
            success=True,
            framework="null",
            checks_total=0,
            checks_passed=0,
            checks_failed=0,
        )
