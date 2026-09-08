from __future__ import annotations

from fastapi import APIRouter, Request

from common.config import ConfigError, load_global_config, save_global_config
from common.models import GlobalConfig

from web_gui_backend.errors import config_error_response, validate_or_422

router = APIRouter()


@router.get("/api/global-config")
def get_global_config(request: Request) -> dict:
    path = request.app.state.config_root / "global.yaml"
    try:
        config = load_global_config(path)
    except ConfigError as e:
        raise config_error_response(e) from e
    return config.model_dump(mode="json")


@router.put("/api/global-config")
def put_global_config(body: dict, request: Request) -> dict:
    path = request.app.state.config_root / "global.yaml"
    config = validate_or_422(GlobalConfig, body, path)
    save_global_config(config, path)
    return config.model_dump(mode="json")
