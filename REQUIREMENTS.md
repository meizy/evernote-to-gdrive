# Evernote → Google Drive Migration Tool

## Overview

A Python CLI tool (`evernote-to-gdrive`) that migrates Evernote notes to Google Drive, preserving content structure and organization. Designed for **one-time migration** from Evernote — not for ongoing synchronization between the two platforms.

## Input

The tool expects a **directory of `.enex` files**, one per notebook. Evernote's desktop app can export each notebook individually to produce this layout.

To preserve **stack hierarchy**, nest notebook exports inside subdirectories named after the stack:

```
export/
  Startups/              ← stack directory
    Funding.enex         ← notebook in "Startups" stack
    ScaleDB.enex
  Seculert.enex          ← notebook with no stack
```

- `export/<notebook>.enex` → notebook folder directly under the migration root
- `export/<stack>/<notebook>.enex` → notebook folder inside a stack folder

A single `.enex` file is also accepted, but all notes will be placed in one folder (the file's basename) with no stack/notebook structure.

## Note Classification & Output Mapping

Each note falls into one of these categories:

| Note type | Condition | Google Drive output |
|---|---|---|
| **Attachment-only, single** | No meaningful text, exactly 1 attachment | Upload the raw file — note title becomes the filename |
| **Attachment-only, multiple** | No meaningful text, ≥2 attachments | See multi-attachment policy below |
| **Text + attachment(s)** | Has text body AND ≥1 attachment | Google Doc with text; attachments embedded or linked (see below) |
| **Text-only** | Has text body, no attachments | Google Doc |

"Meaningful text" means non-empty after stripping Evernote's HTML markup and whitespace.

The following attachment types are excluded from classification and output entirely:
- `application/octet-stream` — raw HTML blobs saved by the Evernote web clipper
- `image/svg+xml` — SVG images, typically decorative web-clip chrome (logos, icons); not supported in Google Docs or DOCX

### Attachment policy (`--attachments`)

Controlled by a CLI flag, default: `doc`. Applies to all note types that produce a doc (text+attachments and attachment-only-multi). For text+attachment notes, `files` implies `both` since the doc must exist to hold the text.

Web-clipped notes (those with a source URL) always use `doc` policy regardless of `--attachments`, to avoid producing sibling files from web clipper images.

| Flag value | Behavior |
|---|---|
| `doc` *(default)* | Embed images inline in the doc; upload non-image attachments (PDF, audio, video, Office docs, etc.) as sibling files and link them. |
| `files` | One raw sibling file per attachment, named `<title>_<label>_<n>.<ext>`. For text+attachment notes, the doc is also created and all attachments are kept as siblings (same as `both`). |
| `both` | Embed images inline in the doc AND keep all attachments as sibling files. |

### Attachment sibling filename pattern

Sibling files (attachments written alongside a doc) are named:

```
<note_title>_<label>_<n>.<ext>
```

Where `<label>` is derived from the MIME type and `<n>` is a per-label running counter:

| MIME primary type | Label | Example |
|---|---|---|
| `image/*` | `img` | `My Note_img_1.jpg` |
| `audio/*` | `aud` | `My Note_aud_1.mp3` |
| `video/*` | `vid` | `My Note_vid_1.mp4` |
| `text/*` | `txt` | `My Note_txt_1.txt` |
| `application/pdf` | `pdf` | `My Note_pdf_1.pdf` |
| `application/*` (other) | first 3 chars of subtype | `My Note_zip_1.zip`, `My Note_doc_1.docx` |

When a doc has sibling files, the doc itself is named `<title>_0` (e.g. `My Note_0.docx`) so all related files sort together.

### Image embedding in Google Docs

Image files are embedded inline in the Google Doc. Supported formats: JPEG, PNG, GIF, and WebP. SVG is excluded entirely (not supported in Google Docs or DOCX and typically noise from web clips). A maximum of 100 images are embedded per note; any beyond that are skipped with a warning.

Non-image attachments (such as PDF, audio, video, Office documents) are uploaded as sibling Drive files and linked from the doc with a clearly labelled hyperlink.

## Notebook → Folder Mapping

Evernote notebooks can be grouped into **stacks**. The folder hierarchy in Drive mirrors this structure inside a configurable root folder in **My Drive**.

```
My Drive/
  Evernote Migration/        ← configurable root
    Startups/                ← stack
      Funding/               ← notebook inside stack
        photo.jpg
        scan.pdf
        Note with text       ← Google Doc
      ScaleDB/               ← notebook inside stack
        ...
    Seculert/                ← notebook with no stack (directly under root)
      ...
```

The stack name is derived from the subdirectory name in the `evernote-backup` export layout:
- `export/<notebook>.enex` → no stack, folder goes directly under root
- `export/<stack>/<notebook>.enex` → stack folder created under root, notebook folder inside it

## Metadata Preservation

### Both modes (local and Google Drive)

| Field | How |
|---|---|
| Title | Filename / doc name |
| Notebook | Folder name |
| Stack | Parent folder |
| Content | Converted to document body with formatting preserved (bold, italic, headings, tables, lists, font sizes/colors, etc.) |
| Attachments | Images embedded inline; non-image attachments uploaded/written separately and linked |
| Source URL | Inserted as first line of document body |
| Updated date | File `mtime` (local) / `modifiedTime` on Drive file (google); falls back to `created` if absent |

### Google Drive only

| Field | How |
|---|---|
| Created date | Drive file `description` field — `Created: YYYY-MM-DD HH:MM UTC` (Drive API does not allow setting `createdTime`) |
| Source URL | Also appended to Drive file `description` field, in addition to the document body |
| Tags | Appended to Drive file `description` field as `Tags: tag1, tag2` (disable with `--no-tags`) |

### Local only (in addition to the common fields above)

| Field | How |
|---|---|
| Created date | File birthtime (macOS/Windows) |
| Tags | Written as `[Tags: tag1, tag2]` on the first line of the `.docx` body (disable with `--no-tags`) |

### Not preserved

- **Author, geolocation, reminders, encrypted content** — not extracted from ENEX

### Content processing

Evernote's HTML-based formatting is preserved in both output modes: bold, italic, underline, headings, tables, ordered and unordered lists, font sizes, and colors. In local mode this is achieved via `html4docx` converting ENML to `.docx`; in Google Drive mode via Drive's native HTML→Google Doc import.

The following additional transformations are applied to note content in all output formats:

- **Encrypted blocks** (`<en-crypt>`) are stripped from the output.
- **Checkboxes** (`<en-todo>`) are converted to `[x]` (checked) or `[ ]` (unchecked) text markers.
- **External images** (HTTP/HTTPS `<img>` tags embedded in notes by the web clipper) are removed with a warning — they cannot be fetched or embedded.

## `analyze` Subcommand

Before migrating, the user can run:

```
evernote-to-gdrive analyze INPUT
```

Output (to console and optionally a JSON file):

- Total note count
- Per-notebook note counts
- Classification breakdown: attachment-only / text+attachment / text-only
- Attachment counts by type (image, PDF, audio, video, other)
- Total attachment size (MB), largest single attachment
- Notes with multiple attachments (count)
- Any notes that would lose content (e.g. notes with encrypted sections, which are stripped)

This lets the user gauge migration scope and expected Drive storage usage before running the full migration.

## Google Authentication

Uses OAuth 2.0. Credentials stored locally in `.config/` under the project directory after first-run authorization.

## Output Modes

### `google` (default)

Uploads directly to Google Drive. Creates Google Docs for text notes; embeds images inline; uploads non-image attachments (PDF, audio, video, etc.) as separate Drive files and inserts clickable links into the Doc.

### `local`

Writes notes to a local folder tree on disk (mirroring the stack/notebook hierarchy). Intended as a staging area: the folder can then be uploaded to Google Drive manually, with Drive's "Convert uploads" setting to auto-convert `.docx` files to Google Docs.

| Note type | Local output |
|---|---|
| Attachment-only, single | Raw file (`<title>.<ext>`) |
| Attachment-only, multi (`--attachments=doc`) | `<title>_0.docx` (if any siblings) with images embedded inline; non-image attachments written as sibling files (`<title>_pdf_1.pdf`, `<title>_aud_1.m4a`, etc.) and linked in the doc |
| Attachment-only, multi (`--attachments=files`) | One raw sibling file per attachment (`<title>_img_1.jpg`, `<title>_pdf_1.pdf`, etc.) |
| Attachment-only, multi (`--attachments=both`) | `<title>_0.docx` with images embedded AND all attachments kept as sibling files |
| Text-only | `<title>.docx` |
| Text + attachments (`--attachments=doc`) | `<title>_0.docx` with images embedded inline; non-image attachments as sibling files linked in the doc |
| Text + attachments (`--attachments=files` or `both`) | `<title>_0.docx` with images embedded inline; all attachments also kept as sibling files |

RTL text (Hebrew, Arabic) is rendered correctly in all output formats.

## CLI Interface

```
evernote-to-gdrive COMMAND [OPTIONS]

Commands:
  analyze    Inspect .enex files and report statistics (no upload)
  migrate    Migrate notes to Google Drive or a local folder

evernote-to-gdrive analyze INPUT [OPTIONS]
  --output-json PATH    Also write stats to a JSON file
  --mime MIME_TYPE       List notes that have an attachment of this MIME type
  --findnote TITLE      Find which notebook(s) contain a note with a given title
  --report-dups         List all notes with duplicate titles within the same notebook
  --report-tags         List all tags with a count of notes per tag, sorted by count descending
  --report-links-notebooks  Total inter-note link count per notebook, sorted by count descending
  --report-links-notes      Inter-note link count per note, sorted by notebook then note name

evernote-to-gdrive migrate INPUT [OPTIONS]
  --output [gdrive|local]    Output mode [default: gdrive]
  --dest TEXT                Output destination: Drive folder path (gdrive, supports a/b/c) or local folder
                             (local, relative or absolute). Default: 'Evernote Migration'
  --dry-run                  Authenticate and create root Drive folder only (gdrive mode only)
  --stack TEXT               Only migrate notebooks in this stack (repeatable)
  --notebook TEXT            Only migrate this notebook (repeatable)
  --note TITLE               Only migrate the note with this exact title (requires --notebook)
  --attachments [doc|files|both]  How to handle attachments [default: doc]
  --log-file PATH            Write migration log (CSV)
  --verbose                  Print a line per note instead of a progress bar
```

### Developer options

```
evernote-to-gdrive migrate INPUT [OPTIONS]
  --debug                    Enable debug logging with timestamps for API calls
  --dest null                Run migration without writing files (output is discarded)
```

## Progress & Logging

- Console progress bar per note (via `rich`)
- Log file records: note title, notebook, classification, Drive file ID(s), status (success / skipped / error)
- Final summary: stacks, notebooks, total notes, success/skipped/errors, estimated uploaded size (gdrive only)

## Google API Usage

All operations use the **Drive API v3 only**.

#### Google Drive API Limits

| Limit | Value | Scope |
|---|---|---|
| **Queries** | 12,000 per minute | Per user |
| **Write requests (sustained)** | 3 per second | Per user, not increasable |
| **Daily upload volume** | 750 GB per 24 hours | Per user, includes copies |

#### Rate limit handling

- Write calls are proactively throttled to stay within the 3 writes/sec sustained limit.
- On `429` or `403 rateLimitExceeded`, the tool retries with exponential backoff (up to 5 attempts) before marking the note as failed.
- When a persistent rate-limit error occurs after a large upload session, the error message includes a hint that the 750 GB daily limit may have been reached and the user can resume the next day.

#### Resuming interrupted runs

Existing notes are never re-uploaded. If a run is interrupted, rerunning resumes from where it left off.

## Duplicate note titles

- **Duplicate note titles within a run**: if two notes in the same notebook share the same safe title, the second (and subsequent) notes are migrated normally — not skipped. In local mode they are renamed with a ` (2)`, ` (3)`, … suffix to avoid filesystem collisions. In Google Drive mode the original name is kept (Drive allows multiple files with the same name, matching Evernote's own behavior). Use `analyze --report-dups` to identify collisions before migrating.
- **Resumed run skips**: if a note already exists at the destination from a prior run, it is skipped (status: `skipped`).

## Error Handling

- **Unsupported attachment types**: upload as-is with original MIME type; log a warning
- **API rate limits**: exponential backoff with retry (max 5 attempts) — see Google API Usage above
- **Partial failures**: continue migration; report failed notes at the end without halting the run
- **API error context**: all Google Drive API failures (including network-level errors like `TimeoutError`, `ConnectionError`) must include the specific operation that failed (e.g. `[upload 'note_pdf_1.pdf']`, `[create doc 'My Note']`) and where applicable the file or resource name, so failures can be diagnosed without re-running with debug logging

## Inter-Note Link Rewriting (GDrive mode)

- Evernote `evernote:///view/...` links in notes are rewritten to Google Doc URLs after migration
- Matching is title-based (ENEX files do not include note GUIDs; anchor text = target note title)
- Two-pass approach: pass 1 migrates all notes; pass 2 rewrites links and updates docs in place
- Unresolved links (target note not in export, or note was renamed after the link was created) are replaced with `[link to "Title" not resolved]` text; stale anchors from renamed notes cannot be resolved
- Use `--skip-note-links` to opt out of link rewriting
- Local output mode: not supported (no stable URLs)
- Single-note mode (`--note`): all links are replaced with the "not resolved" text

## Out of Scope (v1)

- Evernote internal resources (ink notes, encrypted text sections)
- Shared notebooks or collaboration features
- Incremental sync / two-way sync

## Dependencies

- `lxml` — ENEX parsing
- `google-api-python-client` + `google-auth-oauthlib` — Drive API
- `rich` — progress display and console output
- `python-docx` + `html4docx` — generating `.docx` files for local output mode
