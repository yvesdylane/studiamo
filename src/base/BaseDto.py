"""Base class of every DTO.

DTO naming:  XBase (shared fields) | XCreate (input) | XUpdate (partial input)
             | XRead (output)      | XPublic (safe view for students)

Numeric(5,2) columns come back from the database as Decimal; DTOs expose
them as float.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class DTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
