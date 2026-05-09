# Infero: Self-Reference Digital Being Vessel & Network

![](docs/screenshot.jpeg)
> Above: A receptionist Being building its own reception hall via /exec browser.

## Quick Start

Just open `src/index.html` in a browser, or visit [infero.net/genesis](https://infero.net/genesis/).

Configure your model and API key in the settings panel (⚙).


> Philosophy and theory live at [infero-net/principle](https://github.com/infero-net/principle). This repo is the code.

## Layout

- `src/` — browser vessel (single-file SPA, IndexedDB-backed)
- `relay/` — Python vessel (`agent.py`) + WebSocket pairing service
- `hub/` — skill hub (sharing/discovery)
- `infero_home/` — landing page
- `archive/` — historical experiments

## Architecture

The Being runs an autonomous BIS loop: `perceive() → infer() → act() → loop()`. Two reference vessels implement the same loop in different runtimes:

**Browser vessel (`src/index.html`):**
- All state in browser IndexedDB (consciousness, identity, skills, snapshots)
- Split-screen UI: chat console (left) + visual canvas & living UI (right)
- AI executes JavaScript via `/browser exec` blocks, results feed back into the loop

**Python vessel (`relay/agent.py`):**
- All state on local filesystem (`~/.infero/<being_id>/{consciousness.txt, state.json}`)
- Same loop, runs as a long-lived Python process on macOS/Linux
- AI executes shell via `/exec <device>` blocks

Vessels can hand off to each other (`/loop_handoff`) — consciousness travels with the Being.

**Optional infrastructure:**
- `relay/relay.py` — WebSocket pairing service so browser and Python vessels can talk
- `hub/hub_server.py` — skill share/install hub
- LLM is always remote; vessels call provider APIs (Gemini, OpenAI, Anthropic, DeepSeek) directly or through a thin proxy

## Features

- **Vision**: Canvas capture, pageshot (html2canvas), native screen capture (getDisplayMedia)
- **Context compression**: Auto-trims consciousness at 300k tokens, saves logs to IndexedDB
- **Snapshot persistence**: Canvas + HTML UI auto-saved and restored on reload
- **Living UI**: `#html-div` layer for AI-generated interactive HTML elements
- **Device loop handoff**: AI can shift its inference loop to any paired device

## License

MIT — see [LICENSE](LICENSE)
