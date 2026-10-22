---
name: visualize
description: "Add a correct, minimal visual to a lesson — a diagram or geometric picture — that renders inline in the Obsidian log. Use when an idea is genuinely clearer as a picture: a dependency graph, system/flow, sequence, state machine, tree, comparison, or a spatial/geometric thing (coordinate geometry, number line, vectors, a plot, a physical layout). Outsources authoring+rendering to a maker subagent that verifies the image by looking at it, then you embed the returned file."
---

# Visualize

A picture earns its place only when it shows something words can't — shape, structure, direction, relationship, geometry. This skill produces ONE such picture, guarantees it is **correct** (the maker renders it and looks at it before returning), and drops it into the lesson so it renders inline in the Obsidian `md-log` file.

You are the **creative director**. You decide the exact idea and distill it to its fewest carrying elements. A **maker subagent** does the authoring, rendering, visual verification, and saving, then returns a filename. You embed that filename in your reply.

## When to visualize (and when not to)

This teaching system builds a **dependency graph in the learner's head** — axioms at the root, derived facts hanging off them. A visual is powerful exactly when it makes that structure (or a geometry) visible. Reach for one when:

- The idea is a **structure or relationship**: dependencies, a system with parts and arrows, a flow/pipeline, a sequence of exchanges, a state machine, a tree/hierarchy, a comparison, a containment (what's inside vs outside).
- The idea is **spatial or geometric**: coordinate geometry, a number line, vectors, a function's shape, a physical arrangement.

Do NOT visualize when prose or a single equation already carries it. A decorative diagram that just restates the sentence next to it adds noise and a chance to be wrong. When in doubt, don't — a missing visual is cheaper than a false one.

## Choose the maker

Two makers in `agents/`:

- **`mermaid-maker`** — structural/relational visuals: dependency graphs, flowcharts, sequence/state/ER/class diagrams, trees, mindmaps, timelines. This is the default and fits the dependency-graph pedagogy directly.
- **`svg-maker`** — spatial/geometric visuals Mermaid can't lay out: exact coordinates, geometry figures, number lines, vectors, plots, custom shapes.

Rule of thumb: if it's *nodes-and-edges / relationships*, use mermaid-maker. If it's *positions-and-shapes / geometry*, use svg-maker.

## Brief the maker well: one idea, fewest elements

The most common failure is **cramming** — every extra label makes the picture harder to read AND harder to lay out correctly. Before briefing, prune to the fewest elements that carry the idea, and for each ask: *"if I delete this, is the idea still clear?"* If yes, delete it.

Give the maker the concept AND the concrete elements you want — not a vague topic, and not a long checklist.

- BAD: "make a diagram about how TCP works"
- GOOD: "graph TD: a node 'packet' at the top; arrows down to 'ordering' and 'retransmit on loss'; both arrows down into 'reliable stream'. No title. Show that reliability is built FROM packets, not alongside them."

Keep the idea intact but trust the maker to compose; if your brief lists more than ~5–7 elements, cut it first.

## Invoke

Dispatch the maker with the `Agent` tool:

```
Agent(subagent_type: "learn-voice-teacher:mermaid-maker", description: "Make diagram", prompt: "Create a diagram: <your minimal, concrete brief>")
```
or
```
Agent(subagent_type: "learn-voice-teacher:svg-maker", description: "Make figure", prompt: "Draw an SVG: <your minimal, concrete brief>")
```

The maker owns tools — it authors the source, renders it to a PNG, **looks at the PNG and iterates until it is correct and clean**, publishes it into `$OBSIDIAN_VAULT_PATH/viz/` (default `~/Obsidian/learn/viz/`) with a unique filename, and returns:

```
RESULT:
filename: viz-<slug>-<timestamp>.png
path: <vault>/viz/viz-<slug>-<timestamp>.png
```

If it returns `RESULT: NONE`, it couldn't make a correct picture of the brief — simplify or rethink, or decide the visual isn't worth it. Never hand-author or fake a diagram yourself; correctness depends on the maker's render-and-inspect loop.

## Embed it in the lesson

Put the embed directly in your teaching **reply text** (never only in thinking — the learner can't see thinking, and a visual that only appears in thinking will not be rendered). Always place the embed into your visible reply text BEFORE calling `AskUserQuestion`.

Use Obsidian's native wikilink embed syntax with the returned **filename** and a display width, followed by one line giving the absolute path since the terminal can't show images:

```
![[<filename>|500]]
Image: <path returned by the maker>
```

Obsidian natively indexes and renders images from across the entire vault using `![[<filename>|500]]` without path fragility or relative path breakage, and `cite_check` safely preserves wikilinks and embeds while neutralizing citation tokens. Width `|500` is a good default; use larger for dense diagrams. If an embed is placed inside a markdown table cell, escape the width pipe as `\|` (e.g. `![[<filename>\|500]]`) so the table column structure is preserved. Introduce the visual in a sentence, then let it carry the idea — don't narrate every element back in prose.

## Why this is reliable

- The maker never returns a picture it hasn't **looked at**, so "renders fine but says something false" is caught before it reaches the learner.
- PNG embed means **what the maker verified is pixel-identical to what the learner sees** — no re-render drift.
- Unique filenames keep Obsidian's by-filename embed resolution unambiguous.

> The makers render using `mmdc` (Mermaid CLI) for diagrams and `rsvg-convert` (fallback ImageMagick) for SVG. Requires `mmdc` to be installed. You don't render anything yourself — you only brief the maker and embed the filename it returns.
