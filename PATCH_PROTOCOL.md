# Patch Protocol — Memecoin Engine

> Required shape for any patch that touches more than one file, modifies decision logic,
> changes an endpoint contract, or adds a new classified/scored field.
> Not required for typo fixes, label-only changes, or obviously isolated single-line edits.

---

## Phase 1 — Integrity

**Confirm the current state of the system before writing any code.**

Must check:
- What does the live endpoint or DB currently return for the affected field/surface?
- Which invariant (see `INVARIANTS.md`) is violated or missing?
- Is the gap confirmed with an actual query/curl output — not assumed?

Must be explicit:
- The exact field, endpoint, or surface that is wrong
- The expected value vs the actual value
- Whether this gap affects multiple surfaces (NBA card, dossier, second-leg, monitor row)

Avoid:
- Starting the build phase based on a mental model of what's wrong
- Assuming a field exists or has the right value without checking live
- Treating the symptom (wrong UI output) as the root cause

> Do not proceed to Build until the gap is confirmed against live system output.

---

## Phase 2 — Build

**Apply the smallest change that closes the confirmed gap.**

Must check:
- All files that contain the affected field or logic (grep for the field name)
- Whether the fix must be applied in multiple places for cross-surface consistency
- Whether any TypeScript interfaces need to match new backend return fields
- Whether `INVARIANTS.md` needs to be updated (if a threshold or rule changes)

Must be explicit:
- The exact file(s), function(s), and approximate line(s) being changed
- Why this is the minimal fix (not a larger refactor)

Avoid:
- Changing ranking formulas, tier thresholds, or behavioral rules as a side effect
- Adding new intelligence layers without planning their validation function
- Touching more surfaces than the confirmed gap requires

---

## Phase 3 — Verify

**Assert that the fix is correct against live system output.**

Must check:
- Run the specific endpoint or DB query that targets the fixed field
- Confirm the exact expected value is now returned (not just "no error")
- If the fix was multi-surface, verify each surface explicitly
- **If this patch introduces a new classified/scored field:** run `GET /api/memecoins/intel-validation` and confirm an entry exists for that field with a sample count (`n`). If no entry exists, the verification fails — add the validation function before this patch is closed. `NOT_YET_PROVEN` is a passing state; no entry is not.

Must be explicit:
- The exact curl/query used
- The exact output that confirms the fix

Avoid:
- Treating a successful service restart as verification
- Treating "it looks right in the UI" as verification
- Closing the patch without a stated assertion that passed
- Shipping a new intelligence field without a validation entry (invisible debt)

> If the assertion fails, return to Phase 1 — do not patch again without re-confirming root cause.

---

## Cross-surface consistency checklist

When a field appears in multiple endpoints, check all of them:

| Field | Endpoints to check |
|-------|--------------------|
| `perf_tier` | `/next-best-action`, `/token-dossier/{symbol}`, `/quiet-market-intel` |
| `trust_label` | `/next-best-action`, `/token-dossier/{symbol}`, research pool rows |
| `triage_state` | `/next-best-action`, `/token-dossier/{symbol}` |
| `correction_tier` | `/token-dossier/{symbol}` |
| Threshold change | `INVARIANTS.md` + all endpoints that use the threshold |

---

## Evaluation gate for new intelligence fields

> The check lives in Phase 3 above. The rule: `NOT_YET_PROVEN` is a valid verdict. No entry in `/intel-validation` at all is not.
