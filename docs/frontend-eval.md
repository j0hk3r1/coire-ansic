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

## Pending (require installation)
- **opencode** (sst/opencode TUI) — Go binary, ~20MB. Should be quick.
- **omp** — needs source clarification from user
- **omo** — needs source clarification from user
- **Codex CLI** — anthropic-style; install via npm/pip
- **LibreChat** — full web UI, heavier; docker-compose
- **LobeChat** — web UI, lighter; docker

## Tentative ranking (subject to confirmation)
1. **hermes-agent** — best in this round (correct answer, full stack, already
   running). Recommended default.
2. **pi-agent** — secondary; reliable for operator/automation tasks but
   hallucinates more on factual queries.
3. Others — pending install + test.

**ASK USER**: lock in hermes-agent as the official user-chat frontend?
Or keep both (hermes for chat, pi-agent for operator)? Or push install
remaining candidates first?
