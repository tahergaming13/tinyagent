# mini-agent

A minimal terminal coding agent that runs **entirely against a local Ollama model**.
Built from first principles for **small LLMs (~7–8B) with ~8K context windows**.

No LangChain, no frameworks, no cloud. Nine tools, one loop, one context budget.

```
User → CLI → Agent → Ollama → tool call? ──YES──▶ execute → context ──▶ Ollama …
                                        └──NO───▶ final answer
```

## Why

Big agent frameworks assume big contexts. A 7B model with 8K tokens drowns in
huge system prompts and endless history. mini-agent inverts the priorities:

1. Minimal system prompt (~20 lines)
2. Minimal tool schemas (one line per tool)
3. Deterministic Python context management (discard-first, never summarize-by-default)
4. Reliable tool execution (validated, capped, never crashes the loop)

Dumb infrastructure + smart-enough model.

## Installation

```bash
cd mini-agent
python -m pip install -e .   # installs the `tinyagent` command (+ rich)
```

Then open a **new** terminal (so PATH refreshes) and run it from anywhere:

```bash
tinyagent
```

The workspace defaults to `./workspace` under your current directory —
`cd` to your project first, or set `WORKSPACE` in `.env`.
(Without install: `python -m pip install -r requirements.txt` + `python main.py`.)

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

## Ollama setup

```bash
ollama serve
ollama pull qwen3:8b        # or qwen2.5-coder:7b, gemma3:4b, ...
```

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

```bash
tinyagent
```

```
mini-agent  |  Model: qwen3:8b  |  Context: 8192  |  Workspace: .../workspace
> inspect this FastAPI project and fix the startup error
● Thinking...
→ list_files({'path': '.'})
→ read_file({'path': 'app/main.py'})
→ run_command({'command': 'python -m uvicorn app.main:app'})
✓ ... final answer
```

Commands: `/help` `/clear` `/compact` `/init` `/undo` `/context` `/tools`
`/model [name]` (show or switch model) `/thinking [on|off]` (raw output view)
`/sessions` `/resume <name>` `/export [name[.md]]` `/rename <name>` `/fork [name]`
`/quit`

One-shot (non-interactive) runs and per-run overrides:

```bash
tinyagent "fix the failing test in calc.py"
tinyagent -m qwen3-local:latest -w C:\Projects\myapp "list the project layout"
```

Mention files inline with `@path` — the contents are attached (capped):

```text
> fix the bug in @calc.py
```

Type `/` on its own for a numbered menu of every command (pick by number or
name); partial typing like `/mod` offers matching commands. Bare `/model`
lists your local Ollama models with the current one starred — pick one to
switch mid-session (`/model <name>` switches directly).

```text
> fix the bug in @calc.py
```

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

## How the agent loop works

`agent.py` — one `while True`:

1. Send `messages` to Ollama (`ollama.py`, native `/api/chat`, streaming).
2. Parse the reply for a ```` ```tool_call {"name", "arguments"} ```` fence.
   The parser is deliberately tolerant (bare `[tool_call]` lines,
   single-quoted dicts, standalone echoed calls): small models leak variants,
   and history markers use past-tense `[executed: …]` so they don't prime
   mimicry. Unknown tools are rejected, never executed.
3. `execute_tool()` validates, runs, catches everything, caps output.
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
- `/clear` resets; `/context` shows the budget.

## Security limitations

- All file tools are jailed to `WORKSPACE` (`permissions.resolve_in_workspace`);
  `../../` escapes are rejected, not sanitized.
- `run_command` blocks patterns in `permissions.DANGEROUS_PATTERNS` — this is a
  speed bump, not a sandbox. Review the list before pointing the workspace at
  anything precious. Arbitrary shell execution is inherently powerful.
- v0.1 is **not** a security sandbox. Don't run untrusted prompts with an
  SSH agent loaded and a prod checkout mounted.

## Adding a new tool

1. Write the function in `tools/` (keep it small, return a string).
2. Register in `tools.py`: add to `_DISPATCH` + one line in `TOOL_SCHEMAS`.
3. Done — the agent sees it next run. No framework wiring.

## Tests

```bash
python -m pytest tests/ -q
```

All mocked — no Ollama needed. Covers files, terminal, context budgets,
and the full `user → tool → result → tool → answer` loop.

## Project structure

```
mini-agent/
├── main.py            classic CLI (commands, sessions, @file, one-shot mode)
├── tui.py             full-screen Textual UI (same agent underneath)
├── sessions.py        session save/load/list/rename/export (JSON in ~/.tinyagent)
├── agent.py           loop + tool-call parsing + minimal system prompt
├── ollama.py          native Ollama client (stdlib urllib, streaming)
├── context.py         budget, discard-first pruning, compression
├── tools/__init__.py   registry (`TOOLS`, `execute_tool()`)
│   ※ the spec's `tools.py` lives here instead: Python cannot import both a
│     `tools.py` module and a `tools/` package (the package shadows the
│     module), so the registry is the package's `__init__`.
├── permissions.py     workspace jail + dangerous-command gate
├── config.py          env/.env config, no code changes for new models
├── tools/filesystem.py  read/write/edit/list + undo stack
├── tools/terminal.py    run_command
├── tools/search.py      grep/glob
├── tools/web.py         web_search (DDG + Wikipedia fallback) + web_fetch
├── tests/             mocked, offline (test_edit_search.py covers the new tools)
└── workspace/         agent sandbox
```

## Example (first milestone)

```
> Create a FastAPI hello world app here, run it, make sure it starts.
→ list_files({'.'})
→ write_file({'path': 'app/main.py', 'content': '...'})
→ run_command({'command': 'python -c "import app.main; print(\'ok\')"'})
✓ exit 0 → "Done: app/main.py serves GET / → {hello: world}."
```

## Roadmap (not yet built)

git, docker, browser, MCP, LSP, subagents, sessions, memory, indexing,
GUI — the registry + context manager are shaped to accept them later.
