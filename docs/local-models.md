# Running Bib Check Agent on local and open-source models

The skill's lookups are done by a plain Python script, so the model only has to drive the agent: read the instructions, run the checker, research flagged entries with the checker's `--search` and `--bibtex` commands, and edit files. A model can do that if:

- **It calls tools reliably.** It has to run shell commands and edit files, not describe them.
- **Its context window is large enough** for your agent's own instructions plus the skill. With Claude Code that means 128k tokens.
- **It runs inside an agent that loads skills.** Inference servers such as Ollama, LM Studio and llama.cpp serve models, but they don't load skills themselves.

## Tested models

All runs used Claude Code against Ollama 0.35.1 on Windows 11, with an RTX 5090 (32 GB). Each processed the 7-entry test paper in `tests/e2e/fixture-small` and was scored with `tests/e2e/score.py`. "Safety" checks that every key was kept, only cite keys changed in `.tex`, a backup exists, and the invented paper is flagged. "Quality" checks that each planted problem was fixed and nothing extra was added.

| Model | Download | Context | Result |
|---|---|---|---|
| qwen3.8:27b | 17 GB | 128k | Works. All three runs passed every safety check and fixed every planted problem, in about 15 to 20 minutes each. One run was perfect. In the other two, the model did one thing it shouldn't have: one added two unrequested entries to the `.bib`, and the other left a helper script in the project. The procedure now forbids both. With no web search, the invented paper is marked UNRESOLVED ("likely fabricated") rather than called made up. |
| qwen3.8:27b | 17 GB | 64k | Fails. Claude Code's prompt plus its reply reserve fill the window ("Autocompact is thrashing"). |
| qwen3.5:9b | 6.6 GB | 128k | Doesn't work. In two runs it either looked for the `.bib` in the wrong folder, or printed the checker command as text instead of running it, and stopped. It changed no files. |
| qwen3.5:4b | 3.4 GB | 128k | Doesn't work. It found the file but ran commands in forms the skill doesn't pre-approve, and gave up when they were blocked. It changed no files. |

In short, you need a model of roughly 27B parameters or more with strong tool calling. Smaller models fail safely: they stop without changing your files.

GPU memory: qwen3.8:27b at 128k context needs about 29 GB of VRAM to stay entirely on the GPU. On a 16 GB card a model that size runs partly on the CPU, which works but is many times slower. Without a large enough GPU, run the skill with a hosted model instead (Claude, or Ollama's `:cloud` models).

Untested but likely to work, judging from their tool-calling reputation and size: Qwen 3.x 27B and larger, qwen3-coder:30b, gpt-oss:20b and 120b, devstral-small-2, glm-4.7-flash. Please report results.

## Claude Code + Ollama (tested)

**0. Install.** You need:
- [Ollama](https://ollama.com) 0.33 or newer.
- Claude Code. You don't need an Anthropic account for local models.
- On Windows, [Git for Windows](https://git-scm.com/downloads/win). Claude Code's Bash tool uses Git Bash, and the skill runs its commands through it.

Then add the skill, from any terminal:

```bash
claude plugin marketplace add nnicholas-c/bib-check-agent
claude plugin install bib-check-agent@bib-check-agent
```

**1. Make a 128k-context copy of the model.** This reuses the downloaded weights, so nothing new is downloaded.

```bash
printf 'FROM qwen3.8:27b\nPARAMETER num_ctx 131072\n' > Modelfile
ollama create qwen3.8-128k -f Modelfile
```

In PowerShell, write the file with `Set-Content` instead (in Windows PowerShell 5.1, don't use `echo ... > Modelfile`, which writes UTF-16):

```powershell
Set-Content Modelfile "FROM qwen3.8:27b`nPARAMETER num_ctx 131072"
ollama create qwen3.8-128k -f Modelfile
```

While the model is loaded, `ollama ps` should show `100% GPU` and a context of `131072`. If part of the model is on the CPU it will be very slow, so choose a model that fits your GPU. Keep the context at 128k, because 64k fails with Claude Code.

**2. Point Claude Code at Ollama**, and start it from your paper's folder:

```bash
export ANTHROPIC_BASE_URL=http://localhost:11434 ANTHROPIC_AUTH_TOKEN=ollama ANTHROPIC_API_KEY=""
export CLAUDE_CODE_MAX_CONTEXT_TOKENS=131072 CLAUDE_CODE_MAX_OUTPUT_TOKENS=16000 CLAUDE_CODE_DISABLE_WORKFLOWS=1
export CLAUDE_CODE_SUBAGENT_MODEL=qwen3.8-128k ANTHROPIC_DEFAULT_OPUS_MODEL=qwen3.8-128k
export ANTHROPIC_DEFAULT_SONNET_MODEL=qwen3.8-128k ANTHROPIC_DEFAULT_HAIKU_MODEL=qwen3.8-128k
export ENABLE_CLAUDEAI_MCP_SERVERS=false CLAUDE_CODE_TOTAL_TOKENS_REMINDER=off CLAUDE_CODE_ATTRIBUTION_HEADER=0
claude --model qwen3.8-128k --tools "Bash,Read,Edit,Write,Grep,Glob,Skill" --strict-mcp-config
```

In PowerShell, set each variable as `$env:NAME = "value"`, for example `$env:ANTHROPIC_BASE_URL = "http://localhost:11434"`, and then run the same `claude` line.

**3. Ask:** `/bib-check-agent:bib-check-agent refs.bib`. If you copied the skill folder into `~/.claude/skills/` instead of installing the plugin, use `/bib-check-agent refs.bib`.

The tested runs used Claude Code's headless mode with the same flags: `claude -p "/bib-check-agent:bib-check-agent refs.bib" --model qwen3.8-128k --tools ...`. If the interactive session misbehaves, use that form.

What the settings do:

- **`CLAUDE_CODE_MAX_CONTEXT_TOKENS`** must match the window you gave Ollama. Ollama silently drops the oldest messages when a request is too long, so Claude Code has to compact before that happens.
- **`CLAUDE_CODE_MAX_OUTPUT_TOKENS=16000`** shrinks the space Claude Code holds back for each reply. The default for unknown models is 32k.
- **`--tools` and `--strict-mcp-config`** keep only the tools the skill uses, which makes Claude Code's prompt much smaller.
- **`CLAUDE_CODE_DISABLE_WORKFLOWS=1`** sends the model down the one-entry-at-a-time procedure instead of a fleet of parallel agents.
- **The `ANTHROPIC_DEFAULT_*_MODEL` and `CLAUDE_CODE_SUBAGENT_MODEL` variables** make sure nothing asks Ollama for a Claude model it doesn't have.
- **`CLAUDE_CODE_TOTAL_TOKENS_REMINDER=off` and `CLAUDE_CODE_ATTRIBUTION_HEADER=0`** stop Claude Code from changing the prompt on every request, so Ollama can reuse its cache. Ollama's own launcher sets these. They weren't used in the tested runs, which still worked without them.

`ollama launch claude --model <model>` sets the base URL, token, model and cache variables for you, but not `CLAUDE_CODE_MAX_CONTEXT_TOKENS`, `CLAUDE_CODE_MAX_OUTPUT_TOKENS` or `CLAUDE_CODE_DISABLE_WORKFLOWS`. Export those first.

## Other local servers

Claude Code works with any server that speaks the Anthropic Messages API. Use the same variables, with a different `ANTHROPIC_BASE_URL`:

| Server | Base URL | Notes |
|---|---|---|
| Ollama 0.33 or newer | `http://localhost:11434` | Tested with 0.35.1 |
| LM Studio 0.4.1 or newer | `http://localhost:1234` | Untested. Start the server with `lms server start --port 1234`, and set the context length in the model's load settings. |
| llama.cpp `llama-server` | `http://127.0.0.1:8080` | Untested. Pass `-c 131072` for the context. |

## Other agents with local models (untested)

These agents load the same skill folder and reach local models through an OpenAI-compatible endpoint: `http://localhost:11434/v1` for Ollama, `http://127.0.0.1:1234/v1` for LM Studio, and `http://127.0.0.1:8080/v1` for llama.cpp. They don't carry Claude Code's large built-in prompt, so 64k of context may be enough. Whatever the agent, Ollama's context and the agent's idea of it must match.

- **OpenCode.**
  1. Install the skill with `npx skills add nnicholas-c/bib-check-agent -a opencode`, or copy the folder to `~/.agents/skills/`.
  2. Make a 64k copy of the model as in step 1 above, with `PARAMETER num_ctx 65536`.
  3. Add it as a provider in `opencode.json`; OpenCode's provider docs show how.
- **Codex CLI.**
  1. Make a 64k copy of the model the same way.
  2. Put `model_context_window = 65536` in `~/.codex/config.toml`.
  3. Run `codex --oss -m <copy name>`. Turn on network access for the checker (see the main README).
  4. Invoke the skill with `$bib-check-agent refs.bib`.
- **Goose, Cline, OpenHands** and other open-source agents also load skills from `~/.agents/skills/` and support Ollama or LM Studio.

Invoke the skill explicitly. Local models rarely pick up a skill on their own.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Autocompact is thrashing" | The window is too small. Use 128k with Claude Code and set `CLAUDE_CODE_MAX_OUTPUT_TOKENS=16000`. |
| The model prints `<tool_call>` or `<function=...>` text instead of running tools | The model or Ollama version has a tool-calling bug. Upgrade Ollama, or try another model. |
| The model asks where your `.bib` file is | Start the agent from your paper's folder, and name the file in your request. |
| The model says it can't search the web | Expected with most local setups. The procedure researches with the checker's `--search` instead, and marks references it can't find as UNRESOLVED ("likely fabricated") rather than calling them made up. |
| "Cannot reach the paper databases" | Your network or sandbox blocks the hosts listed in the README. |
| Very slow | Check `ollama ps`. If the processor isn't `100% GPU`, part of the model is on the CPU. |
| No Bash tool on Windows | Install Git for Windows and restart Claude Code. |
