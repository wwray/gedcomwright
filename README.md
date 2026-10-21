# gedcomwright

Static family-tree website generator: FTM → GEDCOM → static HTML. See
`BRIEF.md` for the full project brief. (References to `docs/phase0-*.md`
in comments point at the original inspection notes, which describe a
private family tree and aren't included in this repository.)

**Status: Milestones 1 (extract), 1a (lint), 2 (media), 3 (render) and 4
(audit + publish) are done.** The real export's full build passes its own
audit cleanly: 1,379 published / 75 hidden / 3 media withheld, 0 issues
across all six Stage 4 checks.

## Setup

Native Windows, Python 3.12+ (developed and tested against 3.14.7 - the
brief's "3.12+" floor is satisfied, and every dependency below has a
confirmed-working wheel for 3.14).

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
pip install -e .          # installs the `gedcomwright` command
```

Git Bash note: activate with `. .venv/Scripts/activate` instead.

## Config

Everything is driven by `config.yaml` at the repo root. It's gitignored -
start by copying `config.example.yaml` to `config.yaml` and filling in your
own values:

- `paths.gedcom_dir` / `paths.gedcom_file` - either a folder (newest `*.ged`
  wins) or a fixed file. The real GEDCOM and the FTM media folder stay
  **outside the repo** and are never written to.
- `paths.media_dir`, `paths.build_dir`, `paths.overrides_dir`.
- `site.*` - title, domain, base_path (the site is a subpath of a shared
  Apache docroot, e.g. `example.com/family`, not its own vhost),
  contact_email.
- `privacy.*` - `home_person` (the GEDCOM xref, e.g. `@I1@`),
  `min_generation`, and the six rule thresholds (all with BRIEF.md's
  defaults).
- `facts.suppress` - fact types dropped in extract (default `[Race]`).
- `publish.*` - `backend` (`winscp`, the default, or `rsync` - opt-in, see
  below), `host`, `username`, `remote_path`, `private_key_path`, `host_key`
  (`winscp` only), optional `port` (default 22). All optional; `gedcomwright
  publish` errors out clearly, without attempting a connection, until
  every key its chosen backend needs is set. See `docs/deploy-setup.md`.

`overrides/` holds the hand-maintained lists the brief describes
(`living.txt`, `deceased.txt`, `media-allow.txt`, `media-type.txt`) - one
xref per line, `#` for comments. None exist yet; Phase 0 found they
shouldn't be needed given the generation-cutoff rule, but the code reads
them if present.

## How to build (so far)

Only the extract stage exists:

```powershell
gedcomwright extract                    # uses config.yaml in the cwd
gedcomwright extract --config other.yaml
```

Writes:

- `build/data/people.json` - one record per **published** person only
  (name, facts with parsed dates/places/citations/media, parents/spouses/
  children as either a real reference or a "Living [Surname]" placeholder,
  notes, portrait).
- `build/data/sources.json` - every source actually cited by a published
  person (title/author/publ/note, plus a flag for the "Ancestry Family
  Trees" bucket).
- `build/data/media_links.json` - metadata for every media object that
  survived the privacy filter (not yet processed into derivatives - that's
  Stage 2).
- `build/reports/privacy.md` - the privacy report BRIEF.md requires:
  everyone hidden (with group and reason), everyone published as
  undetermined, the centenarian check, and unparseable dates. Gitignored -
  it names people.

```powershell
gedcomwright lint
```

A read-only report of tree problems that affect the site (`build/reports/lint.md`,
gitignored). Fixes are made in FTM, never by this pipeline. Checks: uncited or
undated death facts in recent generations, non-conforming dates, empty/placeholder
surnames, inconsistent nicknames, likely duplicate siblings (a hint list, not a
merge tool), generation-path conflicts, people unconnected to the home person,
biologically-impossible fact ordering (death before birth, parent too young/old,
marriage too young - all "report only", thresholds in `config.yaml`'s `lint:`
section), obituary media possibly attached to the wrong person, and media files on
disk nothing references (excluding byte-identical copies).

On the real export this finds 619 items across 12 checks in about 9 seconds (most
of that is hashing ~2,250 media files for the last check).

BRIEF.md's single "death facts" bullet is implemented as **two separate checks**,
confirmed with Will (8 Oct 2026):
- **"no citation at all", scoped to recent generations** (34) - a large, genuinely
  different backlog of recent deaths nobody has sourced yet. Not what the brief's
  "5" referred to.
- **"cited only to Ancestry Family Trees", tree-wide, not generation-restricted** (4) -
  this is the brief's "5 such facts tree-wide" figure; close enough (the tree has
  grown since 5 Oct) to confirm it's the same check.

```powershell
gedcomwright media                      # processes everything in media_links.json
gedcomwright media --limit 50           # testing only: first 50 items
```

Resolves each referenced file under `paths.media_dir`, classifies it photo/document,
and writes derivatives under `build/site/media/<hash-prefix>/` plus
`build/data/media.json` (id, type, dimensions, caption/description/date, linked
people, portrait flag, derivative paths, zoom tier) and `build/reports/media.md`
(unresolved files, `.htm`/`.doc`/`.docx` - not published in the MVP, per the brief).

- **Image pipeline (pyvips/libvips):** EXIF orientation applied, then converted to
  sRGB and stripped of all metadata (EXIF/GPS/XMP/ICC) on save. Handles greyscale,
  ICC profiles, CMYK, 16-bit and multi-page TIFF (first page only) - all four
  verified via synthetic fixtures (real export has none of these; Phase 0 found
  JPEG/PNG/MPO only). MPO needs no special handling - libvips' plain JPEG loader
  already reads just the primary image.
- **PDFs:** rendered page-by-page via **pypdfium2**, not the brief's originally-named
  PyMuPDF - PyMuPDF is AGPLv3-or-paid-commercial-only; pypdfium2 (BSD-3/Apache-2.0,
  wraps Google's PDFium) does the same job with no copyleft question, confirmed
  against real PDFs in the media folder before switching (decided with Will, 9 Oct
  2026). No PIL/numpy needed - reads the raw bitmap buffer directly. The original PDF
  is never published unless `media.publish_original_pdf: true` (default false, since
  Hard Rule 2 says originals are never published - this is the brief's one
  deliberate, config-gated exception to that, for PDFs specifically).
- **Classification:** override (`overrides/media-type.txt`) > PDF > filename matches
  a source title (Ancestry record image) > citation-only attachment > default photo.
  The brief's first-choice signal, "the FTM category, if the fresh export carries
  it", is dropped rather than left speculative: GEDCOM 5.5.1 has no standard
  media-category tag, and two full inspections of real FTM exports found no custom
  one either - it apparently never reaches the GEDCOM at all (confirmed with Will,
  9 Oct 2026).
- **Zoom tier:** photos get one capped WebP; documents ≥ 4,000px long edge get a DZI
  (OpenSeadragon-ready, 510px WebP tiles); smaller documents get a single large WebP.
- **Incremental:** content-hash filenames plus `build/data/media_cache.json` -
  unchanged source files are never reprocessed; a config/threshold change
  invalidates exactly what it affects.

**Run end-to-end on the full real export**, not just the earlier 100-item sample:
**1,216 of 1,230 processed** (515 photos, 701 documents, 11 PDF pages; 14 correctly
excluded as `.htm`/`.doc`/`.docx`; 0 unresolved), **37,607 files, ~2.05GB**. Confirmed
by eye on the sample batch: a thumbnail, a DZI-tiled newspaper page, and a rendered
PDF death certificate all came out correctly oriented and legible.

**Build output ordering, resolved in Stage 3:** derivatives live in `build/site/media/`;
`render_site()`'s `copy_static()` step only ever wipes and recopies `build/site/static/`
specifically, never the whole `build/site/` tree, so a render never discards Stage 2's
media output.

```powershell
gedcomwright render                     # the whole site: every published person,
                                         # surname index, home, about, 404, search
gedcomwright render --person I1045      # one page only (design-review workflow)
```

Design approved on one person page first (Gordon Wray Crisp, 9 Oct 2026 - see
`docs/` conversation notes), per the brief's "show me one rendered person page as
a design proposal before applying it across the site", then applied everywhere:

- **Every published person** (1,379 on the real export) gets `people/<slug>/index.html` -
  name, the signature lifespan bar, a compact above-the-fold block (portrait +
  Born/Died/Buried + Family, Ancestry/FamilySearch-style - the first design pass put
  too much of this below the fold, per Will's feedback), the full chronological
  timeline, the media gallery (PhotoSwipe for photos, OpenSeadragon in a `<dialog>`
  for documents), and sources grouped by source (the "Ancestry Family Trees" bucket
  separated and labelled, matched on title not id). Citations that cite a specific
  record image show a thumbnail that opens in the same viewers - added per feedback,
  reusing the existing gallery markup/JS rather than building a new component.
- **Surname index** (`surnames/index.html` + `surnames/<key>/index.html` per surname) -
  grouped by **slugified** surname, not the exact string: 11 real pairs in the data
  differ only by case or stray punctuation ("Marshall"/"MARSHALL", "Donaldson"/
  "Donaldson.") and would otherwise silently overwrite each other's page.
- **Home** (`index.html`) - intro copy with real, computed stats (people/date-span),
  the most-populous surnames as "featured" (a defensible default, not hand-picked -
  easy to make configurable if Will wants specific lines instead), a Pagefind search
  box, and the contact invitation.
- **About** (`about/`) - what's published and why, explaining the privacy approach
  in plain terms, plus contact.
- **404** (`404.html`, un-nested - the conventional path a webserver error-document
  directive expects; wiring Apache to actually serve it is a Stage 5 detail).
- **Search**: **Pagefind 1.5.2** (MIT), run as the final build step over the already-
  rendered HTML. Uses the new **Component UI** (`<pagefind-modal-trigger>`/
  `<pagefind-modal>` in the header, `<pagefind-searchbox>` on the home page) -
  confirmed against the *current* docs that 1.5 replaced the old `PagefindUI`/
  `pagefind-ui.js` default, rather than assuming training-data knowledge was still
  accurate. Indexes via `data-pagefind-body` on `<main>` in the shared base
  template, so every page type is covered uniformly with no special-casing.
  Self-hosted (`build/site/pagefind/`), themed via its `--pf-*` CSS variables
  mapped onto the site's own tokens.

Errors are collected per-person rather than aborting the whole batch (one
person's unusual data shouldn't block everyone else's page), but every failure is
printed, never silently dropped.

```powershell
gedcomwright audit
```

Independently re-verifies `build/site/` against `build/data/*.json` and a
fresh privacy classification of the GEDCOM itself - deliberately not just
re-running the same code path that built the site (BRIEF.md Hard Rule 1:
"enforce it in the data stage, then verify it independently with an audit
step that fails the build"). Six checks:

1. every published person still passes a fresh privacy re-check
2. no hidden person's id or page appears anywhere in the output (a
   directory-name check plus a whole-site token scan for each hidden
   bare xref - **not** a name-text scan, since BRIEF.md's Hard Rule 1
   exemption explicitly accepts a living relative's name inside a
   published person's free text)
3. every media file any page references actually exists in `media.json`'s
   manifest
4. every published image (every derivative, plus a one-tile-per-document
   spot-check of DZI tiles) still has no EXIF/XMP/ICC metadata
5. every internal link (every `href`/`src`/`data-tilesource` the templates
   use) resolves to a real file
6. no output file is a byte-identical copy of a source media file (the
   one allowed exception: a PDF's deliberately-copied original, when
   `media.publish_original_pdf` is on)

Exits non-zero and prints the first 10 issues per check if anything fails;
always writes `build/reports/audit.md` (gitignored) with the full list.
Read-only - never touches `build/site/` or `build/data/`.

`build`, wrapping extract+media+render+audit in one command, isn't built
yet - each stage is still run separately.

## How to publish

```powershell
gedcomwright publish                    # dry run: shows what would change, uploads nothing
gedcomwright publish --live             # actually pushes changed files
gedcomwright publish --live --delete    # also removes remote files no longer
                                         # present locally
```

Syncs `build/site/` to the remote docroot over SFTP. Two backends,
selected by `config.yaml`'s `publish.backend`:

- **`winscp`** (default, matches BRIEF.md Stage 5's native-Windows
  recommendation) - WinSCP scripting (`synchronize remote`, `-preview` for
  the dry run). Works with the dedicated SFTP-only, chrooted deploy
  account (below) - the sync protocol never needs a remote shell.
- **`rsync`** (opt-in - Will's choice, 6 Oct 2026, for a simpler and more
  cross-platform option than scripting a Windows GUI tool) - plain `rsync`
  over SSH. **Needs a real shell account, not the chrooted one**: rsync's
  protocol runs a remote command, which an SFTP-only account can never do
  (this is the brief's own stated reason for not using rsync as the
  primary backend). `gedcomwright publish` prints a loud warning every
  time this backend is selected - broader account access is a real,
  ongoing tradeoff, not just a setup-time concern, on a server that also
  hosts other live sites.

Either way, nothing works yet: needs a `publish:` section in `config.yaml`
and either the dedicated SFTP-only chrooted deploy account (`winscp`) or an
appropriately-scoped shell account (`rsync`) set up on the server - neither
exists yet. See `docs/deploy-setup.md` for server-side setup instructions
for both (written, not applied - CLAUDE.md: never run anything against the
VPS without showing Will the exact command and getting a yes first) and
sample Apache config for the `/gene` subpath (swapped in for the brief's
Nginx/Caddy samples, since the real server runs Apache - noted explicitly
in that doc).

`pipeline/publish.py` only ever constructs the exact command for whichever
backend is configured and returns it to the caller; it never decides on its
own to open a connection - `gedcomwright publish` is the only thing that
actually runs it, and only when a human runs that command themselves.

## Post-launch feedback round (6 Oct 2026)

Found and fixed while Will reviewed the manually-copied first build:

- **Pagefind search was broken on the real subpath deployment** -
  `/pagefind/` instead of `/gene/pagefind/`. Pagefind's Component UI
  auto-detects its bundle path from its own script's `src`, but that
  detection can't handle our deliberately root-relative paths
  (`../../pagefind/...` on nested pages) - confirmed against Pagefind's own
  reference doc, not assumed (a first, more casual docs lookup actually
  returned a plausible-looking but fabricated attribute example, caught by
  checking a second, more specific source before using it). Fixed with an
  explicit `<pagefind-config bundle-path="...">` per page, built from
  `site.base_path`.
- **The Timeline silently dropped some dates the vitals block showed** -
  `fact_label()` gated the date suffix on `date.parsed`, but a date like
  `16/07/2020` (slashes aren't a GEDCOM date shape) correctly comes back
  `parsed: False` while still carrying a perfectly usable `display` string.
  Now shown unconditionally whenever there's a `display` value, matching
  `vital_facts()`.
- **Married names** (GEDCOM standard `NAME.TYPE`): a name typed "married"
  now displays as primary, with a differently-surnamed "birth"/"maiden" (or
  untyped first) name bracketed after it as "(née X)"/"(born X)". The real
  export's `NAME.TYPE` is only ever "aka" today, so this changes nothing
  until FTM records a married name - forward-looking, not retroactive.
- **Ancestry-sourced record images are no longer published; citation text
  still is** (reversing BRIEF.md's original "no Ancestry exclusion flag" -
  Will's call, over concern about republishing Ancestry's own scanned
  records). Detected via each source's `PUBL` field containing "ancestry" -
  240 of 269 real sources match. This went through two rounds the same day:
  the first pass (6 Oct) only suppressed the citation thumbnail in
  `group_citations` - but `extract` was never actually rerun afterward, so
  the real site kept shipping the old `sources.json` with no `is_ancestry`
  field at all, and the "fix" had silently done nothing. Will caught this
  by eye the next day (7 Oct), pointing at a screenshot of Ancestry census
  thumbnails still showing. Rerunning extract fixed the display, but
  surfaced a second gap: the same images were *also* reachable through the
  main photo/document gallery (`build_gallery`), which pulls from
  `person["media"]` - a list that includes every citation's media too, not
  just direct attachments - so the same record scan reappeared there even
  with the Sources section fixed. Then Will asked the obvious follow-on
  question: "if we're not going to show the ancestry sources perhaps they
  shouldn't be in the upload?" - right that display-only suppression still
  left the files sitting in `build/site/media/` and in the deploy zip. Now
  `extract` computes `is_ancestry_only` per media item (never a portrait,
  never attached directly, never cited by a non-Ancestry source too, via a
  new `citation_sources` field on `medialink.MediaLink`), and `media.py`
  skips derivative generation for those items entirely - 694 of the real
  export's 1,230 media items. `build/site/media/` dropped from ~2.0GB/37,607
  files to ~0.39GB/1,992 files; the deploy zip from 2.01GB/41,251 files to
  0.40GB/6,194 files. render.py's citation/gallery filtering stays in place
  as a second, independent safeguard even though media.py no longer
  generates the files at all.
- **Multi-page PDF documents are now fully viewable** - previously only
  page 1 of any PDF ever showed, even though every page was processed (3
  of the real export's 7 PDFs have more than one page). The viewer item's
  `pages` list now always carries every page; OpenSeadragon's own
  `sequenceMode` gives built-in prev/next controls.
- **Light/dark toggle** - a header button (sun/moon icon, `static/js/theme.js`),
  persisted via `localStorage`, with an inline `<head>` script that applies
  a saved choice before first paint (no flash of the wrong theme). The
  underlying dark-mode tokens already existed; only the manual override
  and the control itself were missing.
- **Surname listing shows each person's source count** (distinct sources,
  not citations - five citations to one record shows "1 source").
- **A bug introduced and caught in the same session**: Jinja2's `tojson`
  filter is safe inside a `<script>` block but not inside a double-quoted
  HTML attribute - its output isn't HTML-attribute-escaped, so the
  multi-page viewer's `data-pages='...'` attribute was initially written
  double-quoted, which broke on the JSON's own embedded quotes. Confirmed
  with a throwaway Jinja2 script before trusting the fix, then fixed by
  single-quoting that one attribute (safe here specifically because the
  JSON it carries - file paths and "image"/"dzi" kind strings - never
  contains an apostrophe). `pipeline/audit.py`'s link checker also needed
  updating: it only knew the old `data-tilesource` attribute, so it was
  silently checking zero document paths on the broken build and would have
  kept doing so afterward too, had it not been updated to parse the new
  `data-pages` JSON directly. Both caught by direct inspection of the real
  rendered output, not by any test that existed at the time - worth noting,
  since it's exactly the kind of thing a test should catch; both now have
  one (`test_rendered_multi_page_document_has_a_parseable_data_pages_attribute`
  parses real template output through `html.parser`, specifically because
  checking only the Python data shape would not have caught this).
- **Mouse-wheel zoom** (7 Oct 2026): Will liked double-click-to-zoom but
  wanted the wheel to zoom too. The document viewer (OpenSeadragon) already
  does this by default - confirmed in its vendored source
  (`gestureSettingsMouse.scrollToZoom: true`, `cooperativeGestures: false`),
  not assumed, since `gallery.js` never touches either setting. Photos
  (PhotoSwipe) didn't: its default wheel behaviour is to pan, only zooming
  on Ctrl+wheel unless `wheelToZoom: true` is set - confirmed the same way,
  by reading the vendored bundle's wheel-handler code rather than guessing
  at undocumented-in-our-copy option names. Added that one option to the
  `PhotoSwipeLightbox` constructor in `static/js/gallery.js`; no Python
  pipeline or template change needed. Confirmed working by Will on the real
  site (the first report of "doesn't work" turned out to be a stale cached
  `gallery.js` in the browser - static assets here aren't cache-busted, so a
  hard refresh is worth trying first whenever a JS-only change "doesn't
  show up").
- **Duplicate-looking citation lines** (7 Oct 2026, James Wray @I313@):
  genuine duplicate data in FTM itself, not a pipeline bug - confirmed by
  reading the raw GEDCOM subtree, which has two separate `SOUR @S139@`
  citations under one RESI fact, same page text, but two different scanned
  images. It reads as a visible duplicate specifically because the citation
  text is all that's shown (truer than ever now that Ancestry images are
  suppressed); 155 such (source, fact, page) groups exist across 124 people
  tree-wide, common enough to fix in render rather than ask Will to
  hand-edit each one in FTM. `group_citations` now merges citations to the
  same source that would render identically (same fact label, page, link,
  notes) into one line, unioning their `media_items` rather than dropping
  either side's image.
- **"Powered by gedcomwright" footer credit** - plain text, no link (no
  public repo exists yet; Will's choice, 7 Oct 2026, over creating one now).
- **Duplicate-looking Timeline lines** (7 Oct 2026): after fixing the
  citation duplication above, Will asked to double-check the rest of the
  tree for similar dupes. A tree-wide scan found the citation fix itself
  fully clean (0 remaining duplicates across all 1,379 people) and ruled
  out two other possible dupe classes (two different sources sharing one
  title on a page; the same media id appearing twice in one person's
  gallery - 0 found of either). But it turned up a related, separate issue:
  12 people with two facts that render as the literal same Timeline line
  (same label, same place) - the same root cause as the citation bug,
  genuine duplicate data in FTM, confirmed by reading the raw GEDCOM. Susan
  Tupholme (@I329@) has two complete, byte-identical BIRT and DEAT blocks
  directly under one INDI record; Hugh Tupholme and Martha Louise Watts's
  shared marriage is duplicated via an extra FAM record with no spouse link
  at all (same MARR date/place, the duplicate FAM's WIFE tag is simply
  absent). Fixed the same way: a new `_dedupe_facts()` helper merges facts
  that would render identically, unioning their citations and media -
  applied once in `person_context()` so the Timeline, the Sources section
  and the gallery all see the same deduped fact list, rather than patching
  each separately. Deliberately conservative: facts with the same label but
  *different* place text (e.g. two birth records at different levels of
  place detail) are left alone, since merging them could silently drop a
  real distinguishing detail - that's a separate, more judgment-call-laden
  data-quality question, not addressed here.
- **Duplicate/quadruplicate gallery thumbnails** (7 Oct 2026, James Wray Sr.
  @I313@ again): the tree-wide scan above checked for the same media *id*
  appearing twice in a gallery (structurally impossible - `person["media"]`
  is built from a `set()`) and found none, but that wasn't the actual bug.
  Phase 0 already noted 38 pairs of distinct OBJE records sharing
  byte-identical content tree-wide; `media.py`'s content-hash-keyed
  derivative cache already merges those into one file on disk, but
  `build_gallery` still gave each surviving *id* its own gallery entry - so
  the same photo could show two, three, even four times side by side with
  nothing to tell them apart (confirmed on James Wray Sr.'s own page: 7
  distinct images each duplicated, 10 redundant thumbnails; 18 people
  tree-wide, 40 redundant thumbnails total). The citation thumbnails under
  Sources were checked too and are clean (0 found) - each citation's own
  media list is small enough that this collision doesn't occur there.
  Fixed by deduping `build_gallery`'s output on `item["thumbnail"]`, which
  is always a content-hash-derived path for both photo and document types
  - an exact content check, not a guess based on caption or filename.
  James Wray Sr.'s gallery went from 18 images to 8 distinct ones.
- **Render speed** (7 Oct 2026, a self-requested pass before shipping a
  final build, not a bug Will reported): `render_person_page` rebuilt a
  fresh `jinja2.Environment` *and* re-read and re-parsed
  people.json/sources.json/media.json from disk on every single call - on
  the real site, 1,379 times each, even though none of the three ever
  change mid-build. Measured on the real export: full `render` took 184s
  before, 13s after - the Jinja environment is now a module-level
  singleton (built once, reused by every render_* call, including the 280
  surname pages), and the three JSON files are loaded once in
  `render_all_person_pages` and passed down to each person page rather
  than reloaded per person. Output is byte-for-byte the same (263 tests
  still pass; audit still reports 1,379/1,379 rendered, 0 issues) - this
  only removes repeated work, it changes no logic.

## Testing

```powershell
pytest
```

263 tests, all passing: date parsing (every qualifier, boundary years,
every non-conforming shape found in the real export), the GEDCOM parser
(CONC/CONT, cross-references, over-long lines, header encoding), slug
stability, the full privacy classifier (every group and its precedence,
each deceased rule, full vs half vs adopted siblings, the generation rule
including conflicting paths and the home person, all overrides), every
lint check, the media pipeline (path resolution, classification,
orientation/sRGB/metadata-stripping, the CMYK/16-bit/multi-page-TIFF/
greyscale synthetic cases, derivative sizing, the photo/document zoom-tier
split, incremental caching, PDF rendering), the render pipeline (surname
grouping including the case/punctuation-collision merge, vital-facts
selection, relation-summary wording, citation-media resolution, and an
end-to-end `render_site()` run through the synthetic fixture checking
every page type gets written, the HTML stays well-formed, and a hidden
person never gets a page under any name), the audit pipeline (each of the
six Stage 4 checks individually, including a prefix-overlap false-positive
guard for the hidden-id scan and the PDF-entry edge case with no top-level
thumbnail, plus an end-to-end run confirming the real pipeline's own
synthetic-fixture output passes its own audit), publish command
construction for both backends (dry-run vs. `--live` vs. `--delete` flag
handling, config validation including the rsync backend's relaxed
requirements and warning - never actually invoking WinSCP or rsync), and a
person-page-only
end-to-end run over `tests/fixtures/synthetic.ged` (the one GEDCOM file
that's deliberately committed - it's synthetic, not real family data).

## Portability to other genealogy software

This project is explicitly scoped to FTM (the brief's title: "FTM → GEDCOM →
static HTML"), not a generic GEDCOM tool, but it's worth recording precisely how
far that goes, since the core parser is standards-based.

**Genuinely portable** (any GEDCOM 5.5.1-compliant export): the parser itself
(CONC/CONT, cross-references, over-long lines), date parsing (the full ABT/EST/
CAL/BEF/AFT/BET/FROM-TO/INT grammar), and - importantly - the entire privacy
engine (the four groups, the generation-cutoff walk, all thresholds) is built
purely on standard NAME/SEX/BIRT/DEAT/BURI/MARR/FAMC/FAMS/HUSB/WIFE/CHIL/SOUR/
OBJE/NOTE/PLAC tags. None of that cares which program wrote the file.

**FTM-specific, degrades gracefully (doesn't crash, just loses a feature):**
- `_PHOTO` (portrait pick), `_TEXT`/`_DATE` (media caption/date), `_LINK`
  (citation URL), `_MILT` (military fact), `FSID` - all FTM custom tags. Another
  program's export would simply come back without these values; nothing breaks.

**FTM-specific and affects an actual privacy rule, not just display:**
- The full/half/adopted-sibling deceased-publishing rule reads `_FREL`/`_MREL`
  (FTM's own tags). GEDCOM 5.5.1 has a **standard** tag for this -
  `INDI.FAMC.PEDI` (values: birth/adopted/foster/sealing) - which most other
  programs use instead. FTM doesn't emit `PEDI` at all (confirmed: absent from
  every tag inventory in `docs/`), so this pipeline currently can't read it
  either. Against an FTM export this is moot; against anyone else's export, the
  sibling rule would silently never fire (not crash - just under-publish
  compared to what it should). **Cheap, zero-downside fix if it ever matters:**
  add `PEDI` as a fallback alongside `_FREL`/`_MREL` - it wouldn't change a
  single current result, since FTM never emits it. Not done, because the brief
  names `_FREL`/`_MREL` specifically and this is outside its stated scope -
  ask if this should be added.

**Would need real work, not just a fallback:**
- **Encoding:** only UTF-8 (with or without a BOM) is supported; anything else
  (ANSEL - GEDCOM's own historical default charset - or Windows-1252/ANSI, both
  common in older exports from other software) raises `UnsupportedEncoding`
  rather than guessing. Safe, but a real older GEDCOM would be refused outright.
- **Media paths:** `pipeline/mediapath.py` resolves FTM's convention of storing
  an *absolute Windows path* in `FILE`, by finding the media folder's own name
  within it. A program that writes *relative* paths instead (arguably the more
  correct GEDCOM practice, and what e.g. Gramps does by default) would have
  every media file come back "unresolvable" - there's no fallback to "resolve
  relative to media_dir" yet.
- The "Ancestry Family Trees" / "other researchers' trees" bucketing and the
  Ancestry-collection-filename classification heuristic are about this
  specific tree's data (heavy Ancestry sourcing), not about FTM vs. other
  software - they'd simply find no matches on a tree without that sourcing
  pattern, which is harmless but means they wouldn't do anything useful either.

## Known limitations / not done

- **Publishing is built but untested against the real server, for either
  backend.** No `publish:` section exists in `config.yaml` yet, and
  neither the `winscp` backend's SFTP-only deploy account nor the `rsync`
  backend's shell account (`docs/deploy-setup.md`) has been created -
  `gedcomwright publish` currently just errors out cleanly explaining
  what's missing, rather than attempting a connection. `rsync` itself also
  isn't installed on this machine yet (confirmed 6 Oct 2026) - needed only
  if the `rsync` backend is chosen.
- Audit's hidden-id scan and link/manifest checks only recognise the
  attributes templates/ actually uses today (`href`, `src`,
  `data-tilesource`) - a future template change that references a path via
  some other attribute wouldn't be checked unless that attribute is added
  to `audit.LINKED_ATTRS`.
- Audit's EXIF/XMP/ICC check on DZI document tiles is a one-tile spot-check
  per document, not exhaustive (a long document can have hundreds of tiles
  across pyramid levels) - confirmed separately that `dzsave` strips
  metadata by default, so every tile in a given document goes through the
  identical code path as the one checked.
- `config.yaml`'s `site.contact_email` is still a placeholder - every
  contact link on the real site currently points at `contact@example.com`.
  **Needs a real address before this goes live.**
- The home page's intro copy and "featured surnames" selection (most-
  populous, not hand-picked) are a first draft - content Will should read
  and adjust, not treated as final.
- The 404 page is generated (`build/site/404.html`) but not wired to
  anything - making Apache actually serve it on a 404 is a Stage 5
  (publish) server-config detail, not done here.
- Pagefind's `--pf-primary` CSS variable (used to theme the search modal's
  accent colour) is a reasonable guess, not confirmed against Pagefind's
  full variable list - harmless if wrong (unrecognized custom properties
  are simply ignored) but worth eyeballing the rendered modal.
- Multi-page TIFF handling is verified for page-selection (first page
  picked correctly) and for not crashing, but not for exact colour values
  in the synthetic test - libvips' own multi-page-TIFF-*writing* trick used
  to build the test fixture doesn't preserve colour-interpretation tags
  faithfully on reload, independent of this pipeline's own code. Documented
  in `tests/test_media.py`.
- Lint's "likely duplicates" check uses a small, hand-written nickname
  table (Bob/Robert, Doug/Douglas, and about a dozen others) - it's
  explicitly a hint list, not comprehensive, and won't catch every
  nickname pattern in the real data.
- Lint's obituary-misattachment check is a simple name-token overlap
  heuristic; it misses matches where the obituary uses a nickname,
  maiden-name variant or spelling difference the person's own NAME record
  doesn't share. Confirmed false positives on the real export: "Betty
  (Hemmings)" vs the record's "Elizabeth ... /Hemming/", and "Mrs. Anne
  Gowdy" vs the record's "Ann /Small/" (maiden name + spelling) - both the
  same person as the obituary, just not a token match. Not bugs; it's a
  hint list, as the brief says.
- The ID-stability test only covers A→B (determinism); the B→C→D steps
  (one edit, then a revert + FamilySync) were deferred by Will's choice -
  see `docs/phase0-remaining.md`.
- Whether the near-absence of person notes is real was confirmed (yes,
  real) via the FTM export dialog, not from the GEDCOM alone - see the same
  doc.
- `pipeline.privacy._is_ftm_private` (the `RESN`/`_PRIV` check for "marked
  private in FTM") is untested against real data, because the current
  export carries no such marker. Covered by the synthetic fixture only.
- The CLI isn't a properly packaged console script in the "pip install from
  PyPI" sense - `pip install -e .` registers `gedcomwright` in the venv,
  which is all that's needed here, but it hasn't been built as a
  distributable package.
- `facts.suppress` matching is case-insensitive on the EVEN TYPE label
  only; it doesn't suppress a whole tag (e.g. there's no way to suppress
  all `RESI` facts, only a named `EVEN` sub-type like "Race").
- Multiple `NAME` records (19 people in the real export) always use the
  first one as primary; the rest are kept as `aka` but nothing currently
  reads `NAME.TYPE` to prefer a specific one.
- Windows-native testing note (from the brief): run `pytest` yourself at
  each milestone as a sanity check, same as Phase 0.
