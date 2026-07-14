"""ProjectSyncWorkflow — syncs Kanboard projects to Gitea repositories.

Subscribes to the ``project.created`` event emitted by ``ProjectWatcher``
and reacts by:

1. Creating a corresponding Gitea repository (slugified from the project name).
2. Initialising it with a README and pushing the first commit.
3. Persisting the Kanboard-project → Gitea-repo mapping to
   ``./data/project_repos.json`` so that ``HumanGatedWorkflow`` can look
   up the correct git remote when creating ticket branches.

Usage
-----
::

    sync = ProjectSyncWorkflow(
        gitea_manager=gitea_mgr,
        events=events,
        repos_path="./data/project_repos.json",
        local_repos_base="./repos",
    )
    sync.subscribe()          # wire up the event subscription
    # ProjectWatcher is started separately

Mapping file format (``project_repos.json``)
--------------------------------------------
::

    {
      "kanboard:1": {
        "kanboard_project_id": 1,
        "kanboard_project_name": "Shopping Cart",
        "gitea_repo_url": "http://localhost:3000/root/shopping-cart.git",
        "local_repo_path": "./repos/shopping-cart"
      }
    }
"""

import json
import logging
import os
from typing import Any, Dict, Optional

from src.core.events import Events
from src.integrations.gitea_manager import GiteaManager, _slugify

logger = logging.getLogger(__name__)


class ProjectSyncWorkflow:
    """Sync Kanboard projects to Gitea repositories.

    Parameters
    ----------
    gitea_manager : GiteaManager
        Connected Gitea manager instance.
    events : Events
        Marcus event bus.
    repos_path : str
        Path to the project-repo mapping JSON file.
    local_repos_base : str
        Directory under which local git clones are created.
    """

    def __init__(
        self,
        gitea_manager: GiteaManager,
        events: Events,
        repos_path: str = "./data/project_repos.json",
        local_repos_base: str = "./repos",
        webhook_target_url: Optional[str] = None,
        webhook_secret: Optional[str] = None,
    ) -> None:
        """Initialise the workflow.

        Parameters
        ----------
        webhook_target_url : Optional[str]
            URL Gitea should POST push events to (Marcus's
            ``/webhooks/gitea`` endpoint). When set together with
            ``webhook_secret``, every repo provisioned by ``ensure_repo``
            automatically gets a push webhook — no manual setup required.
        webhook_secret : Optional[str]
            Shared HMAC secret for signing webhook deliveries.
        """
        self._gitea = gitea_manager
        self._events = events
        self._repos_path = repos_path
        self._local_repos_base = local_repos_base
        self._webhook_target_url = webhook_target_url
        self._webhook_secret = webhook_secret
        self._mapping: Dict[str, Dict[str, Any]] = self._load_mapping()

    # ------------------------------------------------------------------
    # Event wiring
    # ------------------------------------------------------------------

    def subscribe(self) -> None:
        """Subscribe to ``project.created`` events."""
        self._events.subscribe("project.created", self._on_project_created)
        logger.info("ProjectSyncWorkflow subscribed to project.created")

    # ------------------------------------------------------------------
    # Event handler
    # ------------------------------------------------------------------

    async def _on_project_created(self, event: Any) -> None:
        """Handle a new Kanboard project by creating a Gitea repo.

        Parameters
        ----------
        event : Event
            Marcus event with ``data.kanboard_project_id``,
            ``data.project_name``, ``data.project_description``.
        """
        data = event.data
        pid = int(data.get("kanboard_project_id", 0))
        name = data.get("project_name", f"project-{pid}")
        description = data.get("project_description", "")
        await self.ensure_repo(pid, name, description)

    # ------------------------------------------------------------------
    # On-demand provisioning
    # ------------------------------------------------------------------

    async def ensure_repo(
        self, project_id: int, project_name: str, description: str = ""
    ) -> Optional[Dict[str, Any]]:
        """Return (creating if necessary) the Gitea repo for a project.

        Idempotent — safe to call every time a ticket's project is looked
        up, not just from the ``project.created`` event handler (which
        nothing in Marcus currently publishes). If a mapping already
        exists it is returned as-is without any network calls.

        Parameters
        ----------
        project_id : int
            Kanboard project ID.
        project_name : str
            Human-readable project name (slugified for the Gitea repo).
        description : str
            Optional project description, used as the Gitea repo
            description on first creation.

        Returns
        -------
        Optional[Dict[str, Any]]
            The project's repo mapping, or None if repo creation failed.
        """
        key = f"kanboard:{project_id}"
        if key in self._mapping:
            logger.debug("Project %d already mapped — skipping", project_id)
            return dict(self._mapping[key])

        slug = _slugify(project_name)
        local_path = os.path.join(self._local_repos_base, slug)

        try:
            clone_url = await self._gitea.create_repo(project_name, description)
            await self._gitea.init_with_readme(clone_url, local_path)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to create Gitea repo for project %d (%s): %s",
                project_id,
                project_name,
                exc,
            )
            return None

        await self._ensure_webhook(slug)

        self._mapping[key] = {
            "kanboard_project_id": project_id,
            "kanboard_project_name": project_name,
            "gitea_repo_url": clone_url,
            "local_repo_path": local_path,
        }
        self._save_mapping()
        logger.info(
            "Project %d (%s) → Gitea %s (local: %s)",
            project_id,
            project_name,
            clone_url,
            local_path,
        )
        return dict(self._mapping[key])

    async def _ensure_webhook(self, slug: str) -> None:
        """Create the push webhook for a repo, if configured.

        Failures are logged and swallowed — a missing webhook degrades
        the dev-environment refresh from instant to never (until the next
        ``setup.sh`` run recreates it), but must not block repo creation
        or ticket work.

        Parameters
        ----------
        slug : str
            URL-safe repository name.
        """
        if not self._webhook_target_url or not self._webhook_secret:
            return
        try:
            await self._gitea.create_webhook(
                slug, self._webhook_target_url, self._webhook_secret
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to create Gitea webhook for %s: %s", slug, exc)

    # ------------------------------------------------------------------
    # Public query API
    # ------------------------------------------------------------------

    def get_repo_for_project(
        self, kanboard_project_id: int
    ) -> Optional[Dict[str, Any]]:
        """Return the repo mapping for a Kanboard project.

        Parameters
        ----------
        kanboard_project_id : int
            Kanboard project ID.

        Returns
        -------
        Optional[Dict[str, Any]]
            Dict with ``gitea_repo_url`` and ``local_repo_path``, or None.
        """
        return self._mapping.get(f"kanboard:{kanboard_project_id}")

    def all_mappings(self) -> Dict[str, Dict[str, Any]]:
        """Return all project → repo mappings.

        Returns
        -------
        Dict[str, Dict[str, Any]]
            Full mapping dict.
        """
        return {k: dict(v) for k, v in self._mapping.items()}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_mapping(self) -> Dict[str, Dict[str, Any]]:
        """Load the project-repo mapping from disk.

        Returns
        -------
        Dict[str, Dict[str, Any]]
            Persisted mapping (empty dict if file absent).
        """
        if not os.path.exists(self._repos_path):
            return {}
        try:
            with open(self._repos_path) as f:
                data: Dict[str, Dict[str, Any]] = json.load(f)
            return data
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load project repos mapping: %s", exc)
            return {}

    def _save_mapping(self) -> None:
        """Persist the project-repo mapping to disk."""
        os.makedirs(os.path.dirname(self._repos_path) or ".", exist_ok=True)
        try:
            with open(self._repos_path, "w") as f:
                json.dump(self._mapping, f, indent=2)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not save project repos mapping: %s", exc)
