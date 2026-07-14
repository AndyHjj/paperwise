# Layout-preserving bilingual PDF backend

Paperwise now delegates translated-page reconstruction to an unmodified
BabelDOC 0.6.3 executable. BabelDOC produces only the translated mono PDF.
Paperwise then composes each untouched source page with its translated sibling
and reports any detectable fidelity problems alongside the published PDF.

## Install the optional backend

Keep BabelDOC outside the Paperwise Python environment:

```powershell
uv tool install --python 3.12 "BabelDOC==0.6.3"
babeldoc --version
```

The reported version must be exactly `0.6.3`. If the executable is not on
`PATH`, point Paperwise at it:

```powershell
$env:PAPERWISE_BABELDOC_EXECUTABLE = "C:\path\to\babeldoc.exe"
```

Paperwise also detects a project-local isolated runtime at
`.paperwise-runtime/babeldoc-v063`.

The first run downloads layout models and fonts into BabelDOC's user cache. On
the tested Windows machine, a two-page cold run took about 190 seconds and
peaked near 1.6 GB of memory. Warm runs are substantially faster.

## Provider compatibility

The layout backend supports Paperwise's OpenAI-compatible providers: OpenAI,
DeepSeek, Qwen, and MiMo. The active Paperwise model, base URL, and key are
written to a short-lived per-job TOML file rather than exposed in the child
process command line. The file is deleted when the process exits.

BabelDOC 0.6.3 cannot call Paperwise's native Anthropic Messages client. Select
an OpenAI-compatible provider or configure an OpenAI-compatible proxy before
requesting a bilingual PDF.

## Quality checks and publication

A generated PDF is not treated as flawless merely because BabelDOC exits
successfully. Paperwise also checks:

- the exact requested page count and original page dimensions;
- every source image occurrence to retain its decoded content and coordinates;
- numeric tokens, percentages, and citation numbers to remain present;
- no translation-failure placeholders or near-blank translated pages;
- embedded Chinese text no smaller than 5.9 pt;
- source-annotated horizontal URLs to avoid BabelDOC's one-character prefix
  wrapping; cached mono and dual outputs are revalidated before reuse;
- the final left half to render like the original page within a calibrated
  pixel-difference threshold;
- the final right half to render like the verified translated mono page;
- source links to survive on the original half.

Detected quality problems do not stop publication. The CLI prints the complete
warning list. Errors that make a PDF impossible to compose, such as missing
pages or incompatible page geometry, still stop the job and leave the previous
output untouched.

## Known limitations

BabelDOC 0.6.3 is much more faithful than Paperwise's former white-box overlay,
but it is not an unconditional perfect-layout engine:

- its table-text translation switch is retired; tables are normally preserved
  in their original language instead of being translated cell by cell;
- its scanned-PDF workaround is not OCR;
- formula classification, unusual rotations, and complex cross-column
  paragraphs remain heuristic;
- numeric checks are lexical rather than semantic, so equivalent localized
  units such as `500M` and `5亿` can still trigger a warning;
- dense reference pages can retain all content while developing uneven
  vertical spacing or large blank regions;
- missing glyphs and extremely long translations can still fail;
- unannotated plain-text URLs cannot use the source-link-guided repair and may
  still require visual inspection;
- annotations other than ordinary source links are not yet migrated to the new
  two-page canvas.

The fidelity checks are advisory for composable output. Tables, formulas, dense
pages, and unusual CropBox/rotation files still require visual inspection; use
the emitted warning list to identify the pages that need attention.

## License boundary

Paperwise remains MIT and does not import or vendor BabelDOC. BabelDOC is an
optional, separately installed AGPL-3.0 program invoked through its CLI. This
separation is the lowest-coupling engineering option, but it is not legal
advice or an automatic AGPL exemption. Before distributing a combined installer
or operating a public service, review the AGPL obligations and provide the
exact BabelDOC version, license, and corresponding source information.
