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
- **NEXT: Milestone 3** — implement + isolation-test each tool in `tools.py` (pytest).
  Prompt one tool at a time from its planning.md block; one required failure-mode test
  per tool; verify `create_fit_card` output varies across calls (raise temp if not).

## Milestone checklist

- [x] M0 — Setup & context-management scaffolding
- [x] M1 — Explore data + write "complete interaction" description in planning.md
- [x] M2 — Fill out all of planning.md (specs, loop logic, diagram, AI plan)
- [ ] M3 — Implement + isolation-test each tool in tools.py (pytest)
- [ ] M4 — Wire planning loop + state in agent.py; implement handle_query in app.py
- [ ] M5 — Deliberately trigger each failure mode; screenshot one for the demo
- [ ] M6 — README (all sections), run app end-to-end, record 3–5 min demo
- [ ] Stretch — (≥1) pick after core is solid; update planning.md first

## Repo state

- Starter stubs only: `tools.py` (3 stubs return `[]`/`""`), `agent.py`
  (`run_agent` returns "not implemented"), `app.py` (`handle_query` stub).
- Data + `utils/data_loader.py` in place. `planning.md` / `README.md` are templates.

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
- `tools.py` already imports/calls `load_dotenv()` and `_get_groq_client`'s error string
  mentions a `.env` file. `GROQ_API_KEY` lives in the shell env, so this is harmless
  (load_dotenv is a no-op with no `.env`) — tidy that wording in M3 if convenient.
