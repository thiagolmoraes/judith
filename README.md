# Judith

<p align="center"><img src="docs/assets/judith.png" width="160" alt="Judith" /></p>

AI coworker on your desktop with a WhatsApp ⇄ Claude Code bridge.
Fork of [andrewyng/openworker](https://github.com/andrewyng/openworker), MIT, © 2024 Andrew Ng, see [LICENSE](LICENSE).

## What it does

- **Connectors** — GitHub, Slack, Jira, Notion, Linear, HubSpot, Outlook, Gmail, Google Calendar, any MCP server, plus your terminal and local files.
- **Approval-gated actions** — writes, sends, and shell commands wait for your sign-off.
- **Scheduled automations** — recurring work runs on schedule with full transcripts.
- **Bring your own model** — OpenAI, Anthropic, Google, Ollama, Mistral, or others. Local-first: your keys and data stay on this device.

## WhatsApp ⇄ Claude Code bridge

Manage Claude Code CLI sessions in iTerm2 from WhatsApp.

- List live sessions in iTerm2 tabs
- Read transcripts and check if work is finished
- Send input to running sessions
- Receive completion alerts
- Approve or deny permission prompts remotely

How it works: WhatsApp connects via self-hosted [Evolution API](infra/whatsapp-evolution/);
per-contact sessions discover live `claude` processes by tty, read transcripts, and type
into iTerm2 tabs via AppleScript. Claude Code hooks (`Stop`/`Notification`) write session
state to a registry for precise status and remote approval.

Install the Claude Code hooks:

```shell
python -m coworker.claude_bridge.install
```

macOS only; needs the Evolution API stack in `infra/whatsapp-evolution/`.

## Build from source

Prerequisites: Python 3.10+, Node 20+, Rust.

```shell
python3 -m venv .venv && .venv/bin/pip install -e . pyinstaller tzdata typer
(cd surfaces/gui && npm ci)
./packaging/build_dmg.sh
cp -R surfaces/gui/src-tauri/target/release/bundle/macos/Judith.app /Applications/
codesign --force --deep -s - /Applications/Judith.app
```

Tests: `.venv/bin/pytest` (server), `npm test` and `npm run e2e` in `surfaces/gui/`.

## License

MIT — see [LICENSE](LICENSE). © 2024 Andrew Ng. Fork modifications by Mangia.
Issues: [thiagolmoraes/judith/issues](https://github.com/thiagolmoraes/judith/issues)
