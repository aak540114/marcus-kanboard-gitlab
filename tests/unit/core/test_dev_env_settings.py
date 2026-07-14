"""
Unit tests for src/core/dev_env_settings.py
"""

import json
from pathlib import Path

import pytest

from src.core.dev_env_settings import DevEnvSettingsManager


class TestDevEnvSettingsManager:
    """Tests for DevEnvSettingsManager — global max-parallel-containers setting."""

    @pytest.fixture()
    def mgr(self, tmp_path: Path) -> DevEnvSettingsManager:
        """Manager backed by a temp directory."""
        return DevEnvSettingsManager(data_dir=tmp_path)

    def test_get_max_parallel_containers_returns_none_when_not_set(self, mgr):
        """No setting stored yet → unlimited (None)."""
        assert mgr.get_max_parallel_containers() is None

    def test_set_and_get_round_trips(self, mgr):
        """set_max_parallel_containers then get round-trips the value."""
        mgr.set_max_parallel_containers(3)
        assert mgr.get_max_parallel_containers() == 3

    def test_zero_is_a_valid_explicit_value(self, mgr):
        """0 (no dev environments allowed) is distinct from 'unset'."""
        mgr.set_max_parallel_containers(0)
        assert mgr.get_max_parallel_containers() == 0

    def test_negative_value_raises(self, mgr):
        """Negative counts are rejected."""
        with pytest.raises(ValueError):
            mgr.set_max_parallel_containers(-1)

    def test_persisted_to_disk(self, tmp_path):
        """Setting survives a new manager instance backed by the same dir."""
        mgr1 = DevEnvSettingsManager(data_dir=tmp_path)
        mgr1.set_max_parallel_containers(5)

        mgr2 = DevEnvSettingsManager(data_dir=tmp_path)
        assert mgr2.get_max_parallel_containers() == 5

    def test_writes_valid_json(self, tmp_path):
        """The persisted file is valid JSON with the expected shape."""
        mgr = DevEnvSettingsManager(data_dir=tmp_path)
        mgr.set_max_parallel_containers(2)

        raw = json.loads((tmp_path / "dev_env_settings.json").read_text())
        assert raw["max_parallel_containers"] == 2

    def test_missing_file_returns_default(self, tmp_path):
        """No file on disk yet → defaults, no crash."""
        mgr = DevEnvSettingsManager(data_dir=tmp_path)
        assert mgr.get_max_parallel_containers() is None

    def test_corrupt_file_returns_default(self, tmp_path):
        """Unparseable JSON on disk → defaults, no crash."""
        (tmp_path / "dev_env_settings.json").write_text("NOT JSON {{{")
        mgr = DevEnvSettingsManager(data_dir=tmp_path)
        assert mgr.get_max_parallel_containers() is None

    def test_non_dict_json_returns_default(self, tmp_path):
        """A JSON file that isn't an object → defaults, no crash."""
        (tmp_path / "dev_env_settings.json").write_text("[1, 2, 3]")
        mgr = DevEnvSettingsManager(data_dir=tmp_path)
        assert mgr.get_max_parallel_containers() is None
