Unused. `framework: teacup-agent-cli` runs a sandboxed subprocess
(`external_cli.py`) instead of Teacup Run's own model+tool loop, so nothing ever
reads this file's contents. It exists only because `AgentSpec.validate()` requires
the file named by `instructions:` in `agent.yaml` to be present and readable —
the target teacup-agent checkout supplies its own instructions internally.
