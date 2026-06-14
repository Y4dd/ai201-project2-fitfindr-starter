# FitFindr — Progress Tracker

> Working memory for resuming across sessions. Read this first, then `CLAUDE.md`.
> Update at the end of every session.

**Due:** Monday 2026-06-15, 2:59 AM EDT.  •  **Today:** 2026-06-13.
**Mode:** Collaborative (I draft + explain, user decides key calls & reviews).

## Current status

- **Milestone 0 (setup): DONE** — `CLAUDE.md`, `PROGRESS.md`, memory note created.
  `GROQ_API_KEY` confirmed visible to Python (no `.env` needed).
- **Milestone 1: DONE** — data explored; "A Complete Interaction" section of
  `planning.md` written (3-sentence summary + 3-step trace + error path).
  Anchor query uses spec's `size="M"`; in our data that yields exactly one match
  (lst_002 Y2K Baby Tee, $18, Depop) — wrote it truthfully rather than faking 3.
- **Milestone 2: DONE** — `planning.md` fully written: 3 typed tool specs (exact
  signatures), conditional planning loop (hybrid parse → branch on empty results →
  early return), state-management table (session **owned by `run_agent`**; tools are
  pure functions that take args / return values, never read the session), specific
  error table, and **Mermaid** architecture diagram with a decision **diamond** for the
  empty-results branch, `listings.json` read only by `search_listings`, and the Groq LLM
  called by the two generative tools. AI Tool Plan (M3+M4) names the exact spec sections.
- **Milestone 3: DONE** — all 3 tools implemented in `tools.py` test-first (TDD), each with
  its required failure-mode test in `tests/test_tools.py` (9 tests, all green).
  `search_listings` is pure/deterministic; the two LLM tools mock the `_get_groq_client`
  boundary so the suite runs offline + deterministic. A live Groq smoke run confirmed:
  populated wardrobe names real pieces (with ids), empty wardrobe gives general advice, and
  `create_fit_card` varies across identical calls (temp 1.0). Tidied `_get_groq_client`'s
  `.env` wording and removed the stub TODO scaffolding from docstrings.
- **NEXT: Milestone 4** — wire the conditional planning loop + session state in `agent.py`
  (`run_agent`), then `handle_query` in `app.py`. Prompt from planning.md's Planning Loop +
  State Management + Architecture sections together. Tools are pure: `run_agent` owns the
  session dict, passes args / stores returns, branches on empty `search_results` and returns
  early (never calls `suggest_outfit` with empty input).

## Milestone checklist

- [x] M0 — Setup & context-management scaffolding
- [x] M1 — Explore data + write "complete interaction" description in planning.md
- [x] M2 — Fill out all of planning.md (specs, loop logic, diagram, AI plan)
- [x] M3 — Implement + isolation-test each tool in tools.py (pytest)
- [ ] M4 — Wire planning loop + state in agent.py; implement handle_query in app.py
- [ ] M5 — Deliberately trigger each failure mode; screenshot one for the demo
- [ ] M6 — README (all sections), run app end-to-end, record 3–5 min demo
- [ ] Stretch — (≥1) pick after core is solid; update planning.md first

## Repo state

- `tools.py`: all 3 tools implemented (search pure; `suggest_outfit`/`create_fit_card` call
  Groq `llama-3.3-70b-versatile`, each wrapped in try/except → graceful fallback strings).
- `tests/test_tools.py` (9 passing) + `pytest.ini` (`pythonpath = .`, testpaths `tests`,
  filters the groq/pydantic-on-3.14 warning). Run with `pytest` from the project root.
- `agent.py` (`run_agent` stub) and `app.py` (`handle_query` stub) still untouched — M4.
- Data + `utils/data_loader.py` in place. `README.md` is still the starter template (full
  README w/ tool signatures is an M6 deliverable; signatures already match planning.md).

## Decisions locked (M2)

- **Query parsing:** hybrid — regex pulls `size` + `max_price`, leftover words become
  `description`; LLM fallback if `description` comes out empty; raw query as last-resort.
- **Size matching:** token match — split listing `size` on `/` and spaces; requested
  size must equal a token. `"M"` fits `S/M`, `M/L`; not `XL`, `US 8`.
- **Search scoring:** keyword-overlap count over
  title+description+style_tags+category+colors+brand; drop score 0; sort score desc,
  tie-break by lower price.
- **Temperatures:** `suggest_outfit` ≈ 0.7, `create_fit_card` ≈ 1.0 (vary across calls).

## Open decisions

- **Which stretch feature(s)?** User wants stretch goals. Candidates:
  price-comparison tool, style-profile memory, trend awareness, retry-with-fallback.
  Decide after core works. (Retry-with-fallback pairs naturally with the loop;
  price-comparison is a clean 4th tool.)

## Notes / gotchas

- Keep function signatures identical across code / planning.md / README (graded).
- README inputs/outputs are checked against real signatures.
- `create_fit_card` needs higher temperature so outputs vary.
- M1 walkthrough was reworded from "the only match" → "the top match is lst_002" so the
  ranked-list scoring (several results, lst_002 ranked #1) doesn't contradict the trace.
- DONE (M3): tidied `_get_groq_client` wording (shell env primary, `.env` optional).
- Test approach for the LLM tools: `tests/test_tools.py` monkeypatches `tools._get_groq_client`
  to a fake client (deterministic/offline). The committed suite therefore does NOT prove the
  live "varies" behavior — that was verified by a one-off live smoke run, not a CI test
  (deliberate, to avoid network flakiness). Reuse this fake-client pattern for agent tests.
