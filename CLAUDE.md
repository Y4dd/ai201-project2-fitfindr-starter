# FitFindr — Project Instructions (CodePath AI201, Project 2)

Multi-tool AI agent that helps users find secondhand clothing and style it.
Graded learning project. **Goal: full marks + at least one stretch feature.**
(Previous project scored 17/25 — this one aims higher.)

**To resume work: read `PROGRESS.md` first.** It holds current milestone, what's
done, and what's next. This file holds the rules that don't change.

## Workflow

- **Design-first.** `planning.md` is filled out before writing code for a thing.
  It is BOTH a graded artifact AND the spec we implement from. Keep it specific.
- **Test each tool in isolation** (`tests/test_tools.py`, pytest) before wiring
  it into the agent. A failure-mode test per tool is required.
- **Collaborative mode**: I draft code + docs and explain every design decision;
  the user makes the key calls (tool logic, prompts, error wording), reviews,
  and tweaks. The user must understand each piece (demo narration + reflection).
- **One milestone per session.** At milestone end: commit → update `PROGRESS.md`
  → user runs `/clear`. Durable state lives in files + git, never only in chat.
- **Stretch phase (after M5):** plan **all** chosen stretch features first — one feature
  per session, `/clear` between, each ending by updating `planning.md` — then implement
  them the same way (one feature per session, `/clear` between, TDD + a failure-mode
  test each), then finish with the M6 README + demo. Same collaborative draft-and-explain
  mode throughout; planning a feature = brainstorm → approved design → write into
  `planning.md` (no implementation code in a planning session).
- Commit per milestone with a clear message.

## Hard constraints (these lose points if they drift)

**Function signatures must match EXACTLY across code, `planning.md`, and `README.md`:**
```python
search_listings(description: str, size: str | None = None, max_price: float | None = None) -> list[dict]
suggest_outfit(new_item: dict, wardrobe: dict) -> str
create_fit_card(outfit: str, new_item: dict) -> str
```
The README's documented inputs/outputs are graded against the real signatures.

- **Implement the tools in `tools.py` directly** — do NOT create a separate file
  per tool (the spec says so explicitly).
- **Use the data loader**, don't re-read files: `load_listings()`,
  `get_example_wardrobe()`, `get_empty_wardrobe()` from `utils/data_loader.py`.
- **LLM**: Groq `llama-3.3-70b-versatile` via `_get_groq_client()` in `tools.py`.
  API key is `GROQ_API_KEY`, already exported in the user's shell env — confirmed
  visible to Python, so **no `.env` file is required**.
- `create_fit_card` must produce **different output for different inputs** — use a
  higher temperature.

## Error-handling rules (no raise / no silent fail / no crash)

| Tool | Failure mode | Required behavior |
|------|--------------|-------------------|
| `search_listings` | no matches | return `[]` (never raise) |
| `suggest_outfit` | empty wardrobe (`wardrobe["items"] == []`) | return useful general-styling string (never crash/empty) |
| `create_fit_card` | empty/whitespace `outfit` | return a descriptive error STRING (never raise) |
| planning loop | `search_listings` returned `[]` | set `session["error"]`, return early — **never** call `suggest_outfit` with empty input |

## Architecture

- `agent.py` — `run_agent(query, wardrobe) -> session dict`. The **session dict is
  the single source of truth** for state across tool calls (query → parsed →
  search_results → selected_item → outfit_suggestion → fit_card → error).
- `app.py` — `handle_query()` calls `run_agent()` and maps the session dict to the
  three Gradio output panels (listing / outfit / fit card).
- Planning loop must be **conditional** (branch on what `search_listings` returns),
  not a fixed call-all-three sequence.

## Deliverables (graded)

- `planning.md` (written before code, updated before each stretch feature)
- `README.md`: tool inventory, planning-loop explanation, state management,
  error handling per tool w/ a real example, spec reflection, AI-usage section (≥2)
- Working code + `tests/test_tools.py` (pytest) + Gradio app
- 3–5 min demo video: full multi-step run, visible state passing, one triggered failure
