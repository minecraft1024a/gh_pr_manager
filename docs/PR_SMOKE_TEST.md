# PR Workflow Smoke Test

This small document provides a reviewable diff for testing the `gh_pr_manager` plugin.

Use this pull request to check `/gh list` and `/gh view`. Test `/gh approve` with a separate account that has write access; GitHub does not allow approving your own pull request. Only test `/gh merge` when you intentionally want to exercise the repository's merge rules. Close this pull request with `/gh close` when finished.

This fixture is intentionally documentation-only and is safe to discard after the smoke test.
