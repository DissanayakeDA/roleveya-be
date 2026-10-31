from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, model_validator

from app.models import EmploymentType, WorkMode
from app.schemas.common import Currency, OrmModel, TagList, blank_to_none, optional_str

ExperienceLevel = Literal["entry", "junior", "mid", "senior", "lead", "principal", "executive"]
Salary = Annotated[int | None, Field(ge=0, le=100_000_000)]


class PreferencesIn(BaseModel):
    desired_titles: TagList = []
    preferred_locations: TagList = []
    work_modes: list[WorkMode] = []
    employment_types: list[EmploymentType] = []
    industries: TagList = []
    key_skills: TagList = []
    experience_level: Annotated[ExperienceLevel | None, BeforeValidator(blank_to_none)] = None
    salary_min: Salary = None
    salary_max: Salary = None
    salary_currency: Currency = None
    open_to_relocation: bool = False
    notes: optional_str(2_000) = None

    @model_validator(mode="after")
    def _clean(self):
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError("Minimum salary can't be higher than maximum salary.")
        # De-duplicate tag lists case-insensitively while keeping the user's order.
        for name in ("desired_titles", "preferred_locations", "industries", "key_skills"):
            seen: set[str] = set()
            unique = [v for v in getattr(self, name) if not (v.lower() in seen or seen.add(v.lower()))]
            setattr(self, name, unique)
        self.work_modes = list(dict.fromkeys(self.work_modes))
        self.employment_types = list(dict.fromkeys(self.employment_types))
        return self


class PreferencesOut(OrmModel, PreferencesIn):
    updated_at: datetime | None = None
