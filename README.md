# Judith

**AI coworker on your desktop with a WhatsApp ⇄ Claude Code bridge.** Judith delivers finished work—documents, reports, emails, calendar updates—across your desktop, terminal, and connected apps. Your model key stays on this device, and your data leaves it only through the model provider and the integrations you choose to connect.

## Fork notice

Judith is a fork of [andrewyng/openworker](https://github.com/andrewyng/openworker) (MIT license, © 2024 Andrew Ng). It extends OpenWorker with a WhatsApp ↔ Claude Code bridge and is maintained separately. See [LICENSE](LICENSE).

## What it does

Judith inherits OpenWorker's core capabilities:

- **Works across your desktop** — 25+ integrations including GitHub, Slack, Jira, Notion, Linear, HubSpot, Outlook, Gmail, Google Calendar, plus terminal and local files. Any tool reachable over [MCP](https://modelcontextprotocol.io/) plugs in too.
- **Produces real deliverables** — documents, spreadsheets, reports, and web pages land as files you can open and share.
- **Approval-gated actions** — writes, sends, and shell commands wait for your sign-off. Unattended runs queue their asks instead of acting alone.
- **Scheduled automations** — recurring work like morning briefs, weekly reports, or standing watches run on schedule with full transcripts.
- **Bring your own model** — OpenAI, Anthropic, Google, Ollama, Mistral, Grok, DeepSeek, and others. Your key, your choice, no lock-in.
- **Data stays on your machine** — everything lives locally: the agent, your conversations, tokens, and model keys. Only OAuth handshakes for connectors reach the cloud.

## WhatsApp ⇄ Claude Code bridge

From WhatsApp, manage your Claude Code CLI sessions running in iTerm2:

**Capabilities:**
- List all live Claude Code sessions in open iTerm2 tabs
- Read session transcripts and check if work is finished
- Send input to a running session
- Get notified when a watched session completes
- Approve or deny permission prompts remotely (with echo-confirmation step)

**How it works:**
WhatsApp connects through a self-hosted [Evolution API](infra/whatsapp-evolution/) instance; inbound messages open a per-contact session with the Judith persona, whose tools discover live `claude` processes (by tty), read their transcripts, and type into the right iTerm2 tab via AppleScript. Claude Code hooks (`Stop`/`Notification`) write exact session state to a local file registry — that is what powers precise status, the finish notifications, and remote approval. Install the hooks with:

```shell
python -m coworker.claude_bridge.install
```

macOS only. Requires the Evolution API stack running (see `infra/whatsapp-evolution/README.md`).

## Build from source

**Prerequisites:** Python 3.10+, Node 20+, Rust toolchain ([rustup](https://rustup.rs/)).

```shell
git clone https://github.com/thiagolmoraes/openworker
cd openworker

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
