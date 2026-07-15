"""
Unit tests for src/marcus_mcp/tools/human_gated.py's MCP tool wrappers.

Focused on start_ticket_dev_environment, where an existing bug was found:
the tool used the CALLER-supplied `provider` argument to look up the
just-started environment, but HumanGatedWorkflow.start_dev_environment()
always registers it under the workflow's own configured provider
(self._provider) — a caller-supplied provider that doesn't match silently
returned port: None for an environment that actually started fine.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.marcus_mcp.tools.human_gated import (
    register_workflow,
    start_ticket_dev_environment,
)


@pytest.fixture(autouse=True)
def _reset_workflow_singleton():
    """Ensure each test starts with no workflow registered."""
    register_workflow(None)
    yield
    register_workflow(None)


@pytest.fixture
def mock_workflow():
    wf = MagicMock()
    wf._provider = "kanboard"
    wf.start_dev_environment = AsyncMock(return_value="http://localhost:9100")
    wf._dev_env = MagicMock()
    wf._dev_env.get_info = MagicMock(
        return_value=MagicMock(port=9100)
    )
    return wf


class TestStartTicketDevEnvironment:
    @pytest.mark.asyncio
    async def test_missing_ticket_id_returns_error(self):
        result = await start_ticket_dev_environment({"provider": "kanboard"})
        assert result["success"] is False
        assert "required" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_provider_returns_error(self):
        result = await start_ticket_dev_environment({"ticket_id": "42"})
        assert result["success"] is False
        assert "required" in result["error"]

    @pytest.mark.asyncio
    async def test_no_workflow_registered_returns_error(self):
        result = await start_ticket_dev_environment(
            {"ticket_id": "42", "provider": "kanboard"}
        )
        assert result["success"] is False
        assert "not initialised" in result["error"]

    @pytest.mark.asyncio
    async def test_dev_env_start_failure_returns_error(self, mock_workflow):
        mock_workflow.start_dev_environment = AsyncMock(return_value=None)
        register_workflow(mock_workflow)

        result = await start_ticket_dev_environment(
            {"ticket_id": "42", "provider": "kanboard"}
        )

        assert result["success"] is False
        assert "Failed to start" in result["error"]

    @pytest.mark.asyncio
    async def test_exception_is_caught_and_returned_as_failure(self, mock_workflow):
        mock_workflow.start_dev_environment = AsyncMock(
            side_effect=RuntimeError("docker unreachable")
        )
        register_workflow(mock_workflow)

        result = await start_ticket_dev_environment(
            {"ticket_id": "42", "provider": "kanboard"}
        )

        assert result["success"] is False
        assert "docker unreachable" in result["error"]

    @pytest.mark.asyncio
    async def test_success_uses_workflows_own_provider_for_lookup(self, mock_workflow):
        """The core regression case: the workflow's real provider is
        'kanboard', but the caller (mistakenly, or for another provider
        entirely) supplies a different `provider`. get_info must be
        looked up under the workflow's own provider, not the caller's,
        or the port would be silently reported as None."""
        register_workflow(mock_workflow)

        result = await start_ticket_dev_environment(
            {"ticket_id": "42", "provider": "jira"}
        )

        assert result["success"] is True
        mock_workflow._dev_env.get_info.assert_called_once_with("42", "kanboard")
        assert result["result"]["port"] == 9100
        assert result["result"]["provider"] == "kanboard"
        assert result["result"]["url"] == "http://localhost:9100"

    @pytest.mark.asyncio
    async def test_get_info_miss_still_reports_success_with_none_port(
        self, mock_workflow
    ):
        """A started environment Marcus can't find in its own bookkeeping
        still reports success (the URL is authoritative) with port: None,
        rather than failing outright."""
        mock_workflow._dev_env.get_info = MagicMock(return_value=None)
        register_workflow(mock_workflow)

        result = await start_ticket_dev_environment(
            {"ticket_id": "42", "provider": "kanboard"}
        )

        assert result["success"] is True
        assert result["result"]["port"] is None
