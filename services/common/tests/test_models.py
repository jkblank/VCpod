import pytest
from pydantic import ValidationError

from common.models import ExternalLibraryConfig


def test_external_library_flat_string_selections_unchanged():
    cfg = ExternalLibraryConfig(path="/library", selections=["Linkin Park", "The Cure"])
    assert cfg.selections == ["Linkin Park", "The Cure"]


def test_external_library_nested_mapping_selection_flattened():
    cfg = ExternalLibraryConfig(
        path="/library",
        selections=[
            "Alanis Morissette",
            {"Talking Heads": ["Performance", "Remixed", "The Collection"]},
        ],
    )
    assert cfg.selections == [
        "Alanis Morissette",
        "Talking Heads/Performance",
        "Talking Heads/Remixed",
        "Talking Heads/The Collection",
    ]


def test_external_library_nested_mapping_multiple_artists_in_one_entry():
    cfg = ExternalLibraryConfig(
        path="/library",
        selections=[{"A": ["X"], "B": ["Y", "Z"]}],
    )
    assert cfg.selections == ["A/X", "B/Y", "B/Z"]


def test_external_library_invalid_nested_selection_raises():
    with pytest.raises(ValidationError, match="invalid selections entry"):
        ExternalLibraryConfig(path="/library", selections=[{"Talking Heads": "Performance"}])


def test_external_library_invalid_selection_type_raises():
    with pytest.raises(ValidationError, match="invalid selections entry"):
        ExternalLibraryConfig(path="/library", selections=[123])
