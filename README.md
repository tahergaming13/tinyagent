# tinyagent

A minimal terminal coding agent that runs **entirely against a local Ollama model**.
Built from first principles for **small LLMs (~7–8B) with ~8K context windows**.

No LangChain, no frameworks, no cloud. Nine tools, one loop, one context budget.

```
User → CLI → Agent → Ollama → tool call? ──YES──▶ execute → context ──▶ Ollama …
                                        └──NO───▶ final answer
```

## Why

Big agent frameworks assume big contexts. A 7B model with 8K tokens drowns in
huge system prompts and endless history. tinyagent inverts the priorities:

1. Minimal system prompt (~20 lines)
2. Minimal tool schemas (one line per tool)
3. Deterministic Python context management (discard-first, never summarize-by-default)
4. Reliable tool execution (validated, capped, never crashes the loop)

Dumb infrastructure + smart-enough model.

---

## Setup (5 minutes)

You need two things: **Python 3.11+** and **Ollama**. Pick your OS.

### Windows (PowerShell)

```powershell
# 1. Python (skip if `python --version` already shows 3.11+)
winget install Python.Python.3.12

# 2. Ollama → https://ollama.com/download, run the installer
#    (it starts automatically; check with `ollama list` in a NEW terminal)

# 3. tinyagent
git clone https://github.com/tahergaming13/tinyagent.git
cd tinyagent
python -m pip install -e .
```

> If `tinyagent` is "not recognized", your Python `Scripts` folder isn't on
> PATH. Either open a **new** terminal and retry, or run this once (then open
> a new terminal):
>
> ```powershell
> $s = python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
> setx Path "$env:Path;$s"
> ```

### macOS (Terminal)

```bash
# 1. Python + Ollama (skip what you already have)
brew install python ollama pipx

# 2. Start Ollama (or run `ollama serve` in another tab)
brew services start ollama

# 3. tinyagent (pipx handles PATH for you)
git clone https://github.com/tahergaming13/tinyagent.git
cd tinyagent
pipx install .
```

> No Homebrew? Get Python from https://www.python.org/downloads and Ollama
> from https://ollama.com/download, then use `python3 -m pip install -e .`
> instead of `pipx install .`.

### Linux (bash)

```bash
# 1. Python + pipx (Debian/Ubuntu example)
sudo apt update && sudo apt install -y python3 python3-pip pipx git
pipx ensurepath   # then open a NEW terminal

# 2. Ollama
curl -fsSL https://ollama.com/install.sh | sh
# (starts as a service; otherwise run `ollama serve` in another terminal)

# 3. tinyagent
git clone https://github.com/tahergaming13/tinyagent.git
cd tinyagent
pipx install .
```

> Don't have sudo? Any Python 3.11+ works — then `python3 -m pip install -e .`
> inside a venv, or `pipx install .` with a user-level pipx install.

### All systems — pull a model & configure

```bash
ollama pull qwen3:8b
cp .env.example .env
```

Open `.env` and make sure `OLLAMA_MODEL` matches what you pulled:

```env
OLLAMA_MODEL=qwen3:8b
```

Model suggestions (all run locally, pick by RAM):

| Model | Size | Good for |
|---|---|---|
| `qwen3:8b` | ~5 GB | default all-rounder |
| `qwen2.5-coder:7b` | ~4.7 GB | code tasks (recommended for coding) |
| `gemma3:4b` | ~3.3 GB | weaker machines |

### Run it

```bash
tinyagent --tui    # full-screen UI (recommended)
tinyagent          # classic line-by-line CLI
```

Success looks like this:

```
tinyagent  |  Model: qwen3:8b  |  Context: 8192  |  Workspace: .../workspace
Connected. Models: ['qwen3:8b']
```

Then give it its first job:

```
> list the files in the workspace
```

```
● Thinking...
→ list_files({'path': '.'})
  OK: ...
— done. Context: ~300 / 8,192 tokens —
```

The workspace defaults to `./workspace` under your current directory —
`cd` to your project first, or set an absolute `WORKSPACE` path in `.env`.

---

## Full-screen UI (OpenCode-style)

```bash
tinyagent --tui
```

Top bar (model · session · live context meter), scrolling transcript with
streaming answers, `/` autocomplete dropdown (↑↓ + Enter to complete),
modal pickers for `/model` and `/resume`, and a status line — all 15 commands
and 9 tools work exactly as in the classic CLI. Typing a partial command and
hitting Enter runs it when the match is unique, otherwise opens a chooser;
bare `/` opens the full command menu. The agent runs in a
background thread so the UI never freezes; `Ctrl+Q` quits anytime.

> Use a modern terminal (Windows Terminal, iTerm2, GNOME Terminal, kitty…)
> so the box-drawing glyphs and colors render correctly.

## Configuration

Copy `.env.example` to `.env` and edit — or just set env vars:

| Var | Default | Meaning |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server |
| `OLLAMA_MODEL` | `qwen3:8b` | model (change without touching code) |
| `CONTEXT_SIZE` | `8192` | model window |
| `RESERVED_OUTPUT` | `2048` | kept free; input budget = size − reserved |
| `TEMPERATURE` | `0.2` | low = reliable tool JSON |
| `MAX_TOOL_CALLS` | `20` | loop guard |
| `COMMAND_TIMEOUT` | `30` | seconds per command |
| `WORKSPACE` | `./workspace` | agent sandbox root |
| `MAX_FILE_SIZE` | `100000` | chars read per file |
| `MAX_COMMAND_OUTPUT` | `12000` | chars kept per command |
| `DEBUG` | `0` | `1` for debug log lines |

## Usage

Example session (classic CLI):

```
> inspect this FastAPI project and fix the startup error
● Thinking...
→ list_files({'path': '.'})
→ read_file({'path': 'app/main.py'})
→ run_command({'command': 'python -m uvicorn app.main:app'})
✓ ... final answer
```

Commands: `/help` `/clear` `/compact` `/init` `/undo` `/context` `/tools`
`/model [name]` (show, pick, or switch model) `/thinking [on|off]`
`/sessions` `/resume <name>` `/export [name[.md]]` `/rename <name>` `/fork [name]`
`/quit`

One-shot (non-interactive) runs and per-run overrides:

```bash
tinyagent "fix the failing test in calc.py"
tinyagent -m qwen2.5-coder:7b -w ~/Projects/myapp "list the project layout"
```

Mention files inline with `@path` — the contents are attached (capped):

```text
> fix the bug in @calc.py
```

Type `/` on its own for a numbered menu of every command (pick by number or
name); partial typing like `/mod` offers matching commands. Bare `/model`
lists your local Ollama models with the current one starred — pick one to
switch mid-session (`/model <name>` switches directly).

Sessions live in `~/.tinyagent/sessions/` as plain JSON (override with
`$TINYAGENT_HOME`): `/export` saves the current context, `/resume` loads it,
`/fork` branches it, `/rename` relabels it, `/export notes.md` writes a
readable transcript instead. Saved sessions rebuild the system prompt on load,
so they survive tool-list upgrades.

## Tools

- `read_file {path, start_line?, end_line?}` — numbered lines, encoding-tolerant,
  flags truncation, range reads for big files.
- `write_file {path, content}` — creates parents, stays in workspace, reports bytes/lines.
- `edit_file {path, old_string, new_string}` — surgical replacement;
  `old_string` must occur exactly once (include context lines to disambiguate).
- `list_files {path?}` — depth-limited tree, hides `.git/node_modules/.venv/...`,
  respects `.gitignore`.
- `glob_files {pattern?, path?}` — find files by name (`**/*.py`).
- `grep_files {pattern, path?, include?}` — regex content search, `file:line:` hits.
- `run_command {command}` — runs in workspace, captures stdout/stderr/exit code,
  timeout, head+tail truncation. Dangerous commands (`rm -rf`, `del`, `format`,
  `shutdown`, `git reset --hard`, …) are **blocked** until the user approves.
- `web_search {query, max_results?}` — the online tool (DuckDuckGo, no API
  key, stdlib only). Tries DDG HTML results, falls back to Wikipedia matches
  when DDG bot-walls the request, caps output at ~2.5K chars. Use sparingly:
  search output eats context fast. Upgrade path: add a key-based provider
  (Tavily/Brave) as another `_search_*` function in `tools/web.py`.
- `web_fetch {url}` — fetch a page as capped readable text.

Every write/edit is undoable (`/undo`, session-scoped). Tool results show as
one-line `OK:`/`ERROR:` feedback under each `→` call.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `tinyagent: command not found` | Reopen the terminal (PATH refresh). Still missing? See the Windows PATH note above, or run `pipx ensurepath` (macOS/Linux) |
| `python: command not found` | Use `python3` (macOS/Linux), or install Python and tick “Add to PATH” (Windows) |
| `Cannot reach Ollama` | Start it: `ollama serve` (or the Ollama app / `brew services start ollama`) |
| `Model 'qwen3:8b' not found` | `ollama pull qwen3:8b`, or set `OLLAMA_MODEL` to a model from `ollama list` |
| First answer is very slow | The model is loading into RAM/VRAM — one-time cost per model, later runs are fast |
| Agent answers without acting | Task too vague — name files/commands explicitly; keep `TEMPERATURE` low |
| Same tool call repeating | Let the repeat-guard warn it; if stuck, `/clear` and restate a smaller task |
| `BLOCKED: command looks dangerous` | Expected for `rm`/`del`/etc. Run it yourself or rephrase |
| `TIMEOUT after 30s` | Long server process — run it manually in another terminal, or raise `COMMAND_TIMEOUT` |
| Context near 100% | `/clear`, or `/compact` to squeeze the session into a summary |
| Garbled boxes in `--tui` | Switch to a UTF-8 terminal (Windows Terminal, iTerm2, …) |
| Web search returns nothing | Your network may bot-wall DuckDuckGo; the Wikipedia fallback covers topics, or add a key-based provider (see Tools) |

## How the agent loop works

`agent.py` — one `while True`:

1. Send `messages` to Ollama (`ollama.py`, native `/api/chat`, streaming).
2. Parse the reply for a ```` ```tool_call {"name", "arguments"} ```` fence.
   The parser is deliberately tolerant (bare `[tool_call]` lines,
   single-quoted dicts, standalone echoed calls): small models leak variants,
   and history markers use past-tense `[executed: …]` so they don't prime
   mimicry. Unknown tools are rejected, never executed.
3. `execute_tool()` validates, runs, catches everything, caps output.
4. `ContextManager.add_tool_result()` appends the compact result; repeat
   identical calls get a warning instead of a 3rd execution.
5. Repeat until a reply has no tool call (final answer) or `MAX_TOOL_CALLS`.

## Context management (the important part)

`context.py` — target: stay under `CONTEXT_SIZE − RESERVED_OUTPUT`.

- Token estimate: `len(text)//4` — crude, visible (`Context: ~4,820 / 8,192`).
- **Discard-first, don't summarize**: drop old successful command output →
  old file reads → old tool results → duplicates. Never ask the small model to
  do bookkeeping. Never auto-summarize the conversation.
- Keep: system prompt, current user task, recent turns, errors, files in play.
- Tool results compressed head+tail with a `[TRUNCATED …]` flag; exit codes and
  stderr always preserved.
- `/clear` resets; `/compact` squeezes; `/context` shows the budget.

## Security limitations

- All file tools are jailed to `WORKSPACE` (`permissions.resolve_in_workspace`);
  `../../` escapes are rejected, not sanitized.
- `run_command` blocks patterns in `permissions.DANGEROUS_PATTERNS` — this is a
  speed bump, not a sandbox. Review the list before pointing the workspace at
  anything precious. Arbitrary shell execution is inherently powerful.
- tinyagent is **not** a security sandbox. Don't run untrusted prompts with an
  SSH agent loaded and a prod checkout mounted.

## Adding a new tool

1. Write the function in `tools/` (keep it small, return a string).
2. Register in `tools/__init__.py`: add to `_DISPATCH` + one line in `TOOL_SCHEMAS`.
3. Done — the agent sees it next run. No framework wiring.

## Tests

```bash
python -m pytest tests/ -q
```

All mocked — no Ollama needed (73 tests: files, terminal, context budgets,
agent loop, sessions, CLI menus, headless TUI runs).

## Project structure

```
tinyagent/
├── main.py              classic CLI (commands, sessions, @file, one-shot mode)
├── tui.py               full-screen Textual UI (same agent underneath)
├── sessions.py          session save/load/list/rename/export (JSON in ~/.tinyagent)
├── agent.py             loop + tool-call parsing + minimal system prompt
├── ollama.py            native Ollama client (stdlib urllib, streaming)
├── context.py           budget, discard-first pruning, compression
├── tools/__init__.py    registry (`TOOLS`, `execute_tool()`)
├── permissions.py       workspace jail + dangerous-command gate
├── config.py            env/.env config, no code changes for new models
├── tools/filesystem.py  read/write/edit/list + undo stack
├── tools/terminal.py    run_command
├── tools/search.py      grep/glob
├── tools/web.py         web_search (DDG + Wikipedia fallback) + web_fetch
├── tests/               mocked, offline
└── workspace/           agent sandbox (gitignored)
```

> ※ Why no `tools.py`? Python cannot import both a `tools.py` module and a
> `tools/` package (the package shadows the module), so the registry lives in
> the package's `__init__`.

## Example (first milestone)

```
> Create a FastAPI hello world app here, run it, make sure it starts.
→ list_files({'.'})
→ write_file({'path': 'app/main.py', 'content': '...'})
→ run_command({'command': 'python -c "import app.main; print(\'ok\')"'})
✓ exit 0 → "Done: app/main.py serves GET / → {hello: world}."
```

## Roadmap (not yet built)

git integration, docker tools, browser, MCP, LSP, subagents, persistent memory,
project indexing, GUI — the registry + context manager are shaped to accept
them later.
