# Checkpoint process

_Reconstructed 2026-07-09 (s68) from `checkpoint_s64/65/66.py`, since the process lived only in session memory. This doc is the canonical description; the per-session scripts are the working artifacts._

## What a checkpoint is
The end-of-session refresh of the Status snapshot at the top of `ISSUES.md`, so the next session orients from truth rather than recollection. It is NOT a CLI command. Each session writes a throwaway script `checkpoint_sNN.py` in the repo root (sNN = session number, e.g. `checkpoint_s68.py`).

## What it edits (the invariant)
Always:
- `Counts` line — "N active (X OPEN · Y DEFERRED) · last shipped sNN (...)".
- `Last shipped (sNN)` bullet — one line summarizing the session's shipped work plus commit hashes.
- `_Last updated_` stamp — date + sNN + a one-line session summary; collapse any duplicate stamp to one.

When issues were resolved this session:
- Move each into the `## Resolved log` (inserted after the header, above the previous session's block) with date, session, and a one-line outcome; the entry leaves the Active section.

## Guardrails (every checkpoint script has these)
- Dry-run by default; `--apply` to write. The dry-run prints the planned lines and writes nothing.
- Idempotency sentinel — a unique string from the new stamp (e.g. "(sNN checkpoint."); if it is already present, abort. Safe to re-run.
- Anchor-count assertions — each anchor substring must appear exactly once before it is replaced; otherwise abort with no changes.
- Timestamped backup — copy `ISSUES.md` to `ISSUES.md.bak-YYYYmmdd-HHMMSS` before writing.
- Post-write readback — reopen and confirm the sentinel and a known token landed; fail loudly if not.
- Runs NO git. The human commits and pushes (the bridge cannot run git — see HELM-051). A checkpoint runs after the session's code is already committed and pushed.

## Session numbering
sNN increments by one each checkpoint (s67 -> s68). The number appears in the script name, the Counts line, the Last-shipped bullet, and the stamp.

## Recipe for the next session
1. Copy the newest `checkpoint_sNN.py` to `checkpoint_s(NN+1).py`.
2. Update the EDITS anchors / new lines and the sentinel for the new session; add Resolved-log moves if anything resolved.
3. Run it (dry-run) and eyeball the planned lines.
4. Run again with `--apply`.
5. `git add` + commit + push (yours).

## Notes
- `checkpoint_s65.py` is the fullest template (Resolved-log + Counts + Last-shipped + stamp); `checkpoint_s66.py` was a lighter Status-only refresh. Use whichever scope the session needs.
- `checkpoint_s68.py` (this session) is Status-only: no code shipped, nothing resolved, so it refreshes Counts / Last-shipped / stamp and touches neither the Resolved log nor any code.
