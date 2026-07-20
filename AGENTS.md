# FareScope Repository Instructions

## Source of truth

- `docs/PROJECT_PLAN.md` is the authoritative living plan.
- Every implementation change must update its checklist and progress log in the same commit.
- Never mark a feature implemented until its data source, persistence path, API, UI, and tests are all accounted for.

## Data claims

- Separate `verified`, `needs verification`, `history dependent`, and `blocked` capabilities.
- Preserve a small redacted fixture for every supported upstream response shape.
- Do not claim that a field is available because it appears in product copy or an idea list.
- Collection frequency must be bounded, deduplicated, and observable.

## Architecture

- Keep the backend as a modular monolith. API, scheduler, collector, analysis, and notification run as separate processes but share one domain package.
- Isolate provider-specific behavior behind collector adapters.
- Store timestamps in UTC and money in integer minor units with the original currency.
- Do not store browser profiles, authentication cookies, raw secrets, or unredacted sensitive payloads in Git.

## Frontend

- The frontend derives from `frontend-template` and keeps its Vite, React, TypeScript, Tailwind, shadcn/Radix, Lucide, and Recharts conventions.
- Reuse existing layout and UI components before adding new components.
- Use semantic design tokens and keep operational screens dense, quiet, and scan-friendly.

## Shell commands

- Execute shell commands through `shnote` with concise `--what` and `--why` text.
- File-only previews with `cat`, `head`, `tail`, `sed -n`, or `nl -ba` may run directly.
