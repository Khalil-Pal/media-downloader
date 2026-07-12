# Tier 2 Conversion Changes

This note documents the Tier 2 conversion work added to Sandy Squirrel's existing converter flow.

## Summary

Tier 2 conversions were added on top of the existing `services/converter.py` and `handlers/convert_handler.py` architecture. The implementation reuses the current conversion session directories, cleanup function, max file size enforcement, per-user active conversion guard, rate limiter, Telegram upload path, Telethon large-file fallback, and `MAX_CONCURRENT_CONVERSIONS` semaphore.

No separate conversion system was added.

## New Conversion Pairs

The bot now supports these Tier 2 pairs:

- PDF to Word (`.docx`)
- PDF to PowerPoint (`.pptx`)
- PDF to Markdown (`.md`)
- PDF to Excel (`.xlsx`) for table-based PDFs
- Word (`.docx`) to PowerPoint (`.pptx`)

Existing Tier 1 pairs remain unchanged.

## User Flow

PDF uploads now show these options in Converter mode:

- `-> Word`
- `-> PowerPoint`
- `-> Markdown`
- `-> Excel`

DOCX uploads now show:

- `-> PDF`
- `-> PowerPoint`

The same `supported_targets()` function drives both the inline keyboard and the Downloader-vs-Converter mode gate, so PDF and DOCX files are recognized through the existing mode-enforcement path.

## Tier 2 Caveats

Tier 2 results include an extra translated caveat in the returned caption. Tier 1 results do not include it.

Generic PDF conversion caveat:

```text
Complex formatting may not convert perfectly.
```

DOCX to PPTX caveat:

```text
This PowerPoint is rebuilt from document headings and paragraphs, so formatting and layout may not match the original.
```

New translation keys were added to:

- `en.json`
- `ar.json`
- `ru.json`

## Implementation Details

### LibreOffice-backed conversions

PDF to DOCX and PDF to PPTX use the existing LibreOffice subprocess pattern through the shared `_convert_with_libreoffice()` helper.

LibreOffice conversions use an isolated per-process profile directory:

```text
-env:UserInstallation=<session>/lo_profile_<uuid>
```

This keeps concurrent LibreOffice jobs from sharing the same profile lock files.

### PDF to Markdown

PDF to Markdown uses `pymupdf4llm` first. If that fails, the converter falls back to basic PyMuPDF text extraction and writes minimal readable Markdown.

The fallback path does not try to reconstruct tables, headings, or layout.

### PDF to Excel

PDF to Excel uses Camelot and writes detected tables into an `.xlsx` workbook with one sheet per usable table.

If no usable tables are found, conversion fails with the translated error:

```text
No tables were found in this PDF, so it can't be converted to Excel.
```

### DOCX to PPTX

DOCX to PPTX is a content restructuring conversion, not a true file-format conversion.

The converter:

1. Parses the DOCX with `python-docx`.
2. Starts a new slide for each `Heading 1` paragraph.
3. Adds body paragraphs under that heading as bullet points.
4. Falls back to chunking plain paragraphs into slides when no `Heading 1` styles exist.

## PDF Text Layer Check

Before PDF-to-DOCX, PDF-to-PPTX, PDF-to-Markdown, or PDF-to-Excel, the converter checks that the PDF has an extractable text layer.

Scanned or image-only PDFs are rejected early with the translated error:

```text
This PDF appears to be scanned or image-based. OCR is not supported, so it can't be converted to this format.
```

OCR is intentionally out of scope.

## Dependencies Added

The following dependencies were added to `requirements.txt`:

- `pymupdf4llm`
- `camelot-py[cv]`
- `openpyxl`
- `python-docx`
- `python-pptx`

LibreOffice was already present in the Docker image for Tier 1, so no new LibreOffice system packages were added.

## Verification Performed Locally

Local syntax and locale checks passed:

```text
python -m py_compile services\converter.py handlers\convert_handler.py
exit=0

json locale check
json ok
```

Local conversion checks that do not require host LibreOffice or Docker passed:

```text
CASE pdf_to_md: exit=0 output=text.md bytes=166 contains_notes=True
CASE pdf_to_xlsx: exit=0 output=table.xlsx sheets=['Table 1'] bytes=4939
CASE docx_to_pptx_headings: exit=0 output=headed.pptx slides=2, bytes=29251
CASE docx_to_pptx_no_headings: exit=0 output=plain.pptx slides=3, bytes=30160
CASE scanned_pdf_reject: exit=1 error=ConversionError:scanned_pdf friendly=conversion_error_pdf_scanned
CASE zero_tables_reject: exit=1 error=ConversionError:no_tables friendly=conversion_error_pdf_no_tables
```

Cleanup was confirmed for those cases:

```text
after_count=5 cleaned=True
```

Mode gate and caveat selection were checked:

```text
pdf gate -> docx, pptx, md, xlsx
docx gate -> pdf, pptx
png tier1 -> jpg, webp

pdf_docx -> conversion_tier2_caveat
pdf_xlsx -> conversion_tier2_caveat
docx_pptx -> conversion_tier2_caveat_docx_pptx
docx_pdf -> None
png_jpg -> None
```

## Verification Blocked In This Environment

Docker was not available on PATH:

```text
docker : The term 'docker' is not recognized...
```

Host LibreOffice was also not available on PATH:

```text
Get-Command soffice ... exit=1
```

Because of that, these requested checks could not be completed here:

- Built Docker image verification
- PDF to DOCX success test
- PDF to PPTX success test
- LibreOffice concurrency test
- Docker image size delta, compressed and uncompressed

