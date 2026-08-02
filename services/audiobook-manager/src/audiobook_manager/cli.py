from __future__ import annotations

import argparse
import sys
from pathlib import Path

from audiobook_manager.beets_import import (
    BeetsImportError,
    import_audiobook,
    verify_audiobook_classification,
)
from audiobook_manager.merge import MergeError, find_ffprobe, merge_parts_to_m4b


def _state_paths(state_root: Path) -> tuple[Path, Path]:
    """(beets_db_path, beets_config_dir), both under state_root/audiobooks/."""
    audiobooks_state = state_root / "audiobooks"
    return audiobooks_state / "beets-library.db", audiobooks_state / "beets-config"


def _print_skip_error(source_dir: Path, library_root: Path, state_root: Path) -> None:
    print(
        "ERROR: beets-audible could not confidently match this book -- "
        "nothing was imported."
    )
    print(f"  Merged file left at: {source_dir}")
    print("  Add a metadata.yml next to it (see beets-audible docs) and retry with:")
    print(
        f'    audiobook-manager tag --source-dir "{source_dir}" '
        f'--library-root "{library_root}" --state-root "{state_root}"'
    )


def _cmd_merge(args: argparse.Namespace) -> int:
    try:
        output = merge_parts_to_m4b(args.parts_dir, args.output, bitrate=args.bitrate)
    except MergeError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"Merged into {output}")
    return 0


def _cmd_tag(args: argparse.Namespace) -> int:
    source_dir = Path(args.source_dir).resolve()
    library_root = Path(args.library_root).resolve()
    state_root = Path(args.state_root).resolve()
    beets_db_path, beets_config_dir = _state_paths(state_root)

    try:
        result = import_audiobook(
            source_dir,
            audiobooks_root=library_root,
            beets_db_path=beets_db_path,
            beets_config_dir=beets_config_dir,
        )
    except BeetsImportError as exc:
        print(f"ERROR: {exc}")
        return 1

    if not result.imported:
        _print_skip_error(source_dir, library_root, state_root)
        return 1

    print(f"Imported {len(result.imported_paths)} file(s):")
    ffprobe_path = find_ffprobe()
    for path in result.imported_paths:
        print(f"  {path}")
        if path.suffix.lower() == ".m4b":
            for warning in verify_audiobook_classification(path, ffprobe_path):
                print(f"    WARNING: {warning}")
    return 0


def _cmd_import_audiobook(args: argparse.Namespace) -> int:
    parts_dir = Path(args.parts_dir).resolve()
    library_root = Path(args.library_root).resolve()
    state_root = Path(args.state_root).resolve()
    beets_db_path, beets_config_dir = _state_paths(state_root)

    staging_dir = state_root / "audiobooks" / "staging" / parts_dir.name
    staging_dir.mkdir(parents=True, exist_ok=True)
    merged_path = staging_dir / "merged.m4b"

    try:
        merge_parts_to_m4b(parts_dir, merged_path, bitrate=args.bitrate)
    except MergeError as exc:
        print(f"ERROR: {exc}")
        return 1

    try:
        result = import_audiobook(
            staging_dir,
            audiobooks_root=library_root,
            beets_db_path=beets_db_path,
            beets_config_dir=beets_config_dir,
        )
    except BeetsImportError as exc:
        print(f"ERROR: {exc}")
        return 1

    if not result.imported:
        _print_skip_error(staging_dir, library_root, state_root)
        return 1

    print(f"Imported {len(result.imported_paths)} file(s):")
    ffprobe_path = find_ffprobe()
    for path in result.imported_paths:
        print(f"  {path}")
        if path.suffix.lower() == ".m4b":
            for warning in verify_audiobook_classification(path, ffprobe_path):
                print(f"    WARNING: {warning}")

    try:
        staging_dir.rmdir()
    except OSError:
        pass  # not empty -- beets didn't move everything out, leave for inspection

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="audiobook-manager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    merge_parser = subparsers.add_parser(
        "merge", help="Merge a directory of sequential MP3 parts into one chaptered .m4b"
    )
    merge_parser.add_argument("--parts-dir", required=True)
    merge_parser.add_argument("--output", required=True)
    merge_parser.add_argument(
        "--bitrate",
        default=None,
        help="Force a flat lossy AAC bitrate (e.g. 64k), overriding the "
        "default source-matching/lossless-cutover policy (see "
        "merge.select_encoding)",
    )
    merge_parser.set_defaults(func=_cmd_merge)

    tag_parser = subparsers.add_parser(
        "tag", help="Tag an already-merged audiobook file via beets-audible"
    )
    tag_parser.add_argument(
        "--source-dir", required=True, help="Directory containing one merged audio file"
    )
    tag_parser.add_argument("--library-root", required=True)
    tag_parser.add_argument("--state-root", required=True)
    tag_parser.set_defaults(func=_cmd_tag)

    import_parser = subparsers.add_parser(
        "import-audiobook",
        help="Merge a directory of MP3 parts and tag the result via beets-audible, in one step",
    )
    import_parser.add_argument("--parts-dir", required=True)
    import_parser.add_argument("--library-root", required=True)
    import_parser.add_argument("--state-root", required=True)
    import_parser.add_argument(
        "--bitrate",
        default=None,
        help="Force a flat lossy AAC bitrate (e.g. 64k), overriding the "
        "default source-matching/lossless-cutover policy (see "
        "merge.select_encoding)",
    )
    import_parser.set_defaults(func=_cmd_import_audiobook)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
