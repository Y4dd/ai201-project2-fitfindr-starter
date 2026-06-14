# FitFindr — planning.md

> Complete this document before writing any implementation code.
> Your spec and agent diagram are what you'll use to direct AI tools (Claude, Copilot, etc.) to generate your implementation — the more specific they are, the more useful the generated code will be.
> Your planning.md will be reviewed as part of your submission.
> Update it before starting any stretch features.

---

## Tools

List every tool your agent will use. For each tool, fill in all four fields.
You must have at least 3 tools. The three required tools are listed — add any additional tools below them.

### Tool 1: search_listings

**What it does:**
<!-- Describe what this tool does in 1–2 sentences -->

**Input parameters:**
<!-- List each parameter, its type, and what it represents -->
- `description` (str): ...
- `size` (str): ...
- `max_price` (float): ...

**What it returns:**
<!-- Describe the return value — what fields does a result contain? -->

**What happens if it fails or returns nothing:**
<!-- What should the agent do if no listings match? -->

---

### Tool 2: suggest_outfit

**What it does:**
<!-- Describe what this tool does in 1–2 sentences -->

**Input parameters:**
<!-- List each parameter, its type, and what it represents -->
- `new_item` (dict): ...
- `wardrobe` (dict): ...

**What it returns:**
<!-- Describe the return value -->

**What happens if it fails or returns nothing:**
<!-- What should the agent do if the wardrobe is empty or no outfit can be suggested? -->

---

### Tool 3: create_fit_card

**What it does:**
<!-- Describe what this tool does in 1–2 sentences -->

**Input parameters:**
<!-- List each parameter, its type, and what it represents -->
- `outfit` (...): ...

**What it returns:**
<!-- Describe the return value -->

**What happens if it fails or returns nothing:**
<!-- What should the agent do if the outfit data is incomplete? -->

---

### Additional Tools (if any)

<!-- Copy the block above for any tools beyond the required three -->

---

## Planning Loop

**How does your agent decide which tool to call next?**
<!-- Describe the logic your planning loop uses. What does it look at? What conditions change its behavior? How does it know when it's done? -->

---

## State Management

**How does information from one tool get passed to the next?**
<!-- Describe how your agent stores and accesses state within a session. What data is tracked? How is it passed between tool calls? -->

---

## Error Handling

For each tool, describe the specific failure mode you're handling and what the agent does in response.

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| search_listings | No results match the query | |
| suggest_outfit | Wardrobe is empty | |
| create_fit_card | Outfit input is missing or incomplete | |

---

## Architecture

<!-- Draw a diagram of your agent showing how the components connect:
     User input → Planning Loop → Tools (search_listings, suggest_outfit, create_fit_card)
                                                                          ↕
                                                                   State / Session
     Show what triggers each tool, how state flows between them, and where error paths branch off.
     ASCII art, a Mermaid diagram (https://mermaid.js.org/syntax/flowchart.html), or an embedded
     sketch are all fine. You'll share this diagram with an AI tool when asking it to implement
     the planning loop and each individual tool. -->

---

## AI Tool Plan

<!-- For each part of the implementation below, describe:
     - Which AI tool you plan to use (Claude, Copilot, ChatGPT, etc.)
     - What you'll give it as input (which sections of this planning.md, your agent diagram)
     - What you expect it to produce
     - How you'll verify the output matches your spec before moving on

     "I'll use AI to help me code" is not a plan.
     "I'll give Claude my Tool 1 spec (inputs, return value, failure mode) and ask it to implement
     search_listings() using load_listings() from the data loader — then test it against 3 queries
     before trusting it" is a plan. -->

**Milestone 3 — Individual tool implementations:**

**Milestone 4 — Planning loop and state management:**

---

## A Complete Interaction (Step by Step)

**What FitFindr does (in three sentences):** FitFindr takes one natural-language
thrifting request and orchestrates three tools to answer it. The query triggers
`search_listings` (filter by description / size / price); the top match triggers
`suggest_outfit` (style it against the user's wardrobe); that styling triggers
`create_fit_card` (write a shareable caption). If `search_listings` finds nothing the
agent stops and tells the user what to adjust instead of calling the later tools with
empty input; an empty wardrobe makes `suggest_outfit` give general advice; missing
outfit text makes `create_fit_card` return an error string.

**Example user query:** "I'm looking for a vintage graphic tee under $30, size M. I mostly wear baggy jeans and chunky sneakers. What's out there and how would I style it?"

**Step 1 — Search.** `search_listings("vintage graphic tee", size="M", max_price=30.0)`.
Size matching is fuzzy, so `"M"` qualifies `"S/M"`. In our data the only size-M graphic
tee under $30 is **lst_002 — "Y2K Baby Tee — Butterfly Print"** ($18, Depop). The agent
stores the results and sets `selected_item = results[0]`.

**Step 2 — Suggest outfit.** `suggest_outfit(lst_002, wardrobe)`. The wardrobe holds the
user's baggy jeans (w_001) and chunky sneakers (w_007), so the LLM returns a specific
outfit (fitted tee + baggy jeans + chunky sneakers). Stored in `outfit_suggestion`.

**Step 3 — Fit card.** `create_fit_card(suggestion, lst_002)`. With a higher temperature
the LLM returns a casual, shareable caption naming the item, its $18 price, and Depop.
Stored in `fit_card`.

**Final output to user:** Three Gradio panels — the listing, the outfit, the fit card —
from one query, with no re-entry between steps.

**Error path:** An impossible query ("designer ballgown, size XXS, under $5") makes
`search_listings` return `[]`; the agent sets `session["error"]` and returns early,
leaving `outfit_suggestion` and `fit_card` as `None` — `suggest_outfit` is never called
with empty input.
