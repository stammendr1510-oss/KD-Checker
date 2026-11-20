# Offline acceptance for the PDF analyzer

From the repository root in a configured Python environment, run:

```console
python -m pytest -q tests/test_offline_document_analysis.py
```

The test generates synthetic one-page ВО, Э3, ПЭ3 and СП PDFs and passes their
bytes to the analysis function called by the parser worker. The test also
generates a second Э3 with different content under the same name. The command
requires no HTTP server, external service, deployed Checklist or production data.

| Finding | Expected evidence |
| --- | --- |
| KD-SET-201 | Inventory identifies all four kinds in the upload. |
| KD-SET-203 | СП page 1, row 2 contains a reference absent from the recognized uploads. |
| KD-PE-104 | ПЭ3 page 1, rows 3 and 4 both contain 1KM1. This is an engineering review warning. |
| KD-PE-100 | 1KM1 and XT1 appear in the extracted Э3 text on page 1. |
| KD-SET-103 | With two distinct Э3 candidates, pair analysis is skipped and the independent ПЭ3 warning is retained. |

The test verifies analysis of extractable PDF text and table geometry.
For Windows acceptance, run the same cases through the parser worker and upload
endpoint in an environment that supports loopback. Verify the finding sources,
save an engineer decision, export the report and reopen the run.
