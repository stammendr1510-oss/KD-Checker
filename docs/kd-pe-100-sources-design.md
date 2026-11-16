# Structured sources for KD-PE-100

KD-PE-100 reports a sample of matches between the extracted Э3 text and
designations in ПЭ3. Keep its severity, code, file, location, message and
recommendation intact. Matching text supports an informational finding about
the extracted content. Engineering completeness and connectivity require
separate evidence.

## Proposed representation

Add an opt-in, versioned representation of the run with a finding-level
`evidence` object. Keep the existing default response, exports and comparison
identity unchanged. Example with page and row evidence only:

```json
{
  "evidence": {
    "schema_version": 1,
    "kind": "pdf_text_match",
    "truncated": false,
    "matches": [
      {
        "designation": "1KM1",
        "scheme": [
          {"document": "ABCD.123456.001.Э3.pdf", "pdf_page": 1}
        ],
        "element_list": [
          {"document": "ABCD.123456.001.ПЭ3.pdf", "pdf_page": 1, "table_row": 3},
          {"document": "ABCD.123456.001.ПЭ3.pdf", "pdf_page": 1, "table_row": 4}
        ]
      }
    ]
  }
}
```

Use the display filename and the run-scoped source identity when the run has
one. PDF page numbers start at 1 and refer to physical pages. Include
`table_row` only where the extractor confirms the source row. Optionally attach
`boxes_pt` to each occurrence. Each box has four finite numbers
`[x0, y0, x1, y1]` in unrotated PyMuPDF page coordinates, in points from the
top left. The PDF viewer applies crop and rotation transforms when placing
an overlay. Use one box per confidently located text fragment. Omit boxes
when the extractor cannot attribute the exact matched text to those fragments.

The legacy finding provides Э3 page-level matches from `scheme_designations`.
Word rectangles are available from the extractor, but retaining a bounding
box requires mapping the same accepted match to its contributing words.
Reconstructed labels need provenance for each fragment. A whole word that
contains the match may be used as a highlight only when the match can be
located unambiguously inside that word. Retain page-only evidence otherwise.
For ПЭ3 retain the confirmed row and the designation cell's contributing
words. A designation expanded from a range can cite the confirmed cell and
row. A rectangle for a particular expanded member requires separate literal
text evidence.

## Compatibility and validation

- Introduce a versioned worker response only when both parent and child support
  it. The current parent validates exact keys, so sending an added field to a
  v1 parent would reject the entire result. Keep v1 as the default during rollout.
- Persist evidence with the run and stable finding position, if implemented.
  Old runs without evidence stay readable. Never reconstruct historical coordinates
  from a newer upload. CSV/XLSX and existing comparison keys keep their current
  behavior. The opt-in response omits evidence when it is unavailable.
- Validate that source references belong to the run and its access scope.
  Require positive page and row numbers and finite, ordered rectangles inside
  the recorded page bounds. Limit samples to five designations, three locations
  per document and four boxes per location. Sort and deduplicate before limiting.
  Report truncation when locations are omitted.
- Emit evidence only for an unambiguous matched pair. Keep the existing
  informational ambiguity result when multiple Э3 or ПЭ3 candidates qualify.

## Worker contract

`run_check` defaults to result version 1. The parent sends no version field
and the worker emits only `status` and `issues` at the top level. Explicit
`result_version=2` adds `result_version: 2` to both the manifest and response.
The parent requires the requested version and rejects unsolicited evidence or
a mismatched reply. An older worker can still serve v1. Select v2 only when the
worker supports it.

Version 2 can include `evidence` on informational KD-PE-100 findings with
matched designations. Evidence `schema_version` is 1. It also carries a
`truncated` boolean. Locations with `boxes_pt` carry `page_size_pt`
as `[width, height]` in the same coordinate space. The parent checks numeric
bounds against the reported page dimensions and ties source names to the
submitted pair. It does not reopen documents to re-derive geometry.

This implementation captures rectangles for directly located full words in
Э3 and literal single-word ПЭ3 cells with confirmed geometry. Wrapped labels
and expanded ranges can retain page and confirmed row evidence without boxes.
The HTTP backend still requests v1. Exposing an opt-in run representation and
source navigation is a separate step.
