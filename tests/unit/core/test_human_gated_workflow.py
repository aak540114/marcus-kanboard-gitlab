"""
Unit tests for HumanGatedWorkflow.

Covers the new trigger rules introduced in the human-gated AI workflow:
  - AI starts when a ticket is UNASSIGNED and status is ready or in_progress.
  - When a human assigns themselves, AI releases its claim and stands down.
  - When a ticket becomes unassigned while in a workable state, AI claims it.
  - Humans cannot push a card to waiting_for_human (AI-only state).
  - The claim gate prevents two Marcus instances from double-starting.
  - get_work_context includes the already_claimed_by field.

All external dependencies (kanban, branch manager, dev env, AC generator)
are mocked; no file I/O or network calls occur.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.events import Events
from src.core.ticket_lifecycle import (
    InvalidTransitionError,
    TicketLifecycleManager,
    TicketState,
)
from src.workflows.human_gated_workflow import HumanGatedWorkflow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(data: dict) -> Any:
    """Build a minimal event object with a .data attribute."""
    ev = MagicMock()
    ev.data = data
    return ev


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def state_file(tmp_path):
    """Temporary lifecycle state file."""
    return str(tmp_path / "lifecycle.json")


@pytest.fixture
def lifecycle(state_file):
    """Fresh lifecycle manager backed by a temp file."""
    return TicketLifecycleManager(state_file=state_file)


@pytest.fixture
def mock_kanban():
    """Mock KanbanInterface."""
    kb = MagicMock()
    kb.move_task_to_column = AsyncMock(return_value=True)
    kb.add_comment = AsyncMock(return_value=1)
    kb.get_task_by_id = AsyncMock(return_value=None)
    return kb


@pytest.fixture
def mock_branch():
    """Mock BranchManager."""
    bm = MagicMock()
    bm.create_branch = AsyncMock(return_value=True)
    bm.merge_to_main = AsyncMock(return_value=True)
    bm.rebase_on_main = AsyncMock(return_value=True)
    bm.get_branch_commits = AsyncMock(return_value=[])
    bm.config = MagicMock()
    bm.config.main_branch = "main"
    bm.make_branch_name = MagicMock(
        side_effect=lambda provider, tid: f"ticket/{provider}/{tid}"
    )
    return bm


@pytest.fixture
def mock_dev_env():
    """Mock DevEnvironmentManager."""
    de = MagicMock()
    de.stop = AsyncMock()
    de.stop_all = AsyncMock()
    de.start = AsyncMock()
    de.get_info = MagicMock(return_value=None)
    return de


@pytest.fixture
def mock_ac_gen():
    """Mock ACGenerator."""
    gen = MagicMock()
    gen.generate = AsyncMock(return_value="- [ ] Acceptance criterion 1")
    return gen


@pytest.fixture
def workflow(lifecycle, mock_kanban, mock_branch, mock_dev_env, mock_ac_gen):
    """HumanGatedWorkflow wired with mocked dependencies."""
    events = Events()
    wf = HumanGatedWorkflow(
        kanban=mock_kanban,
        events=events,
        provider_name="kanboard",
        lifecycle=lifecycle,
        branch_manager=mock_branch,
        dev_env_manager=mock_dev_env,
        ac_generator=mock_ac_gen,
    )
    # Patch BranchManager.make_branch_name at class level
    with patch(
        "src.workflows.human_gated_workflow.BranchManager.make_branch_name",
        side_effect=lambda provider, tid: f"ticket/{provider}/{tid}",
    ):
        yield wf


# ---------------------------------------------------------------------------
# Trigger: unassigned ticket moved to ready/in_progress → AI starts
# ---------------------------------------------------------------------------


class TestStatusChangedTrigger:
    """AI starts when status → ready/in_progress and ticket is unassigned."""

    @pytest.mark.asyncio
    async def test_ready_unassigned_triggers_ai(
        self, workflow, lifecycle, mock_kanban
    ):
        """Status change to ready with no assignee causes AI to claim and start."""
        lifecycle.get_or_create("42", "kanboard")
        event = _make_event(
            {"ticket_id": "42", "new_status": "ready", "old_status": "todo",
             "provider": "kanboard"}
        )
        with patch(
            "src.workflows.human_gated_workflow.BranchManager.make_branch_name",
            return_value="ticket/kanboard/42",
        ):
            await workflow._on_status_changed(event)

        rec = lifecycle.get("42", "kanboard")
        assert rec is not None
        assert rec.state == TicketState.IN_PROGRESS
        assert rec.ai_agent_id is not None
        mock_kanban.move_task_to_column.assert_called_with("42", "in progress")

    @pytest.mark.asyncio
    async def test_in_progress_unassigned_triggers_ai(
        self, workflow, lifecycle, mock_kanban
    ):
        """Status change to in_progress with no assignee causes AI to claim."""
        lifecycle.get_or_create("43", "kanboard")
        lifecycle.transition("43", "kanboard", TicketState.READY)
        event = _make_event(
            {"ticket_id": "43", "new_status": "in_progress",
             "old_status": "ready", "provider": "kanboard"}
        )
        with patch(
            "src.workflows.human_gated_workflow.BranchManager.make_branch_name",
            return_value="ticket/kanboard/43",
        ):
            await workflow._on_status_changed(event)

        rec = lifecycle.get("43", "kanboard")
        assert rec is not None
        assert rec.ai_agent_id is not None

    @pytest.mark.asyncio
    async def test_assigned_ticket_does_not_trigger_ai(
        self, workflow, lifecycle, mock_kanban
    ):
        """AI does NOT start if a human is assigned to the ticket."""
        lifecycle.get_or_create("44", "kanboard")
        lifecycle.set_assignee("44", "kanboard", "alice")
        event = _make_event(
            {"ticket_id": "44", "new_status": "ready", "old_status": "todo",
             "provider": "kanboard"}
        )
        await workflow._on_status_changed(event)

        rec = lifecycle.get("44", "kanboard")
        assert rec is not None
        assert rec.ai_agent_id is None  # claim not taken
        mock_kanban.move_task_to_column.assert_not_called()

    @pytest.mark.asyncio
    async def test_waiting_for_human_by_human_is_rejected(
        self, workflow, lifecycle
    ):
        """Human moving card to waiting_for_human is silently ignored."""
        lifecycle.get_or_create("45", "kanboard")
        lifecycle.transition("45", "kanboard", TicketState.READY)
        lifecycle.transition("45", "kanboard", TicketState.IN_PROGRESS)
        event = _make_event(
            {"ticket_id": "45", "new_status": "waiting_for_human",
             "old_status": "in_progress", "provider": "kanboard"}
        )
        await workflow._on_status_changed(event)

        rec = lifecycle.get("45", "kanboard")
        assert rec is not None
        # State must not change to WAITING_FOR_HUMAN
        assert rec.state == TicketState.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_todo_status_resets_lifecycle_state(self, workflow, lifecycle):
        """Human moving card to todo updates internal lifecycle state."""
        lifecycle.get_or_create("46", "kanboard")
        lifecycle.transition("46", "kanboard", TicketState.READY)
        event = _make_event(
            {"ticket_id": "46", "new_status": "todo",
             "old_status": "ready", "provider": "kanboard"}
        )
        await workflow._on_status_changed(event)

        rec = lifecycle.get("46", "kanboard")
        assert rec is not None
        assert rec.state == TicketState.TODO


# ---------------------------------------------------------------------------
# Trigger: ticket unassigned while workable → AI starts
# ---------------------------------------------------------------------------


class TestUnassignedTrigger:
    """When human removes their assignment and ticket is workable, AI starts."""

    @pytest.mark.asyncio
    async def test_unassign_while_ready_triggers_ai(
        self, workflow, lifecycle, mock_kanban
    ):
        """Unassigning a READY ticket lets AI claim and start work."""
        lifecycle.get_or_create("50", "kanboard")
        lifecycle.transition("50", "kanboard", TicketState.READY)
        lifecycle.set_assignee("50", "kanboard", "bob")

        event = _make_event({"ticket_id": "50", "provider": "kanboard"})
        with patch(
            "src.workflows.human_gated_workflow.BranchManager.make_branch_name",
            return_value="ticket/kanboard/50",
        ):
            await workflow._on_ticket_unassigned(event)

        rec = lifecycle.get("50", "kanboard")
        assert rec is not None
        assert rec.assignee in (None, "", "0")
        assert rec.ai_agent_id is not None
        assert rec.state == TicketState.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_unassign_while_todo_does_not_trigger_ai(
        self, workflow, lifecycle, mock_kanban
    ):
        """Unassigning a TODO ticket does not trigger AI work."""
        lifecycle.get_or_create("51", "kanboard")
        lifecycle.set_assignee("51", "kanboard", "carol")

        event = _make_event({"ticket_id": "51", "provider": "kanboard"})
        await workflow._on_ticket_unassigned(event)

        rec = lifecycle.get("51", "kanboard")
        assert rec is not None
        assert rec.ai_agent_id is None
        mock_kanban.move_task_to_column.assert_not_called()


# ---------------------------------------------------------------------------
# Trigger: human assigns ticket → AI releases claim
# ---------------------------------------------------------------------------


class TestAssignedTrigger:
    """When a human assigns a ticket, the AI claim is released."""

    @pytest.mark.asyncio
    async def test_assign_releases_existing_ai_claim(self, workflow, lifecycle):
        """Assigning to a human clears any AI claim on the ticket."""
        lifecycle.get_or_create("60", "kanboard")
        lifecycle.claim_ticket("60", "kanboard", "agent-x")

        event = _make_event(
            {"ticket_id": "60", "assignee": "dave", "provider": "kanboard"}
        )
        await workflow._on_ticket_assigned(event)

        rec = lifecycle.get("60", "kanboard")
        assert rec is not None
        assert rec.ai_agent_id is None
        assert rec.assignee == "dave"

    @pytest.mark.asyncio
    async def test_assign_stores_assignee(self, workflow, lifecycle):
        """_on_ticket_assigned records the human assignee name."""
        lifecycle.get_or_create("61", "kanboard")
        event = _make_event(
            {"ticket_id": "61", "assignee": "eve", "provider": "kanboard"}
        )
        await workflow._on_ticket_assigned(event)

        rec = lifecycle.get("61", "kanboard")
        assert rec is not None
        assert rec.assignee == "eve"


# ---------------------------------------------------------------------------
# Anti-duplication: second claim is rejected
# ---------------------------------------------------------------------------


class TestClaimGate:
    """Two concurrent Marcus instances cannot both claim the same ticket."""

    @pytest.mark.asyncio
    async def test_already_claimed_ticket_is_skipped(
        self, workflow, lifecycle, mock_kanban
    ):
        """If a ticket is already claimed, _start_ai_work exits early."""
        lifecycle.get_or_create("70", "kanboard")
        lifecycle.claim_ticket("70", "kanboard", "other-marcus")

        rec = lifecycle.get("70", "kanboard")
        assert rec is not None
        with patch(
            "src.workflows.human_gated_workflow.BranchManager.make_branch_name",
            return_value="ticket/kanboard/70",
        ):
            await workflow._start_ai_work("70", rec)

        # Branch must NOT have been created.
        mock_kanban.move_task_to_column.assert_not_called()
        # Claim still belongs to original holder.
        rec2 = lifecycle.get("70", "kanboard")
        assert rec2 is not None
        assert rec2.ai_agent_id == "other-marcus"

    @pytest.mark.asyncio
    async def test_branch_failure_releases_claim(
        self, workflow, lifecycle, mock_branch
    ):
        """If branch creation fails, the claim is released so retry is possible."""
        mock_branch.create_branch = AsyncMock(return_value=False)
        lifecycle.get_or_create("71", "kanboard")

        rec = lifecycle.get("71", "kanboard")
        assert rec is not None
        with patch(
            "src.workflows.human_gated_workflow.BranchManager.make_branch_name",
            return_value="ticket/kanboard/71",
        ):
            await workflow._start_ai_work("71", rec)

        rec2 = lifecycle.get("71", "kanboard")
        assert rec2 is not None
        assert rec2.ai_agent_id is None  # released after failure


# ---------------------------------------------------------------------------
# get_work_context: includes already_claimed_by
# ---------------------------------------------------------------------------


class TestGetWorkContext:
    """get_work_context exposes the current claimant."""

    @pytest.mark.asyncio
    async def test_unclaimed_ticket_has_none_claimed_by(
        self, workflow, lifecycle
    ):
        """already_claimed_by is None for unclaimed tickets."""
        lifecycle.get_or_create("80", "kanboard")
        ctx = await workflow.get_work_context("80")
        assert ctx is not None
        assert ctx["already_claimed_by"] is None

    @pytest.mark.asyncio
    async def test_claimed_ticket_exposes_agent_id(
        self, workflow, lifecycle
    ):
        """already_claimed_by shows the holding agent's identifier."""
        lifecycle.get_or_create("81", "kanboard")
        lifecycle.claim_ticket("81", "kanboard", "marcus-abc123")
        ctx = await workflow.get_work_context("81")
        assert ctx is not None
        assert ctx["already_claimed_by"] == "marcus-abc123"


# ---------------------------------------------------------------------------
# _is_unassigned helper
# ---------------------------------------------------------------------------


class TestIsUnassigned:
    """_is_unassigned returns True for None, empty string, and '0'."""

    def _make_record(self, assignee):
        """Build a minimal TicketRecord-like mock."""
        rec = MagicMock()
        rec.assignee = assignee
        return rec

    def test_none_assignee_is_unassigned(self, workflow):
        """assignee=None is treated as unassigned."""
        assert workflow._is_unassigned(self._make_record(None)) is True

    def test_empty_string_is_unassigned(self, workflow):
        """assignee='' is treated as unassigned."""
        assert workflow._is_unassigned(self._make_record("")) is True

    def test_kanboard_zero_is_unassigned(self, workflow):
        """Kanboard owner_id '0' sentinel is treated as unassigned."""
        assert workflow._is_unassigned(self._make_record("0")) is True

    def test_named_assignee_is_not_unassigned(self, workflow):
        """A real username is not treated as unassigned."""
        assert workflow._is_unassigned(self._make_record("alice")) is False
