from __future__ import annotations

from pathlib import Path, PurePosixPath

import yaml
from pydantic import ValidationError

from common.models import GlobalConfig, ProfileConfig


class ConfigError(Exception):
    """Raised for any config load/validation failure, with per-field messages."""

    def __init__(self, path: Path, errors: list[str]):
        self.path = path
        self.errors = errors
        super().__init__("\n".join(f"{path}: {e}" for e in errors))


def format_validation_error(path: Path, exc: ValidationError) -> ConfigError:
    messages = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        messages.append(f"{loc} — {err['msg']}")
    return ConfigError(path, messages)


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        raise ConfigError(path, ["file not found"])
    with path.open("r") as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(path, [f"invalid YAML: {e}"]) from e
    if data is None:
        raise ConfigError(path, ["file is empty"])
    return data


def load_global_config(path: Path | str) -> GlobalConfig:
    path = Path(path)
    data = _load_yaml(path)
    try:
        return GlobalConfig.model_validate(data)
    except ValidationError as e:
        raise format_validation_error(path, e) from e


def load_profile_config(path: Path | str) -> ProfileConfig:
    path = Path(path)
    data = _load_yaml(path)
    try:
        profile = ProfileConfig.model_validate(data)
    except ValidationError as e:
        raise format_validation_error(path, e) from e
    if profile.profile == "global":
        # state_root/global.sqlite is reserved for fetch-scheduler's
        # cross-profile maintenance tasks (dedup/cleanup/backup pruning —
        # see common.schedule/common.backups); a profile named "global"
        # would silently collide with it via resolve_roots.
        raise ConfigError(path, ["profile name 'global' is reserved, choose a different name"])
    return profile


def load_all_profiles(directory: Path | str) -> dict[str, ProfileConfig]:
    """Load every *.yaml file in directory, keyed by each profile's `profile` field.

    Fails fast on the first invalid or duplicate-named profile.
    """
    directory = Path(directory)
    profiles: dict[str, ProfileConfig] = {}
    seen_paths: dict[str, Path] = {}
    for path in sorted(directory.glob("*.yaml")):
        profile = load_profile_config(path)
        if profile.profile in profiles:
            raise ConfigError(
                path,
                [
                    f"duplicate profile name '{profile.profile}' "
                    f"(already defined in {seen_paths[profile.profile]})"
                ],
            )
        profiles[profile.profile] = profile
        seen_paths[profile.profile] = path
    return profiles


def _dump_yaml(model_data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        # sort_keys=False -- preserves the model's own field declaration
        # order (profile, device, fetch, playlists, podcasts, sync, ...),
        # matching the hand-authored shape of alice.yaml/bob.yaml rather
        # than an alphabetically-resorted one.
        yaml.safe_dump(model_data, f, sort_keys=False)


def save_profile_config(profile: ProfileConfig, path: Path | str) -> None:
    """Writes profile to path as YAML, through the exact same schema
    every loader reads -- load_profile_config(path) afterward always
    reproduces an identical model. Enforces the same two business rules
    load_profile_config/load_all_profiles enforce at read time, so an
    invalid write is caught here rather than surfacing later as a
    confusing failure the next time anything loads config/."""
    path = Path(path)
    if profile.profile == "global":
        raise ConfigError(path, ["profile name 'global' is reserved, choose a different name"])

    profiles_dir = path.parent
    if profiles_dir.is_dir():
        for existing_path in sorted(profiles_dir.glob("*.yaml")):
            if existing_path == path:
                continue  # overwriting this same profile in place is fine
            try:
                existing = load_profile_config(existing_path)
            except ConfigError:
                continue  # an already-broken sibling file isn't this write's problem
            if existing.profile == profile.profile:
                raise ConfigError(
                    path,
                    [
                        f"duplicate profile name '{profile.profile}' "
                        f"(already defined in {existing_path})"
                    ],
                )

    _dump_yaml(profile.model_dump(mode="json", exclude_none=True), path)


def save_global_config(config: GlobalConfig, path: Path | str) -> None:
    """Writes config to path as YAML, through the exact same schema
    load_global_config reads -- no business rules beyond the schema
    itself apply to global.yaml, unlike profiles."""
    _dump_yaml(config.model_dump(mode="json", exclude_none=True), Path(path))


def resolve_config_path(container_path: str, config_root: Path) -> Path:
    """global.yaml / profile YAML credential paths are always written as
    /config/... container paths, per the ./config:/config:ro mount in
    docker-compose.yml. Re-root them under config_root so the same YAML
    values work unmodified when run bare-metal. Falls back to the literal
    path if it isn't /config-rooted (e.g. someone already wrote a real host
    path there).

    Moved here from music_stack_cli.orchestrate (its original home) once
    web-gui-backend needed the exact same resolution for global-config
    source credential paths -- a second real caller, not just
    music-stack-cli's own fetch pipeline."""
    posix_path = PurePosixPath(container_path)
    try:
        return config_root / posix_path.relative_to("/config")
    except ValueError:
        return Path(container_path)


def resolve_profile_path(value: Path | str, config_root: Path | str) -> Path:
    """Resolve a CLI-supplied `--profile` value to a concrete YAML path.

    Accepts either a literal path (existing behavior everywhere this
    lands, preserved as-is) or a bare profile name (e.g. "john"), which
    is resolved against `config_root/profiles/{name}.yaml`. Raises
    ConfigError listing the profile names actually found under
    config_root/profiles/ when neither resolves, so a typo gets a
    helpful list instead of a raw file-not-found.
    """
    literal = Path(value)
    if literal.is_file():
        return literal

    profiles_dir = Path(config_root) / "profiles"
    by_name = profiles_dir / f"{value}.yaml"
    if by_name.is_file():
        return by_name

    available = sorted(p.stem for p in profiles_dir.glob("*.yaml")) if profiles_dir.is_dir() else []
    hint = f"available profiles: {', '.join(available)}" if available else f"no profiles found under {profiles_dir}"
    raise ConfigError(by_name, [f"no such file, and no profile named '{value}' — {hint}"])
