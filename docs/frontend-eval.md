# Frontend Eval (in-progress)

Test prompt: **"list docker containers running and tell me jellyfin version"**
Ground truth: `Jellyfin.Server 10.11.8.0` (verified via
`docker exec jellyfin /jellyfin/jellyfin --version`).

Goal scoring:
- **Install ease** (1–5, 5 = already on .93)
- **Tool accuracy** — did it actually run commands and report real values?
- **TTFT** — wall time for the whole user-visible response
- **Hallucinations** — claimed facts that weren't verified
- **UX** — interactive ergonomics (TUI / API / web)

## Candidates tested

### hermes-agent (current frontend) — RUN @ 22:09 WEST
- Install: 5 (already on .93, api_server :8642)
- Tool accuracy: **5/5 — returned 10.11.8 (correct)**
- TTFT: 29s
- Hallucinations: 0 — used actual exec to read version
- UX: Open WebUI :3030 OR direct REST :8642; full tool/skill stack
- Notes: earlier failures (empty stops, container-restart hallucination,
  refused `docker compose up -d`) appear resolved by the pool/probe/pi
  fixes (no groq in best primaries, nvidia-nim last fallback, strip-shim
  orphan guard, pi-mono keepRecentTokens 30k).

### pi-agent — RUN @ 22:08 WEST
- Install: 5 (already on .93, npm-global/bin/pi)
- Tool accuracy: **3/5 — listed containers correctly; hallucinated jellyfin v10.9.7**
- TTFT: 34s
- Hallucinations: 1 — claimed v10.9.7 without actual verification
- UX: TUI (`pi` interactive) + non-interactive `-p` mode; no built-in web UI
- Notes: configured for `defaultModel: ops` (smaller pool); for user-facing
  chat, would need a different default. Less reliable on factual claims
  out-of-the-box than hermes-agent.

### opencode (sst/opencode) — RUN @ 22:15 WEST
- Install: 4 (one curl-bash, ~20MB Go binary)
- Tool accuracy: **5/5 — returned 10.11.8 (correct via real exec)**
- TTFT: ~60-90s (hit shell-output timeout in test; clean tool flow)
- Hallucinations: 0 — tried multiple binary paths, kept trying until success
- UX: TUI with shell-style command visibility; great transparency
- Config: ~/.config/opencode/opencode.json defines `coire` provider
  pointing at strip-shim :4002 with pool aliases best/code/mid/fast as
  selectable models
- Notes: visible "$ docker exec ..." trace gives nice operator insight
  but means longer TTFT. Honest failure path (tries alternatives).

## Pending (require more install effort, unclear ROI)
- **omp** — needs source clarification from user
- **omo** — needs source clarification from user
- **Codex CLI** — anthropic-style; install via npm/pip
- **LibreChat** — full web UI, heavier; docker-compose
- **LobeChat** — web UI, lighter; docker

## Tentative ranking (subject to confirmation)
1. **hermes-agent** — fastest (29s), correct, fullest feature stack
   (memory, kanban, voice, agentic skills, Open WebUI integration).
   Recommended default for user-facing chat.
2. **opencode** — correct, transparent shell-style UX, no hallucinations.
   Slower TTFT but ideal for "I want to see what the agent is doing"
   workflows. Strong secondary option.
3. **pi-agent** — fastest to call (already installed) but hallucinates
   factual claims without verifying. Keep for operator/automation
   (where it's currently used) — not promoted to user chat.
4. Others — pending install + test (no clear ROI without user steering).

**ASK USER**:
- Lock hermes-agent as the default user chat? (Open WebUI :3030
  already points there.)
- Keep opencode installed as alternate frontend (~/.opencode/bin/opencode,
  `coire/best` model wired) for transparent-shell workflows?
- Want me to install + test the remaining 5 candidates, or are these
  three enough to make a call?
