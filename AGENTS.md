# AGENTS.md

- Write simple, readable, maintainable code.
- Use clear, precise names.
- Keep functions small and focused.
- Keep files cohesive and reasonably sized.
- Avoid duplication, dead code, and premature abstractions.
- Separate data loading, API, and frontend responsibilities.
- Use type hints and explicit data contracts.
- Validate inputs at boundaries and return clear errors.
- Install required dependencies when needed and record them in project manifests.
- Add meaningful tests for public behavior and edge cases.
- Run tests, formatting, linting, and type checks before completion.
- Keep README setup and usage instructions accurate.
- Preserve existing user changes and avoid destructive operations.
- Try not to leave any erros or warnings as much as possible

Use clear, domain-specific names. Avoid vague names such as data, item, manager, helper, or utils when a precise name is available.

Keep functions small and cohesive, with one clear responsibility. Extract code when a function mixes validation, transformation, persistence, transport, or presentation concerns.

Keep modules focused. Split a file when it contains unrelated responsibilities or becomes difficult to review; do not split code merely to satisfy an arbitrary line count.

Make data flow explicit. Prefer simple composition and plain typed records over hidden mutable state or deep inheritance.

Use Python type hints for public functions, boundaries, and domain models. Use TypeScript, or checked JavaScript with documented shapes, for browser-facing data.

Define stable contracts at boundaries: file input, normalized bars, API responses, and chart input. Keep transport schemas separate from internal models where doing so prevents coupling.

Validate assumptions at boundaries and return actionable errors. Do not silently repair malformed market data.

Document non-obvious decisions and invariants, not what the code already says.

Avoid duplication, compatibility aliases, dead code, speculative configuration, and premature optimization.
