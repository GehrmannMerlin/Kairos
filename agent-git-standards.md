# Kairos Agent Git Standards

## Repository and remotes

- The canonical repository is `https://github.com/GehrmannMerlin/Kairos.git`; use it as `origin`.
- Before pushing, fetching for a cleanup, creating a tag, or opening a PR, verify the remote URL and current branch.
- Keep `main` as the only long-lived branch. Work on short-lived branches named for one focused change.
- Never force-push or rewrite shared `main` history.

## Commits and pull requests

- Use small commits with an English Conventional Commits subject and a Chinese body that explains the change and reason.
- Merge changes through a pull request. The PR must describe its scope and include relevant verification evidence and material risks.
- Choose a merge method that preserves useful history and does not mix incompatible runtime architectures. Do not merge an old runtime branch just to make its ancestry appear connected to `main`.
- Do not claim a change is verified when required checks have failed or have not run.

## Remote branch retention

Merged feature branches are short-lived references. A remote branch may be deleted only after all of these checks pass:

1. GitHub confirms that the branch's pull request is merged.
2. The current remote branch tip exactly matches the head SHA recorded for that merged pull request. A different tip means there may be post-merge work; preserve the branch and review it.
3. There is no open pull request using the branch, and no known active work depends on it.
4. The change is represented by the intended canonical product line on `main`. For architecture transitions, verify the application tree and the relevant PR evidence; ancestry alone is not required when the change was squash-merged, rebased, or deliberately reconciled.
5. The branch is not `main`, a release/tag reference, an active concurrent branch, or an explicit preservation target.

Do not use `git branch --merged` or commit ancestry as the sole proof that a remote branch is safe to remove. When any check is uncertain, keep the branch and investigate. Once repository maintainers have enabled GitHub's delete-branch-on-merge setting, GitHub may remove a branch after its PR is merged; the same safety conditions still apply before merging and for branches that predate the setting.

## Local branch cleanup

- Remote branch cleanup and local working-tree cleanup are separate operations.
- Before removing a local branch, verify that it has no unique commits, is not checked out in a worktree, and has no uncommitted work attached to it.
- Never discard uncommitted changes with reset, clean, or stash as part of routine branch cleanup. Preserve active work and resolve its ownership explicitly.

## History-preservation exceptions

- Keep release tags and explicitly named archive tags. Do not delete tags during branch cleanup.
- A canonical-history reconciliation may retain an old line as a parent while keeping the canonical application's tree. Review the reconciliation PR and its tree-identity evidence before retiring superseded branches.
- Do not create long-lived archive branches when an immutable tag and retained Git history provide the required record.
