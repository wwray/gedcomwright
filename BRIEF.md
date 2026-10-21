# gedcomwright: project brief v2 (FTM → GEDCOM → static HTML)

*Project name: **gedcomwright**. A wright is a maker, and it's also a nod to "write". The CLI entry point is `gedcomwright` (e.g. `gedcomwright build`, `gedcomwright publish --live`).*

v2, 5 October 2026. This revises v1 after the Phase 0 inspection, and is written to be worked in **Claude Code**. Save it in the repo root as `BRIEF.md`, with `docs/phase0-report.md` beside it. Then put one line in `CLAUDE.md`: "Read BRIEF.md and docs/phase0-report.md before doing anything."

**Changes from v1**
- The Ancestry-sourced media exclusion is removed.
- Hard Rule 1 is reworded so that names inside published people's text are accepted.
- An environment section for Claude Code is added.
- Phase 0 is split into done and remaining.
- One privacy decision is still open (§Privacy, "Undetermined").
- Phase 0 proposals 5–10 are folded in.

## Read this first: how I want you to work

- **Finish Phase 0 (the remaining items below) and get my approval before writing the pipeline.**
- Work in the phases below, in order. At the end of each phase, stop and report back to me.
- **Tell me plainly and up front when something is partial, stubbed, untested or skipped.** Never describe work as complete when it isn't. Separate "done and tested" from "done but untested" from "not done".
- If you aren't sure about something (a GEDCOM tag's meaning, a library's behaviour, a version), say so and check rather than guess.
- Ask before making architectural decisions not covered here. Ask before adding dependencies beyond those listed.
- Push back if you think part of this brief is wrong. I'd rather hear it than have it built badly.
- Keep a `README.md` current: setup, config, how to build, how to publish, known limitations.
- Stay in scope. The "Later" list at the end is explicitly out of scope for the MVP.

## Environment (Claude Code)

- **Recommended: Claude Code running natively on Windows, with Git for Windows installed.** Claude Code's Bash tool is then **Git Bash**, so commands look like Linux, but Python, pyvips, PyMuPDF, Pagefind and WinSCP are the **Windows** builds. Claude Code can run and test the real pipeline itself. That's the main reason to prefer this over WSL.
  - Git Bash quirks to expect: paths like `/c/Users/<you>/...`, the venv activates from `.venv/Scripts/activate` (not `bin/`), and CRLF line endings.
  - In Python code, use `pathlib` throughout and never hard-code separators.
- **Alternative: WSL 2.** This gives a real Linux toolchain and Claude Code sandboxing, but reading the media folder through `/mnt/c` is slow. If WSL is chosen:
  - mirror the media folder into the WSL filesystem with `rsync` before each build (one-way, read-only on the Windows side);
  - publish with `lftp` (`mirror -R` over `sftp://`) instead of WinSCP. Plain `rsync` needs a shell on the server, which the SFTP-only chroot account won't have.
- **Python 3.12+** in a venv, with a pinned `requirements.txt`.
- Keep the code portable: nothing should depend on which of the two set-ups is used, except the publish backend.
- Protect the FTM folder and the input folder from writes with Claude Code permission deny rules in `.claude/settings.json`. Check the current docs for path-rule syntax on Windows rather than guessing.

## Context

- My family tree is maintained in **Family Tree Maker (FTM)**, which stays the master copy. This project never edits FTM data. It only reads a GEDCOM export.
- **Goal:** a public, modern-looking static website of my family tree, including photos and document scans. Its main purpose is that **relatives can find it by searching ancestors' names and then contact me.** An earlier site of mine (PhpGedView) did exactly that years ago, and I want that capability back.
- **Hosting:** my Linux VPS, which already runs WordPress. The tree site gets its **own virtual host and document root**. Do not touch the WordPress install, its database or its config.
- Output is pushed to the VPS over **SFTP**.
- I'm an experienced IT professional and programmer. You don't need to explain basics, but do explain trade-offs.

## Decisions already made

- **Build our own** rather than use webtrees, TNG, Gramps Web, Betty, ged2site or GedSite.
- **Static output**, because static HTML doesn't need patching and will still work in 20 years.
- **Python + Jinja2**, with no JS framework.
- **Publish as many deceased people as possible.**
- **Media is a first-class feature:** web-optimized derivatives, plus a zoom/pan pop-out viewer with deep zoom for documents.
- **Record images from Ancestry-published sources are not published; citation text is.** Reversed from the original "no Ancestry exclusion flag" (owner decision, 6 Oct 2026, over concern about republishing Ancestry's own scanned record images). Detected via each source's GEDCOM `PUBL` field containing "ancestry" (240 of 269 real sources - the rest are FamilySearch, Find A Grave, state archives, or direct unpublished citations, all untouched). A media item is excluded only when *every* path that reaches it is an Ancestry citation - never a portrait, never attached directly to a person/fact, never cited by a non-Ancestry source too. Citation text (source title/author/page/link/notes) is unaffected; neither is a profile photo, which is never attached via a source citation in the first place. Originally (6 Oct) this only hid the thumbnail on render; as of 7 Oct 2026 (Will: "if we're not going to show the ancestry sources perhaps they shouldn't be in the upload") the derivative files for these 694 of 1,230 real media items are never generated or shipped at all - caught because a stale `build/data/` meant the first fix hadn't actually taken effect on the real site yet, which is how he noticed the thumbnails were still showing.
- **Obituaries, notes and captions of published people are published as-is**, even when they name living relatives. They're public by nature (owner decision, 5 Oct 2026).

## Inputs

- **GEDCOM:** an FTM export with a dated filename. Either confirm a fixed filename, or have config point at a folder and take the newest `.ged` in it.
  - Keep this folder **outside the repo and outside Google Drive**. It contains living people's details.
  - Same filename every time, overwritten on each export. Read in place, read-only.
  - FTM export settings, **verified against the real dialog 5 Oct 2026** (FTM 2024/25's
    Export dialog, "Individuals to export: Entire file", output format "Family Tree
    Maker 2024"):
    - **Entire file**
    - Output format **Family Tree Maker 2024** (writes GEDCOM 5.5.1, UTF-8 under the hood)
    - **"Privatize living people" unticked**
    - Under **Include**, all boxes **ticked**: Tasks, Private facts, Private notes,
      Charts/reports/books, Media files, Private media.
      *(This corrects the original guess below it would be "unticked" — these
      checkboxes mean "include", not "exclude", so ticking them is what puts
      everyone's facts and notes into the GEDCOM for our own privacy stage to
      classify. Unticking them would have silently dropped private data, including
      any Death fact marked private, before the pipeline ever saw it.)*
    - **"Include only items linked to selected individuals" ticked** — this is what
      keeps unattached media/source records out of the export. Leave it ticked for
      the real export; it was deliberately unticked once, for the `idtest` B export,
      to test unattached-record handling.
- **FTM media folder:** the tree's media folder under `Documents\Family Tree Maker\` (FTM's folder names can contain double spaces). The GEDCOM references media by absolute Windows path under exactly this prefix. Read-only.
- **Repo:** the GEDCOM sits outside the repo; keep it that way.
- Site domain: `<DOMAIN>`
- Contact email: `<EMAIL>`

## Hard rules (non-negotiable)

1. **No living person is published.** No page, and none of their facts, dates, places, notes or media, appears in the JSON or the output. In family listings of published people they appear only as "Living [Surname]" with no link, or are omitted. The export is deliberately **not** privatized, so this pipeline is the only safeguard. Enforce it in the data stage, then verify it independently with an audit step that fails the build.
   - *Accepted exception:* a living person's name may appear inside the free text of a published person (an obituary, note, caption or document image).
2. **Original media files are never published.** Only processed derivatives.
3. **Only media referenced by published people is copied.** Never copy the whole media folder.
4. **All published images have metadata stripped** (EXIF, GPS, XMP), after orientation is applied. At least 84 source images carry GPS.
5. **Output is fully static and self-contained.** No server-side code, no database, no CDNs, no third-party trackers or analytics, no external fonts unless self-hosted.
6. **Never run anything against the VPS** (ssh, sftp, WinSCP, lftp, config changes) without showing me the exact command and getting a yes. The publish script must default to a dry run.
7. **Never write to the FTM folder or the input folder**, and never commit the GEDCOM or any build output containing it.

## Phase 0 (inspection)

### Already done (on the stale 27 Jul 2026 export; see `docs/phase0-report.md`)

These are the main findings. Re-verify the counts against the fresh export.

- **Format:** GEDCOM 5.5.1, UTF-8 with BOM, CRLF line endings, FTM 25.0.2.1181. 68 lines exceed 255 characters, so don't enforce the limit.
- **Size:** about 1,337 people, 332 families, 221 sources, 1,018 OBJE records.
- **No stable key besides the xref.** No `_UID`, `REFN` or `RIN`. `FSID` (FamilySearch ID, no underscore) appears on only 21 people.
- **Custom tags:**
  - `_FREL`/`_MREL`: child's relationship to father/mother ("Natural"; 2 "Adopted")
  - `_PHOTO`: preferred portrait
  - `_TEXT`: media description
  - `_DATE`: media date, free text
  - `_LINK`: citation URL
  - `_MILT`: military fact
  - EVEN+TYPE: FTM custom facts (Arrival, Departure, Marital Status, Race, NaturalizationDeclaration)
- **`FORM` is always "jpg"**, even for PDFs and .htm. Use the file extension.
- **No media category tag** in that export.
- **Media attachment:**
  - 420 objects on people
  - 525 **only on citations** (all under Ancestry sources). Map these to people via citation → fact → person (or family → both spouses).
  - 73 unattached
  - none on FAM records or SOUR records
- **Duplicates:** 38 images appear as two or more separate OBJE records with identical bytes. Content hashing merges them.
- **Media folder:** 2,250 files, of which 896 unreferenced files are byte-identical copies of referenced ones (FTM's `name(1).jpg` pattern).
- **Image types:** JPEG, PNG and 6 MPO (use frame 1). No TIFF, CMYK or 16-bit images, so those paths can be tested only with synthetic fixtures. 1,167 images are greyscale; 133 have ICC profiles.
- **Image sizes:** referenced images have a median long edge of about 3,300 px and a maximum of 8,984×6,968.
- **PDFs:** 7 referenced, up to 4 pages.
- **Other file types:** 12 `.htm` (saved web pages, mostly obituaries) and 2 Word files.
- **Notes:** only 5 people have person NOTEs. The long text is in 575 media descriptions and HTML-bearing source notes.
- **Dates:** standard forms plus 21 non-conforming values (`26/10/1968`, `25.07.1897`, `28th July, 1805`, `(ABT 1865)`, a list of years). Report these; don't guess at them.
- **Names:**
  - empty surnames (`Ellen //`) and `/None/`
  - nicknames in quotes, in parentheses, or as `Given, Nick` without slashes
  - stray `+`, and a `(Rev)` suffix
  - 17 people with multiple NAMEs
- **Privacy, rough count using this brief's rules:** 1,258 deceased, 14 likely living, **65 undetermined, most of whom appear to be living present-day relatives entered with a name only.**

### Remaining (do these first)

1. **Re-run the tag and record inspection** on the current export. A media check of the 5 Oct export is already done (see below). Will hasn't set any FTM media categories, so classification relies on the automatic rules.
2. **ID-stability test.** I'll provide `A.ged`, `B.ged`, `C.ged` and optionally `D.ged` in an `idtest\` folder outside the repo:
   - **A → B:** no changes between them (determinism).
   - **B → C:** one throwaway person added and one existing name edited.
   - **C → D:** the throwaway person deleted, the edit reverted, and a FamilySync with Ancestry run.

   Diff the xrefs of every person matched by name + birth + parents. Report whether IDs are stable across each step. If they aren't, propose a fallback key and tell me what it costs.
3. **Person notes:** confirm whether the near-absence of person notes is real or an export setting.
4. ~~Media folder check~~ **Done on the 5 Oct export:**
   - **1,246 media objects:** all resolve on disk under the expected prefix, none are missing, and **none are unattached** (the July export had 73 unattached).
   - **Referenced files by type:** 1,225 jpg (including 5 MPO), 7 pdf, 12 htm, 1 doc, 1 docx.
   - **Image sizes:** long edge median 3,323 px, 90th percentile 4,960, maximum 8,984. 318 are ≥ 4,000 px and 145 are under 800 px. Total 1.2 GB.
   - **Image metadata and modes:** 48 carry GPS, 10 have non-trivial EXIF orientation, and 634 are greyscale.
   - **Duplicates:** 60 groups of separate OBJE records point at byte-identical files.
   - **Unreferenced files:** 2,250 files on disk, of which 815 unreferenced ones are byte-identical copies of referenced files. 189 are truly unreferenced (35 of those are in `Person Profiles`).
   - **Privacy impact under rule B:** 3 objects are withheld, and all 3 are linked only to hidden people. Under the earlier version of the rule it was 70, because dead relatives in recent generations were hidden.
   - Re-run this check whenever the export changes.
5. Report back, then stop for approval.

## Architecture

Python pipeline with stages, each runnable on its own, driven by one config file.

```
config.yaml          # paths, site title/domain, contact, privacy years, thresholds, overrides
pipeline/
  extract.py         # GEDCOM -> normalized JSON (privacy applied here)
  media.py           # derivatives, tiles, manifest (incremental)
  render.py          # Jinja2 templates -> static HTML
  audit.py           # privacy + output checks; non-zero exit on failure
  publish.py         # SFTP sync; WinSCP (Windows) or lftp (WSL); dry-run by default
templates/
static/              # CSS, JS (self-hosted vendor libs)
overrides/           # hand-maintained lists
build/data/          # intermediate JSON (gitignored)
build/site/          # final output (gitignored)
tests/
```

- **Media processing:** pyvips/libvips (`pip install "pyvips[binary]"` on Windows), plus **PyMuPDF** to render PDF pages, since the binary libvips build can't load PDFs. Confirm both work early (Milestone 2) and tell me PyMuPDF's licence.
- **Search:** Pagefind via pip (`pagefind[extended]`). Work from the **current** docs; 1.5 replaced the default UI.
- Minimal JS. No front-end framework unless you justify it.

## Stage 1: extract

- **Parser:** a small purpose-built one is fine. Use a library only if it handles FTM's output correctly when tested against my file.
- Handle CONC/CONT, the encoding from the header, cross-references, and over-long lines.
- **Normalize into JSON:** people, families, events/facts, places (as text), sources, citations, notes and media links.
- **Citation media:** map it to the person whose fact cites it; for a family fact, map it to both spouses.
- **Stable keys and slugs:** use the stable key from the Phase 0 ID test as the internal key. Generate stable, readable URL slugs (e.g. `/people/james-wray-1838-1888-I123/`). Slugs change only if the name or dates change.
- **Dates:** parse into a structure that keeps qualifiers (about, before, after, between, estimated) and displays them nicely. Never invent precision.
- **HTML in source notes:** sanitize to an allowlist (`p`, `i`, `em`, `b`, `strong`, `a[href]`, `br`, `ul`, `ol`, `li`).
- **Suppressed fact types** (config `facts.suppress`, default `[Race]`): drop them in extract.

### Privacy (in extract, before anything is written)

Classify everyone into exactly one of four groups. Apply the groups **in this order**, and the first match wins:

**1. Known living, always hidden.** IDs in `overrides/living.txt`, plus anyone marked private in FTM, if that's visible in the export.

**2. Deceased, published.** Any one of these is enough:
- a death, burial, cremation or probate record, with or without a date
- any dated event in their own life (including family events like marriage) more than 100 years ago
- a child with any dated event more than 100 years ago
- a parent born more than 175 years ago
- a **full** sibling (same two parents, **both `_FREL` and `_MREL` "Natural"**) born more than 130 years ago

Optional: `overrides/deceased.txt` publishes specific people the rules miss. It's not expected to be needed, given the generation rule below.

**3. Likely living, hidden.** No match above, and **either** they have a dated event of their own within the last 100 years, **or** a child was born or christened within the last 85 years.

**4. Undetermined.** No evidence either way.

**Generation cutoff (Will's rule, 5 Oct 2026, revised to "rule B" the same day).** This is applied alongside the four groups above, and it replaces the need for a hand-kept list of deceased people.

- **How generation is counted:** relative to the home person, `privacy.home_person` (the tree owner). Going to a parent counts +1, to a child −1, to a spouse 0, found by a breadth-first walk. Where two paths give different generations (3 people on the 5 Oct data), use the **younger** one.
- **Your parents' generation and younger** (`privacy.min_generation: 2`): **published only if in group 2 (deceased)**, i.e. there is evidence of death. Group 3 and group 4 people are hidden. Direct line or not makes no difference. A parent with no dates at all is therefore hidden.
- **The home person:** always hidden.
- **Grandparents' generation and above:** groups 1–3 apply as written, and group 4 (undetermined) is **published**.
- **People not connected to the home person at all:** groups 1–3 apply, and group 4 is **hidden**.

In effect, in recent generations a person is published only when the tree records their death. Adding a Death fact in FTM (dated and sourced where possible) is how someone gets published.

Expected on the 5 Oct export: about **1,379 people published and 75 hidden**, and **3 media items withheld**.

The privacy report lists every person hidden by the generation rule as its own section.

Thresholds live in config: `event_years` 100, `child_years` 100, `parent_years` 175, `sibling_years` 130, `recent_years` 100, `recent_child_years` 85.

**Date handling:**
- **Deceased tests:** use the **latest** date the value allows. `BEF` and exact dates are used as given. `ABT`/`EST`/`CAL` are used as the stated date. `BET x AND y` uses `y`. `AFT` and open-ended dates don't count.
- **Likely-living test:** use the **earliest** date the value allows. `AFT 1950` counts as recent; `BEF 1950` does not.
- Unparseable dates count for nothing, and are listed in the report.

Don't add any other inference. If you think there's a gap, raise it with me.

Living people (groups 1 and 3, and group 4 if set to hide) get no pages. In listings they appear as "Living [Surname]", or "Living" when there's no surname; config can omit them instead. None of their own data is written to the JSON.

**Privacy report**, written every build:
- **everyone hidden**, with group and reason
- **everyone published as undetermined** (only possible if set to publish)
- **centenarian check:** anyone published only by the event or child rule whose qualifying date is within the last 110 years
- people with unparseable dates

Respect FTM private facts, notes and media if they appear in the export.

**Media privacy:** media linked to any living person is excluded by default, even if it's also linked to a published person. `overrides/media-allow.txt` publishes specific items anyway.

## Stage 2: media

Input is only the media referenced by published people.

- Resolve FTM's absolute paths to the configured media folder (prefix rewrite, case-insensitive). Report unresolvable files without failing the build. Handle filenames with leading spaces, `&` and double spaces.
- Apply EXIF orientation, then strip all metadata. Convert to sRGB. Handle greyscale, ICC profiles and MPO; also handle CMYK, 16-bit and multi-page TIFF (synthetic tests only).
- **Classification (photo/document):**
  - the FTM category, if the fresh export carries it;
  - otherwise a document if: PDF; or the filename stem (minus `(N)`) equals a source title (Ancestry record image); or citation-only attachment;
  - otherwise a photo.
  - `overrides/media-type.txt` corrects any of these.
- **Outputs per image:**
  - a thumbnail (~400 px WebP)
  - a display image (~2048 px WebP, plus `srcset`)
  - a zoom tier:
    - **photos:** one WebP capped around 3,000–4,000 px
    - **documents ≥ 4,000 px:** DZI via `dzsave` with **510 px tiles** (≈31k files on July data, vs ≈109k at 254 px)
    - **smaller documents:** a single large WebP, opened in OpenSeadragon as a simple image
- **PDFs:** render pages to images and treat them as documents. Publish the original PDF only if config allows it.
- **`.htm` and Word files are not published in the MVP.** List them in the media report.
- **Filenames and caching:** content-hash filenames, plus a hash cache for incremental rebuilds.
- **Manifest:** write `build/data/media.json` with id, type, dimensions, caption (`TITL`), description (`_TEXT`), date (`_DATE`, free text), linked people, derivative paths and zoom tier.
- All thresholds are configurable. Tune them against the real scans.

## Stage 3: render

### Page types (MVP)

- **Home:** site intro, featured surnames, search box, contact invitation.
- **Person page:**
  - name and dates/places
  - parents, spouses and children, linked where published
  - events/facts in chronological order
  - notes
  - **sources grouped by source**, with citations under each (otherwise NAME/RESI citations swamp the page)
  - citations to the source titled **"Ancestry Family Trees"** (`@S1@`: other members' trees) are shown in their own group, labelled **"Other researchers' trees (not original records)"** and listed after the record sources. Match on title, not ID, in case the xref changes.
  - media gallery, with the `_PHOTO` portrait first
  - the contact call to action
- **Surname index:** all surnames with counts, plus a page per surname. People with an empty surname go under "Surname unknown".
- **Sources:** there are no standalone source pages in the MVP. A source appears only as a citation on a published person's page. Citations on hidden people, and sources cited only by hidden people, never appear.
- **Media:** galleries on person pages. A standalone page per item is optional; discuss it with me first.
- **Search:** Pagefind over person pages.
- **About/contact page**, and a **404 page**.

### Design

- Modern, clean and calm. Generous whitespace and excellent typography. Use self-hosted fonts or a system font stack.
- Responsive, mobile-first, with light and dark mode following the system preference.
- Accessible: semantic HTML, keyboard navigation, visible focus, good contrast, and alt text from captions.
- Fast: lazy-loaded images, minimal JS.
- Show me one rendered person page as a design proposal before applying it across the site.

### Image viewer

- **Photos:** a PhotoSwipe v5 lightbox (zoom, pan, swipe/arrow navigation, Esc to close).
- **Documents:** OpenSeadragon in the same modal, with zoom buttons and fullscreen.
- Both show the caption, date and links to the people shown, and work well with mobile touch.
- A URL hash deep-links to one image.
- Self-host both libraries. Confirm current versions and licences.

### Findability (SEO)

- Titles like `James Wray (1838–1888) – Hawkesville, Ontario | <Site name>`.
- Meta descriptions generated from the person's key facts.
- schema.org `Person` JSON-LD, using URLs of published people only.
- Canonical URLs, `sitemap.xml` and `robots.txt`.
- Stable URLs.

### Contact

- Every person page ends with "Related to [Name]? I'd love to hear from you." The same appears on the home and about pages.
- **MVP:** a mailto link with a lightly obfuscated email (assembled by JS, with a no-JS fallback) and a pre-filled subject.
- The mechanism is pluggable via config.

## Stage 4: audit

The build fails if:

- any published person fails the privacy rules
- any hidden person's ID, page or data appears in the output (names inside published free text are exempt; see Hard Rule 1)
- any output file references a media file not in the manifest
- any published image still contains EXIF/GPS/XMP
- any internal link is broken
- the output contains originals (by hash) from the media folder

The audit also prints a summary: people published, people withheld by group, media published, media withheld by reason, and missing files.

## Stage 5: publish

- `publish.py` syncs `build/site/` to the remote docroot over **SFTP** so that only changed files go up:
  - **native Windows:** WinSCP scripting (`winscp.com`, `synchronize remote`)
  - **WSL:** `lftp mirror -R`
- **Dry run by default.** `--live` is needed to push. Remote deletion only with an explicit flag.
- SSH key auth only. The deploy user is a dedicated **SFTP-only, chrooted** account. Document the server-side setup for me as instructions; don't perform it.
- Expect a slow first upload. Atomic deploys are out of scope.
- Provide a sample **Nginx** server block and a Caddy equivalent: HTTPS notes, long cache for hashed assets, short cache for HTML, gzip/brotli. **Hand them to me; don't apply them.**

## Tree lint (add-on, after Milestone 1)

`gedcomwright lint` is a **read-only** report of tree problems that affect the site. Fixes are made by me in FTM, never by the pipeline. It reuses the extract stage's parser, writes `build/reports/lint.md` (gitignored, since it names living people), and is re-run after each export so the list shrinks. FTM's own tools cover the general clean-up (Find Duplicate People, the Data Errors report, place resolution); don't duplicate them.

Checks:
- **Death facts are what publish someone in recent generations,** so flag them first:
  - any Death/Burial fact in the parents' generation or younger with **no citation**, or whose only citation is "Ancestry Family Trees" (on 5 Oct: 5 such facts tree-wide);
  - death facts with no date.
- Dates that aren't valid GEDCOM (`24/08/1969`, `25.07.1897`, `28th July, 1805`, `(ABT 1865)`, lists of years), with person and fact.
- Empty or placeholder surnames (`Ellen //`, `/None/`, surname-only records).
- Nicknames written inconsistently in the name field (quotes, parentheses, `Given, Nick` with no slashes), and stray characters (`+`, `(Rev)`).
- Likely duplicates the generation rule exposed: same surname, compatible given names (Bob/Robert, Doug/Douglas), same family. These are a hint list for FTM's own duplicate finder, not merges.
- People whose generation differs by path (Porter/Henderson on 5 Oct), and people not connected to the home person.
- Facts and families with impossible ordering: death before birth, child born before a parent was 12 or after the mother was 55, marriage before 14. Configurable thresholds; report only.
- Media: objects whose title or description says "obit…" attached to someone other than the subject, and media files on disk that no OBJE references (excluding byte-identical copies).
- Each section shows a count, so progress is visible.

## Testing

- Unit tests for:
  - date parsing
  - privacy determination: no dates, each group and their precedence, each deceased rule, half vs full vs adopted siblings, each qualifier in both directions, boundary years, the generation rule (direct ancestor vs collateral, an undated direct ancestor, an unconnected person, conflicting generation paths, and the home person), and all overrides
  - slug stability
- A **synthetic GEDCOM fixture** containing:
  - living, deceased and undetermined people
  - private facts
  - media linked to living people
  - citation-only media
  - an adopted child
  - an empty surname
  - non-conforming dates
  - over-long lines
  - a CMYK, a 16-bit and a multi-page TIFF
- One command that runs the full pipeline end to end on my real export.

## Milestones (stop and report after each)

0. Phase 0 remaining items (above), plus a re-check of the generation-rule counts on the current export.
1. Extract + privacy + tests, with JSON output and a privacy report I can review.
1a. Tree lint report (`gedcomwright lint`). Small; it doesn't block Milestone 2.
2. Media stage, with sample outputs (photo and document) and a manifest.
3. Render: one person-page design proposal, then the full site with search.
4. Audit + publish script + server config samples.
5. README complete, plus a list of known limitations.

## Out of scope for MVP (later)

- Interactive tree/pedigree charts (evaluate existing d3-based libraries first)
- Maps and place pages (the export already has 1,480 coordinates)
- Narrative/story chapters linked to person pages
- Publishing `.htm` obituaries
- Contact form endpoint on the VPS
- Redirects from old site URLs
- Multi-language support
