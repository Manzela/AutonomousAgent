# Agent Project Context

This Hermes Agent runs on the AutonomousAgent project, deployed locally via docker-compose.

## Working directory

The shell sandbox mounts `workspace` as `/workspace`. Treat that as the persistent project workspace.

## Tools

You have access to:
- File reads (in-process, host FS read-only)
- Shell commands (Docker shell sandbox, no network)
- Browser automation (Playwright sandbox)
- GitHub via MCP (gh-authenticated)
- Context7 for live library docs
- Web search via the agent's built-in tools

For arbitrary code execution beyond shell, ask first — we may route to a cloud sandbox.

## Conventions

- Always commit work in small, focused git commits
- Prefer editing existing files over creating new ones
- Run tests before declaring work complete
- Follow the patterns in CLAUDE.md / AGENTS.md files of the projects you work in
