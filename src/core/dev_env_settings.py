"""
Global setting for the maximum number of dev-environment Docker containers
that may run in parallel.

Marcus's per-ticket dev-environment feature (see ``src/core/dev_environment.py``)
spawns a sibling Docker container on the host every time a human clicks
"Open Dev Environment" on a ticket. Without a cap, many simultaneous
clicks could exhaust host resources. A human sets this limit once, from
the Kanboard board header (see ``kanboard/plugins/MarcusDevEnv``); once
the limit is reached, starting a dev environment for a *new* ticket fails
until an existing one is stopped.

Unlike per-project settings (:mod:`src.core.gate_settings`), this is a
single global value — dev-environment containers are a host-wide resource
constraint, not something that makes sense to scope per project.

Settings are persisted as a JSON file at::

    <data_dir>/dev_env_settings.json

Schema::

    {"max_parallel_containers": 3}

Absence of the key (or a missing file) means "unlimited" — the historical
default before this setting existed.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = Path(os.getcwd()) / "data"


class DevEnvSettingsManager:
    """Reads and writes the global max-parallel-dev-environments setting.

    Parameters
    ----------
    data_dir : Optional[Path]
        Directory that contains ``dev_env_settings.json``. Defaults to
        ``./data/`` relative to the Marcus working directory.
    """

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        """Initialise the manager, loading any persisted setting."""
        self._path = (data_dir or _DEFAULT_DATA_DIR) / "dev_env_settings.json"
        self._data: Dict[str, Any] = self._load()

    def get_max_parallel_containers(self) -> Optional[int]:
        """Return the configured limit, or ``None`` if unset (unlimited).

        Returns
        -------
        Optional[int]
            Non-negative integer limit, or ``None`` when no limit has been
            configured (dev environments are unrestricted).
        """
        val = self._data.get("max_parallel_containers")
        if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
            return val
        return None

    def set_max_parallel_containers(self, count: int) -> None:
        """Persist the maximum number of concurrent dev-environment containers.

        Parameters
        ----------
        count : int
            Non-negative limit. ``0`` disables dev environments entirely.

        Raises
        ------
        ValueError
            If ``count`` is negative.
        """
        if count < 0:
            raise ValueError(f"max_parallel_containers must be >= 0, got {count}")
        self._data["max_parallel_containers"] = count
        self._save()
        logger.info("Set max_parallel_containers to %d", count)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> Dict[str, Any]:
        """Load settings from disk; return an empty structure on missing file."""
        if not self._path.exists():
            return {}
        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read dev_env_settings.json: %s", exc)
            return {}

    def _save(self) -> None:
        """Write settings to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._path, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
        except OSError as exc:
            logger.error("Could not write dev_env_settings.json: %s", exc)
