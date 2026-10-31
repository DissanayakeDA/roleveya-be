from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints


def blank_to_none(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def optional_str(max_length: int):
    """A trimmed, length-limited string where blank input becomes None."""
    return Annotated[Annotated[str, StringConstraints(max_length=max_length)] | None, BeforeValidator(blank_to_none)]


def required_str(max_length: int):
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)]


Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
TagList = Annotated[list[Tag], Field(max_length=25)]
Currency = Annotated[
    Annotated[str, StringConstraints(to_upper=True, pattern=r"^[A-Za-z]{3}$")] | None, BeforeValidator(blank_to_none)
]


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MessageOut(BaseModel):
    message: str
