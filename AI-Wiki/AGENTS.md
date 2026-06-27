# Codex Wiki Instructions

This folder is an Obsidian-compatible knowledge base maintained by Codex.

## Layout

- `Projects/Monitorize/raw/`: original source material. Do not rewrite these files during normal wiki maintenance.
- `Projects/Monitorize/wiki/`: Codex-generated project knowledge.

## Maintenance Rules

- Use Markdown files that render cleanly in Obsidian.
- Use wiki links for internal references, for example `[[overview]]` and `[[architecture]]`.
- Keep `index.md` as the entry point and catalog of pages.
- Keep `log.md` as an append-only chronological history.
- When a source changes the understanding of the project, update all affected pages instead of only adding a new note.
- If new information contradicts old information, call that out in the relevant page and record it in `log.md`.
- Prefer concise factual notes over long summaries.
- Include source references when the information came from repo docs or raw source files.

## Query Workflow

When answering questions from this wiki:

1. Read `Projects/Monitorize/wiki/index.md`.
2. Read the listed pages that match the question.
3. Search the repository or raw sources only if the wiki is incomplete or stale.
4. If the answer produces durable project knowledge, file it back into the wiki.
