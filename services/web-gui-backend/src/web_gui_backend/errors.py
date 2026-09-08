"""Shared error-response helpers every router uses -- kept in one place
so every route surfaces ConfigError/ValidationError failures in the
exact same {"path": ..., "errors": [...]} shape, regardless of which
router raised it."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from common.config import ConfigError, format_validation_error


def config_error_response(exc: ConfigError) -> HTTPException:
    return HTTPException(status_code=422, detail={"path": str(exc.path), "errors": exc.errors})


def validate_or_422(model_cls: type[BaseModel], body: dict, path: Path) -> BaseModel:
    try:
        return model_cls.model_validate(body)
    except ValidationError as e:
        raise config_error_response(format_validation_error(path, e)) from e
