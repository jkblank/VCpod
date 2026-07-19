from __future__ import annotations

import sys
from pathlib import Path

from common.config import ConfigError, load_global_config, load_profile_config


def _print_error(path: Path, exc: ConfigError) -> None:
    print(f"ERROR {path}")
    for line in exc.errors:
        print(f"  {line}")


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    global_path = root / "config" / "global.yaml"
    profiles_dir = root / "config" / "profiles"

    ok = True

    try:
        load_global_config(global_path)
        print(f"OK    {global_path}")
    except ConfigError as e:
        ok = False
        _print_error(global_path, e)

    if not profiles_dir.is_dir():
        print(f"ERROR {profiles_dir}: directory not found")
        sys.exit(1)

    seen: dict[str, Path] = {}
    for path in sorted(profiles_dir.glob("*.yaml")):
        try:
            profile = load_profile_config(path)
            if profile.profile in seen:
                raise ConfigError(
                    path,
                    [
                        f"duplicate profile name '{profile.profile}' "
                        f"(already defined in {seen[profile.profile]})"
                    ],
                )
            seen[profile.profile] = path
            print(f"OK    {path}")
        except ConfigError as e:
            ok = False
            _print_error(path, e)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
