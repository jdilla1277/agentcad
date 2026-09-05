# Harness source snapshots

These directories contain the `package.json`, lockfile, source, and tests from
the exact internal harness revisions used for the two runs:

- `control/`: `88cc7b20258e98946abdd2e4b0041f79002ea0f3`
- `agentcad/`: `470860f1d3625f4ba1fe122892ded775f04f5f7f`

The original internal README files are omitted because they contain unrelated
operational notes. The executable source and tests are otherwise copied
without modification. Run `npm ci && npm test` inside either directory to run
its unit tests. Live benchmark execution additionally requires provider
credentials, CADGenBench fixture data, candidate executables, and a fixed
evaluator executable.
