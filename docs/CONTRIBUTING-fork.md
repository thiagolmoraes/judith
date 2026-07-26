# Fork workflow

This fork of [`andrewyng/openworker`](https://github.com/andrewyng/openworker) uses
`deploy/hml` as its working trunk. `main` exists only to track upstream.

## Branches

| Branch       | Purpose                                                        |
| ------------ | -------------------------------------------------------------- |
| `deploy/hml` | Default branch. All feature work targets this.                  |
| `main`       | Mirror of upstream. Never developed on directly.                |
| `deploy/*`   | Future deploy targets. Inherit the same protection rules.       |

## Remotes

```sh
git remote -v
# origin    https://github.com/thiagolmoraes/openworker.git
# upstream  https://github.com/andrewyng/openworker.git
```

If `upstream` is missing:

```sh
git remote add upstream https://github.com/andrewyng/openworker.git
```

## Syncing upstream

Upstream changes land on `main` first, then get evaluated before reaching
`deploy/hml`. Nothing from upstream is merged into the trunk automatically.

```sh
git fetch upstream
git checkout main
git merge --ff-only upstream/main
git push origin main
```

The `--ff-only` flag keeps `main` a faithful mirror: if it refuses to
fast-forward, `main` has diverged and should be inspected rather than merged.

Once `main` is current, decide per change whether it belongs in the trunk:

```sh
git checkout deploy/hml
git merge main          # or cherry-pick individual commits
```

## Feature work

```sh
git checkout deploy/hml
git pull
git checkout -b feature/my-change
# ... commit ...
git push -u origin feature/my-change
gh pr create --base deploy/hml
```

Feature branches are unprotected, so they can be rebased and force-pushed
freely while a PR is in review.

## Branch protection

Two rulesets apply to `main` and `deploy/**`:

- **`no-force-push-no-delete`** — blocks force-pushes and branch deletion. No
  bypass actors, so this applies to repository admins too.
- **`require-pull-request`** — requires a pull request with one approval.
  Repository admins may bypass, which is how upstream syncs reach `main`.

Because force-push has no bypass, a protected branch cannot be rewound in
place. To recover from a bad commit, prefer `git revert`. If history really
must be rewritten, temporarily set the ruleset to `disabled`, then re-enable it:

```sh
gh api -X PUT repos/thiagolmoraes/openworker/rulesets/19772473 -f enforcement=disabled
# ... perform the rewrite ...
gh api -X PUT repos/thiagolmoraes/openworker/rulesets/19772473 -f enforcement=active
```

To inspect the rules that currently apply to a branch:

```sh
gh api repos/thiagolmoraes/openworker/rules/branches/deploy/hml
```

## Automated review

[CodeRabbit](https://coderabbit.ai) reviews every pull request targeting
`deploy/hml`, `main`, or any `deploy/*` branch. Its configuration lives in
[`.coderabbit.yaml`](../.coderabbit.yaml), which defines path-scoped review
instructions — the strictest cover the security-critical modules
(`secrets.py`, `permissions.py`, `workspace_trust.py`, `risk.py`, `audit.py`)
and `coworker/tools/`, whose arguments arrive from model output and are
therefore untrusted.

Review is advisory: CodeRabbit comments but does not block merges.
