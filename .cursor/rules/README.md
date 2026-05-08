# Cursor rules for this repo

- **`AGENT_BASE.md`** — your **always-on** personal rules (short copy); Cursor uses **`.cursor/rules/agent-base.mdc`** with `alwaysApply: true` so every session loads them.
- **`PROJECT.md`** — repo layout and technical conventions (`shared_lib`, modules, tests).
- **Cursor IDE:** Files under **`.cursor/rules/*.mdc`** apply automatically when this folder is the project root.

If you rename files, update cross-references in `.cursor/rules/astro-code.mdc` and `AGENT_BASE.md` / `agent-base.mdc`.
