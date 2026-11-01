"""An explicit, test-backed issue selected by the developer."""

from pathlib import PurePosixPath
import re

from pydantic import BaseModel, Field, field_validator, model_validator


_NODE_ID = re.compile(r"^[A-Za-z0-9_./-]+(?:::[A-Za-z0-9_./\[\],-]+)+$")


class IssueContract(BaseModel):
    description: str = Field(min_length=1, max_length=4000)
    target_test: str = Field(default="", max_length=500)
    expected_behavior: str = Field(default="", max_length=2000)
    target_file: str = Field(default="", max_length=500)

    @field_validator("description", "target_test", "expected_behavior", "target_file", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("target_test")
    @classmethod
    def validate_target_test(cls, value: str) -> str:
        if not value:
            return value
        path = value.split("::", 1)[0]
        parts = PurePosixPath(path).parts
        if (
            not _NODE_ID.fullmatch(value)
            or not path.endswith(".py")
            or path.startswith("/")
            or any(part.startswith("-") for part in parts)
            or any(part in (".", "..") for part in parts)
        ):
            raise ValueError("target_test must be a relative pytest node ID such as tests/test_api.py::test_case")
        return value

    @field_validator("target_file")
    @classmethod
    def validate_target_file(cls, value: str) -> str:
        if not value:
            return value
        parts = PurePosixPath(value).parts
        if (
            not re.fullmatch(r"[A-Za-z0-9_./-]+\.py", value)
            or value.startswith("/")
            or any(part.startswith("-") for part in parts)
            or any(part in (".", "..") for part in parts)
        ):
            raise ValueError("target_file must be a relative Python source path")
        return value


class IssueFields(BaseModel):
    """An issue needs an existing test or an expectation to generate one."""

    issue_hint: str | None = None
    target_test: str | None = None
    expected_behavior: str | None = None
    target_file: str | None = None

    @model_validator(mode="after")
    def validate_contract(self) -> "IssueFields":
        self.issue_hint = self.issue_hint.strip() if self.issue_hint else None
        self.target_test = self.target_test.strip() if self.target_test else None
        self.expected_behavior = self.expected_behavior.strip() if self.expected_behavior else None
        self.target_file = self.target_file.strip() if self.target_file else None
        if any((self.issue_hint, self.target_test, self.expected_behavior, self.target_file)):
            if not self.issue_hint or not (self.target_test or self.expected_behavior):
                raise ValueError("Targeted repair requires issue_hint and either target_test or expected_behavior")
            contract = IssueContract(
                description=self.issue_hint,
                target_test=self.target_test or "",
                expected_behavior=self.expected_behavior or "",
                target_file=self.target_file or "",
            )
            self.target_test = contract.target_test
        return self

    def issue_contract(self) -> dict | None:
        if not self.issue_hint:
            return None
        return IssueContract(
            description=self.issue_hint,
            target_test=self.target_test or "",
            expected_behavior=self.expected_behavior or "",
            target_file=self.target_file or "",
        ).model_dump(mode="json")
