> **ARCHIVED.** Historical harness comparison that led to opencode+omo. CoireAnsic is now harness-agnostic — see `docs/connect/`.

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
  pointing at the shim front door :4001 with pool aliases best/code/mid/fast as
  selectable models
- Notes: visible "$ docker exec ..." trace gives nice operator insight
  but means longer TTFT. Honest failure path (tries alternatives).

### Codex CLI (@openai/codex 0.131.0) — TESTED @ 22:05 WEST
- Install: 5 (npm install -g @openai/codex)
- Config requirement: `wire_api = "responses"` is now MANDATORY.
  bifrost + strip-shim only speak chat/completions today.
- Verdict: **incompatible with our stack** without writing a Responses
  API adapter for strip-shim. Skipping for this round.

### omp (oh-my-pi v15.1.7, can1357/oh-my-pi) — TESTED @ 23:25 WEST
- Install: 4 (curl-bash one-liner via https://omp.sh/install)
- Tool accuracy: **5/5 — returned 10.11.8.0 directly** via docker exec
- TTFT: fast (sub-30s for tool-heavy query)
- Hallucinations: 0
- UX: TUI + `-p` print mode + `--model` selector
- Config: `~/.omp/agent/models.yml` defines `coire-bifrost` provider
  pointing at the shim front door :4001 with all 7 pool aliases as selectable
  models. `--model coire-bifrost/best` works. Subagent roles
  (`--smol`, `--slow`, `--plan`) can be wired to specific pool aliases
  via the same config (see adapters/omp/models.yml in repo).

### omo (oh-my-openagent dev, code-yeongyu/oh-my-openagent) — TESTED @ 23:30 WEST
- Install: 4 (bunx oh-my-openagent install — Bun required)
- Tool accuracy: **5/5 — returned 10.11.8** via docker inspect label
- TTFT: slower than opencode-direct (full agent stack)
- Hallucinations: 0
- UX: full multi-agent harness (Sisyphus + 9 specialist agents) on top
  of OpenCode TUI. Agents auto-spawn parallel team for complex tasks.
- Config: `~/.config/opencode/oh-my-openagent.json` maps each agent
  + category to one of our 7 pools. Mapping in repo at
  adapters/omo/oh-my-openagent.json:
    sisyphus / atlas / prometheus / metis / oracle → best
    hephaestus / momus → code
    sisyphus-junior → mid
    librarian / explore → fast
    multimodal-looker / category visual-engineering → vision
    category writing → compress
    category quick → fast
    category ultrabrain / deep / artistry → best

## Pending (require more install effort, unclear ROI)
- **LibreChat** — full web UI, heavier; docker-compose
- **LobeChat** — web UI, lighter; docker

## Final ranking (5 frontends tested + multi-agent ones)
1. **hermes-agent + Open WebUI** — fastest (29s), correct, fullest feature
   stack (memory, kanban, voice, agentic skills, web UI on :3030).
   Recommended default for user-facing chat.
2. **omo (multi-agent)** — best for parallel team workflows. 10 specialist
   agents mapped onto our 7 pools (orchestrator→best, code→code, search→fast,
   vision→vision, writing→compress). Slower per-query but massively
   parallel for complex tasks. Best for "build me X" with hyperplan +
   security-research skills.
3. **omp (single-agent)** — clean fork of pi-mono with sessions, subagents,
   slash commands. Fast direct answers, per-role model overrides
   (--smol/--slow/--plan can each point at a different pool).
4. **opencode** — correct, transparent shell-style UX. Solo, no team mode.
   Good middle ground.
5. **pi-agent** — hallucinated jellyfin version. Keep for operator/internal
   only (already wired for op-* timers).

**ASK USER**:
- Lock hermes-agent as the default user chat? (Open WebUI :3030
  already points there.)
- Keep opencode installed as alternate frontend (~/.opencode/bin/opencode,
  `coire/best` model wired) for transparent-shell workflows?
- Want me to install + test the remaining 5 candidates, or are these
  three enough to make a call?
