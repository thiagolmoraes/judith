# Judith

<p align="center"><img src="docs/assets/judith.png" width="160" alt="Judith" /></p>

<p align="center">AI coworker on your desktop with a WhatsApp ⇄ Claude Code bridge</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License" /></a>
  <img src="https://img.shields.io/badge/platform-macOS-lightgrey" alt="Platform: macOS" />
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+" />
</p>

Judith delivers finished work—documents, reports, emails, calendar updates—across your desktop, terminal, and connected apps. Your model key stays on this device, and your data leaves it only through the model provider and the integrations you choose to connect.

## Fork notice

Judith is a fork of [andrewyng/openworker](https://github.com/andrewyng/openworker) (MIT license, © 2024 Andrew Ng). It extends OpenWorker with a WhatsApp ↔ Claude Code bridge and is maintained separately. See [LICENSE](LICENSE).

## What it does

Judith inherits OpenWorker's core capabilities:

- **Works across your desktop** — 25+ integrations including GitHub, Slack, Jira, Notion, Linear, HubSpot, Outlook, Gmail, Google Calendar, plus terminal and local files. Any tool reachable over [MCP](https://modelcontextprotocol.io/) plugs in too.
- **Produces real deliverables** — documents, spreadsheets, reports, and web pages land as files you can open and share.
- **Approval-gated actions** — writes, sends, and shell commands wait for your sign-off. Unattended runs queue their asks instead of acting alone.
- **Scheduled automations** — recurring work like morning briefs, weekly reports, or standing watches run on schedule with full transcripts.
- **Bring your own model** — OpenAI, Anthropic, Google, Ollama, Mistral, Grok, DeepSeek, and others. Your key, your choice, no lock-in.
- **Local-first** — the agent, your conversations, tokens, and model keys live on this device. What you send to the model provider and to connected services is, by design, the only data that leaves it.

## WhatsApp ⇄ Claude Code bridge

From WhatsApp, manage your Claude Code CLI sessions running in iTerm2.

| Capability | Description |
|---|---|
| List sessions | View all live Claude Code sessions in open iTerm2 tabs |
| Read transcripts | Check session transcripts and verify if work is finished |
| Send input | Direct input to a running session |
| Get notifications | Receive alerts when a watched session completes |
| Approve remotely | Approve or deny permission prompts from WhatsApp (with echo-confirmation step) |

### How it works

WhatsApp connects through a self-hosted [Evolution API](infra/whatsapp-evolution/) instance; inbound messages open a per-contact session with the Judith persona, whose tools discover live `claude` processes (by tty), read their transcripts, and type into the right iTerm2 tab via AppleScript. Claude Code hooks (`Stop`/`Notification`) write exact session state to a local file registry — that is what powers precise status, the finish notifications, and remote approval. Install the hooks with:

```shell
python -m coworker.claude_bridge.install
```

macOS only. Requires the Evolution API stack running (see `infra/whatsapp-evolution/README.md`).

## Build from source

**Prerequisites:** Python 3.10+, Node 20+, Rust toolchain ([rustup](https://rustup.rs/)).

```shell
git clone https://github.com/thiagolmoraes/judith
cd judith

# 1. Set up Python environment
python3 -m venv .venv
.venv/bin/pip install -e . pyinstaller tzdata typer

# 2. Set up GUI
cd surfaces/gui
npm ci
cd ../..

# 3. Build unsigned dev DMG
./packaging/build_dmg.sh

# 4. Install and ad-hoc sign (unsigned builds refuse to launch otherwise)
cp -R surfaces/gui/src-tauri/target/release/bundle/macos/Judith.app /Applications/
codesign --force --deep -s - /Applications/Judith.app
```

For active development:

```shell
# Terminal 1: start the server
.venv/bin/openworker-server --cwd ~/some/project --port 8765

# Terminal 2: start the UI
cd surfaces/gui
npm run dev        # Vite dev server
# or
npm run tauri dev  # Full Tauri desktop app
```

Run tests: `.venv/bin/pytest` (server), `npm test` and `npm run e2e` in `surfaces/gui/`.

## License

MIT — see [LICENSE](LICENSE). © 2024 Andrew Ng. Fork modifications by Mangia.
