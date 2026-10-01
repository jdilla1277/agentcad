# agentcad

agentcad is a local CLI and MCP server for AI-agent CAD workflows.

## Repo Identity

This checkout is the **public** `jdilla1277/agentcad` repo. It is for
externally safe package code, public docs, and examples.

Do **not** open PRs here for internal planning material: PRDs, roadmap notes,
marketing drafts, promotional plans, feedback logs, launch notes, private
operational context, or anything that should not be public.

If the user asks for internal planning, promotion work, or website work for
`agentcad.dev`, use the internal repo (`jdilla1277/agentcad-internal`) instead.
Before creating any PR, run `git remote -v` and confirm whether the target repo
is public or internal.

To get a working tree for the internal repo without disturbing this checkout:

    git -C ~/conductor/repos/agentcad-internal worktree add <path> -b <branch> origin/main

The website lives at `agentcad-site/` in that repo.

## Development

Use Python 3.10-3.12. CadQuery/OpenCascade does not support Python 3.13+.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[mcp,dev]"
pytest
```

## Product Contract

- Commands return structured JSON.
- `agentcad run` creates versioned output directories and records metadata.
- `agentcad docs` and `agentcad --help` are part of the agent-facing API.
- Keep error messages concise and actionable for coding agents.
- Prefer local, deterministic workflows over hosted dependencies.
- The command list and the `agentcad docs` section list are pinned by
  `tests/test_public_surface.py`. Adding or removing either fails that test
  until you regenerate the snapshot with
  `AGENTCAD_UPDATE_SURFACE=1 pytest tests/test_public_surface.py` **and**
  update the hand-written mirror at agentcad.dev/docs. The website is in a
  different repo, so nothing else will catch that drift.

## Validation

For changes to agent-facing contracts, CLI workflows, docs/help output, viewer
behavior, or app-like user flows, run a narrow sub-agent friction check before
opening or finalizing the PR. Ask the sub-agent to behave like a fresh agent:
read the docs, use the feature end-to-end in a scratch project, and report
confusing behavior or mismatches between docs and reality.

When output semantics can change the agent's next action, pre-register the
expected behaviors before collecting responses. Use both a targeted
comprehension prompt and a neutral, open-ended workflow prompt when the risk
warrants it. Explicit questions can prove that the output supports a correct
interpretation; they do not prove that an agent will apply that interpretation
without prompting.

For geometry comparison or validation, pair synthetic primitive tests with at
least one real workflow artifact that preserves the topology and container
shape agents actually produce, such as compounds or multi-solid outputs. A
Boolean that works for one closed primitive may behave differently for the
same occupied geometry wrapped in a compound.

Keep friction artifacts under `.context/` unless they are intentional public
fixtures. When validating unmerged CLI behavior, avoid stale installed code and
stale daemons: use the current checkout (`PYTHONPATH=src` or editable install)
and pass `--no-daemon` for command behavior checks.

## Explaining Your Work

When summarizing a change, bug, or design decision for the user, use simple,
concrete language. Mechanism-first jargon is the failure mode.

- Order: what the feature does for the user → what went wrong and what it
  would cause → the fix. Consequence before mechanism.
- Describe geometry and data physically before naming APIs: "two separate
  solids in one container, like LEGO pieces in a bag — not glued together"
  beats "a TopoDS_Compound with non-manifold member interfaces".
- State consequences concretely: "an agent trusting that number would revert
  a correct edit", not "produces incorrect results".
- Give the fix one line of intuition ("the comparison cares about occupied
  space, not how the model is bookkept into pieces") before any detail.
- Keep OCCT/library class names out of the explanation unless the reader
  needs them to act; plain terms ("the overlap math") carry the meaning.

## Fork PRs

External contributors submit PRs from forks. When acting as maintainer on one:

- Check the head repo before pushing follow-up commits:
  `gh pr view <N> --json headRepositoryOwner,maintainerCanModify`.
  Pushing `origin HEAD:<branch>` creates a stray branch on this repo instead
  of updating the PR — push to the fork instead:
  `git push https://github.com/<owner>/agentcad.git HEAD:<branch>`
  (allowed when the PR has maintainer edits enabled).
- Fork-PR CI runs may be held at `action_required` awaiting maintainer
  approval, and `gh pr checks` misleadingly reports "no checks reported".
  Find the held run with
  `gh api "repos/jdilla1277/agentcad/actions/runs?head_sha=<sha>"` and approve
  it with `gh api -X POST repos/jdilla1277/agentcad/actions/runs/<id>/approve`.
- `main` requires green checks and an up-to-date branch (branch protection,
  admins included). If main moves under a fork PR, merge main into the branch
  and push that to the fork.

## Public Repo Rules

- Do not add internal PRDs, roadmap notes, marketing drafts, feedback logs, secrets, or private operational context.
- Keep examples and docs safe for public users.
- Keep generated artifacts out of git unless they are intentional fixtures.
