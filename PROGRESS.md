# FitFindr — Progress Tracker

> Working memory for resuming across sessions. Read this first, then `CLAUDE.md`.
> Update at the end of every session.

**Due:** Monday 2026-06-15, 2:59 AM EDT.  •  **Today:** 2026-06-14.
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
- **Milestone 4: DONE** — `run_agent` (the conditional planning loop) wired in `agent.py`
  and `handle_query` in `app.py`, both built test-first. `run_agent` owns the session dict,
  passes args to the pure tools, stores returns, **branches on empty `search_results`** and
  returns early (the crown-jewel test monkeypatches `suggest_outfit`/`create_fit_card` to
  explode, proving they're never reached on a no-match). Parser is the planning.md hybrid:
  tier-1 regex made robust to word-sizes (`Medium`→`M`) and price variants (`$30`/`30$`/
  `30 dollars`) so the common cases are deterministic; tier-2 LLM net (gated on empty
  description) + tier-3 raw-query fallback. New suites `tests/test_agent.py` (9) +
  `tests/test_app.py` (4) reuse the fake-`_get_groq_client` pattern; **full suite 22 green**.
  Live smoke run confirmed: happy path fills selected_item→outfit→fit_card (outfit names real
  wardrobe ids), no-results returns the planning.md error verbatim, Gradio interface builds.
- **Milestone 5: DONE** — deliberately triggered all three documented failure modes
  directly against the tools and captured them in `milestone5_tests.png`:
  `search_listings("designer ballgown", "XXS", 5)` → `[]`;
  `suggest_outfit(item, get_empty_wardrobe())` → general-styling string (no named pieces,
  no crash); `create_fit_card("", item)` → the "⚠️ No outfit to write up yet…" error
  string. Referenced from a new "Failure-mode verification (Milestone 5)" subsection
  under Error Handling in `planning.md`.
- **Core (M0–M5): DONE.** Remaining work reorganized (per user) into a **stretch phase**,
  then the demo. New workflow: plan **all four** stretch features first — one feature per
  session, `/clear` between, each updating `planning.md` — then implement them the same
  way (one per session, `/clear` between, TDD + a failure-mode test each), then finish
  with **M6** (full README + run app + 3–5 min demo).
- **Stretch features chosen (all four).** Recommended order = lowest-risk / highest-value
  first, so a time crunch still lands the strongest ones (deadline is **tomorrow,
  Mon 2026-06-15 2:59 AM EDT**): (1) **retry logic w/ fallback**, (2) **price-comparison
  tool**, (3) **style-profile memory**, (4) **trend awareness**.
- **SP1 (plan retry-logic-with-fallback): DONE** — design approved + written into
  `planning.md` (7 spots): Additional Tools (Stretch-1 entry + helper signature
  `_search_with_fallback(...) -> tuple[list[dict], str | None]`), Planning Loop §3–4 (the
  ordered ladder: attempt 0 → drop size → drop size+price, skip no-ops, first hit wins),
  State Management (new `retry_note` field + app banner), Error Handling (updated
  `search_listings` row + new retry row), Architecture (diagram + prose), AI Tool Plan
  (SI1 entry), and the walkthrough error path. **Decisions locked:** ordered ladder
  size→price (description never dropped); note *names the exact tradeoff* (dropped
  filter(s) + item's real size/price); lives in `run_agent`, tools stay pure; invariant
  *never call `suggest_outfit` on empty input* preserved. Diagram intentionally draws the
  canonical both-set ladder; the no-size/price-only edge case is covered in prose.
- **SP2 (plan price-comparison tool): DONE** — design approved + written into `planning.md`
  (8 spots): a full **Tool 4: compare_price** spec block under Additional Tools, Planning
  Loop step 5, State Management (`price_check` field), Error Handling row, Architecture
  (diagram node + Stretch-2 prose), AI Tool Plan (SI2 entry), and the walkthrough (Step 1b +
  4-panel / 4-tool updates). **Decisions locked:** signature
  `compare_price(new_item: dict, comparables: list[dict]) -> dict` — **pure & deterministic**
  (no LLM, no filesystem; the *agent* passes `load_listings()`, the tool self-selects
  same-category peers, excluding the item by `id`, so the single-data-reader invariant holds).
  Banding by **percentile of peers** (≤25th great_deal · 25–75 fair · >75 high); median +
  count cited in the verdict. Returns a 6-key dict `{band, verdict, price, median,
  n_comparables, category}`. Failure mode = **<3 comparables → `insufficient_data`** (median
  `None`, never raises), reliably triggered by any **accessories** item (2 peers). Runs as an
  unconditional, non-branching step after a successful search; surfaces in a **dedicated 4th
  "Price check" Gradio panel**. Anchor numbers verified against the dataset (lst_002 tee $18 →
  great_deal vs $21.50 median, 14 comparables).
- **NEXT: SP3 — plan style-profile memory.** New session after `/clear`; same brainstorm →
  approved design → write into `planning.md` flow. (SP4 = trend awareness still open — see the
  trend-source note under Open decisions.)

## Milestone checklist

- [x] M0 — Setup & context-management scaffolding
- [x] M1 — Explore data + write "complete interaction" description in planning.md
- [x] M2 — Fill out all of planning.md (specs, loop logic, diagram, AI plan)
- [x] M3 — Implement + isolation-test each tool in tools.py (pytest)
- [x] M4 — Wire planning loop + state in agent.py; implement handle_query in app.py
- [x] M5 — Deliberately trigger each failure mode; screenshot one for the demo

**Stretch — PLANNING phase** (one feature per session, `/clear` between; output = updated `planning.md`):
- [x] SP1 — Plan retry logic w/ fallback
- [x] SP2 — Plan price-comparison tool
- [ ] SP3 — Plan style-profile memory
- [ ] SP4 — Plan trend awareness

**Stretch — IMPLEMENTATION phase** (one feature per session, `/clear` between; TDD + failure-mode test each):
- [ ] SI1 — Implement retry logic w/ fallback
- [ ] SI2 — Implement price-comparison tool
- [ ] SI3 — Implement style-profile memory
- [ ] SI4 — Implement trend awareness

- [ ] M6 — README (all sections incl. new tools), run app end-to-end, record 3–5 min demo

## Repo state

- `tools.py`: all 3 tools implemented (search pure; `suggest_outfit`/`create_fit_card` call
  Groq `llama-3.3-70b-versatile`, each wrapped in try/except → graceful fallback strings).
- `tests/` (22 passing): `test_tools.py` (9) + `test_agent.py` (9) + `test_app.py` (4),
  all mocking `tools._get_groq_client`. `pytest.ini` (`pythonpath = .`, testpaths `tests`,
  filters the groq/pydantic-on-3.14 warning). Run with `pytest` from the project root.
- `agent.py`: `run_agent` (conditional loop) + `_parse_query` / `_llm_parse_query` /
  `_no_results_message` helpers done. `app.py`: `handle_query` + `_format_listing` done.
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

- **Which stretch feature(s)? — RESOLVED (2026-06-14):** all four, in this order —
  retry-logic-with-fallback, price-comparison tool, style-profile memory, trend awareness.
  Plan-all-then-implement workflow (see Current status). Order is risk/value-ranked so a
  time crunch still lands the best ones.
- **Trend awareness source (open, revisit in SP4):** the spec says "a public fashion
  platform," but the project is meant to run offline with no new accounts. Likely resolve
  by deriving "trending" from the dataset itself (e.g. most common `style_tags` in the
  user's size) or a small local mock — decide during SP4 so it stays testable.

## Notes / gotchas

- groq+pydantic-v1-on-py3.14 `UserWarning` is silenced in TWO places: `pytest.ini`
  (test runs) and a `warnings.filterwarnings(...)` in `tools.py` before `from groq import Groq`
  (app/agent runs). Harmless third-party compat notice; API calls work fine.
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
