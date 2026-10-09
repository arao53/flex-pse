"""Every released schema version keeps a fixture that must load forever."""

from pathlib import Path

import pytest

from flexcore.config.io import load_model_config
from flexcore.config.schema import CURRENT_SCHEMA_VERSION

_FIXTURES = sorted(
    (Path(__file__).parent.parent / "fixtures" / "configs").glob("*.json")
)


@pytest.mark.unit
@pytest.mark.parametrize("path", _FIXTURES, ids=lambda p: p.stem)
def test_every_migration_fixture_loads(path):
    """Each stored old-version config loads and ends at the current schema version."""
    cfg = load_model_config(path)

    assert cfg.schema_version == CURRENT_SCHEMA_VERSION
