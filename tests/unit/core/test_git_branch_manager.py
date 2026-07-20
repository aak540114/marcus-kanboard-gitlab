"""
Unit tests for BranchManager's failure-path git hygiene.

All git subprocess calls are mocked via BranchManager._git — no real git
repository or subprocess is involved. These tests pin the behavior that a
FAILED multi-step git sequence must leave the shared working tree clean:
a conflicted `git merge` (or a conflicted `git pull` inside merge_to_main)
plants MERGE_HEAD in the repo, and without an explicit `git merge --abort`
every subsequent git operation for every other ticket fails with
"you have not concluded your merge".
"""

from unittest.mock import AsyncMock

import pytest

from src.core.git_branch_manager import BranchManager, BranchManagerConfig


def _mgr() -> BranchManager:
    """BranchManager with a throwaway repo path (never actually used)."""
    return BranchManager(BranchManagerConfig(repo_path="/tmp/fake-repo"))


def _calls(git_mock) -> list:
    """Return the list of git argv tuples issued via the mocked _git."""
    return [c.args for c in git_mock.call_args_list]


class TestMergeToMainAbortsOnFailure:
    """merge_to_main must clean up a failed merge, mirroring rebase_on_main."""

    @pytest.mark.asyncio
    async def test_failed_merge_runs_merge_abort(self):
        """A conflicted `git merge` is followed by `git merge --abort`."""
        mgr = _mgr()

        async def fake_git(*args):
            if args[0] == "merge" and "--abort" not in args:
                return (1, "", "CONFLICT (content): merge conflict in app.py")
            return (0, "", "")

        mgr._git = AsyncMock(side_effect=fake_git)

        ok = await mgr.merge_to_main("ticket/kanboard/7")

        assert ok is False
        assert ("merge", "--abort") in _calls(mgr._git)

    @pytest.mark.asyncio
    async def test_failed_pull_aborts_merge_state_and_fails(self):
        """A conflicted `git pull` (which also plants MERGE_HEAD) aborts and
        returns False instead of merging against a stale/conflicted main."""
        mgr = _mgr()

        async def fake_git(*args):
            if args[0] == "pull":
                return (1, "", "CONFLICT: Merge conflict in app.py")
            return (0, "", "")

        mgr._git = AsyncMock(side_effect=fake_git)

        ok = await mgr.merge_to_main("ticket/kanboard/7")

        assert ok is False
        assert ("merge", "--abort") in _calls(mgr._git)
        # The ticket merge itself must never have been attempted.
        assert not any(
            c[0] == "merge" and "ticket/kanboard/7" in c for c in _calls(mgr._git)
        )

    @pytest.mark.asyncio
    async def test_successful_merge_does_not_abort(self):
        """The happy path issues no merge --abort."""
        mgr = _mgr()
        mgr._git = AsyncMock(return_value=(0, "", ""))

        ok = await mgr.merge_to_main("ticket/kanboard/7", delete_after=False)

        assert ok is True
        assert ("merge", "--abort") not in _calls(mgr._git)


class TestMergeFetchesAgentBranch:
    """merge_to_main must merge the AGENT's pushed commits, not the stale
    local branch. With the self-clone design the agent's work lives on the
    remote branch; this clone's local ticket branch is empty."""

    @pytest.mark.asyncio
    async def test_fetches_branch_and_merges_fetch_head(self):
        """A successful fetch → merge FETCH_HEAD (the remote agent commits)."""
        mgr = _mgr()
        mgr._git = AsyncMock(return_value=(0, "", ""))

        ok = await mgr.merge_to_main("ticket/kanboard/3", delete_after=False)

        assert ok is True
        calls = _calls(mgr._git)
        # Fetched the ticket branch before merging.
        assert any(
            c[0] == "fetch" and "ticket/kanboard/3" in c for c in calls
        )
        # Merged the fetched remote tip, not the stale local branch.
        assert any(
            c[0] == "merge" and "FETCH_HEAD" in c for c in calls
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_local_branch_when_remote_absent(self):
        """If the remote branch can't be fetched, merge the local ref."""
        mgr = _mgr()

        async def fake_git(*args):
            if args[0] == "fetch" and args[-1] == "ticket/kanboard/3":
                return (1, "", "couldn't find remote ref")
            return (0, "", "")

        mgr._git = AsyncMock(side_effect=fake_git)

        ok = await mgr.merge_to_main("ticket/kanboard/3", delete_after=False)

        assert ok is True
        assert any(
            c[0] == "merge" and "ticket/kanboard/3" in c
            for c in _calls(mgr._git)
        )
