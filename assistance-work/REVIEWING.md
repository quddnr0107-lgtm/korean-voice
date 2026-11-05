# Independent review contract

The authoring files remain drafts. Nothing is approved by completeness, length,
successful parsing, a hash, a test, or a synthesis run.

For each assigned shard, read every original row with its full article and any
reference actually used in the explanation. Compare subject, conditions, scope,
numbers, units, inclusive/exclusive boundaries, exceptions, and obligation versus
permission/prohibition. Inspect the actual figure for tables whose OCR order is
ambiguous. A short restatement need not reproduce a whole chapter referenced for
procedure, but it must explain the operative meaning and cannot invent details.

Correct deficient explanations directly in the draft JSONL, retaining the
original author and adding a clear revision note when material. Resolve the
author's specific warnings. Report unresolved source gaps to root; do not
misrepresent a gap as checked. Do not change original corpus text in this pass.

Write one record per actually checked row to `reviews/<same-shard-name>.jsonl`:

```json
{
  "sourceId": "exact identity from rows.jsonl",
  "sourceIndex": 123,
  "status": "approved",
  "author": "root",
  "reviewer": "f1_validation",
  "textSha256": "SHA256 of exact final explanation UTF-8 bytes",
  "originalSha256": "originalSha256 from rows.jsonl",
  "sourceDataSha256": "d5fdeb3cb755635318685b50332012c51cbb3e0ca54ca188ff994b7d7debdd00",
  "unresolvedIssues": [],
  "resolutions": [],
  "note": "Specific review note where useful; otherwise concise scope statement"
}
```

Use `status: "blocked"` and a nonempty unresolvedIssues list if an unresolved
issue prevents approval. The reviewer must differ from the author. Hashing and
formatting records after actual review is mechanical and allowed; a loop cannot
replace reading or create approval for rows the reviewer did not inspect.

For a table group, inspect the complete group and figure, then record each source
identity it covers. Preserve its explicit anchor and member mapping. Do not
merge genuinely different clauses or duplicate occurrences just to reduce count.

When external evidence resolves a warning, add an item in `resolutions` with
`issue`, `evidence` (official URL/local captured source/figure Git blob), and
`result`. Source presentation defects may be tracked separately if the correct
explanation is verified, but missing content used by the explanation is blocking.

Assignments after all drafts:
- f1_validation: root's integrated-defense 4 shards and directives 01–07.
- f1_frontend: f1_backend's military-service-decree 06–15 and f1_validation's
  reserve-act/decree/rule/support-rule 6 shards.
- f1_backend: f1_frontend's military-service-act 01–08, military-service-rule
  01–05, directives 08–11, after compiler work.
- root: f1_validation's military-service-decree 01–05, plus engineering and
  validation of corrections.

Full public generation is blocked until all 6,073 body identities have approved,
current review records and all required presentation/source corrections pass.
