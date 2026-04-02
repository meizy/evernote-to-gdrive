# Evernote → Google Drive Migration Tool

## Overview

A Python CLI tool (`evernote-to-gdrive`) that migrates Evernote notes to Google Drive, preserving content structure and organization.

## Input

Evernote exports notes as `.enex` files (Evernote XML format). The user exports notebooks from the Evernote desktop/web app, producing one `.enex` file per notebook (or one for all notes).

The tool accepts:
- A single `.enex` file
- A directory of `.enex` files (one per notebook)

## Note Classification & Output Mapping

Each note falls into one of these categories:

| Note type | Condition | Google Drive output |
|---|---|---|
| **Attachment-only, single** | No meaningful text, exactly 1 attachment | Upload the raw file — note title becomes the filename |
| **Attachment-only, multiple** | No meaningful text, ≥2 attachments | See multi-attachment policy below |
| **Text + attachment(s)** | Has text body AND ≥1 attachment | Google Doc with text; attachments embedded or linked (see below) |
| **Text-only** | Has text body, no attachments | Google Doc |

"Meaningful text" means non-empty after stripping Evernote's HTML markup and whitespace.

### Attachment policy (`--attachments`)

Controlled by a CLI flag, default: `doc`. Applies to all note types that produce a doc (text+attachments and attachment-only-multi). For text+attachment notes, `files` implies `both` since the doc must exist to hold the text.

| Flag value | Behavior |
|---|---|
| `doc` *(default)* | Embed images inline in the doc; upload PDFs/other as sibling files and link them. Temp image files created during gdrive embedding are deleted after the doc is created. |
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

The Google Docs API supports **inline image insertion** (`insertInlineImage`) for JPEG, PNG, and GIF. The API requires a publicly accessible URL (max 2KB) — base64 data URIs are not supported.

**Embedding flow (gdrive mode):**
1. Upload the image to Drive in the notebook folder using the sibling filename pattern.
2. Grant public read access (`anyone with link`).
3. Pass the public Drive download URL to `insertInlineImage` in the doc's single `batchUpdate` call.
4. If policy is `doc`: delete the uploaded image file after the doc is created (it was only needed temporarily). If policy is `both` or `files`: keep it.

This results in **2N+2 API calls** per note without deletion (N uploads + N permission grants + 1 doc create + 1 modifiedTime patch), or **3N+2** with deletion.

**PDFs cannot be embedded** via the Docs API. PDF attachments are uploaded as sibling Drive files and linked from the doc with a clearly labelled hyperlink.

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
| Content | Converted to plain text body |
| Attachments | Images embedded inline; PDFs uploaded/written separately and linked |
| Source URL | Inserted as first line of document body |
| Updated date | File `mtime` (local) / `modifiedTime` on Drive file (google); falls back to `created` if absent |

### Local only

| Field | How |
|---|---|
| Created date | File birthtime (macOS/Windows) |

### Google Drive only

| Field | How |
|---|---|
| Created date | Drive file `description` field — `Created: YYYY-MM-DD HH:MM UTC` (Drive API does not allow setting `createdTime`) |
| Source URL | Also appended to Drive file `description` field, in addition to the document body |

### Not preserved

- **Tags** — no meaningful equivalent in Google Drive
- **Author, geolocation, reminders, encrypted content** — not extracted from ENEX

## `analyze` Subcommand

Before migrating, the user can run:

```
evernote-to-gdrive analyze INPUT
```

Output (to console and optionally a JSON file):

- Total note count
- Per-notebook note counts
- Classification breakdown: attachment-only / text+attachment / text-only
- Attachment counts by type (JPEG, PNG, PDF, other)
- Total attachment size (MB), largest single attachment
- Notes with multiple attachments (count)
- Any notes that would be skipped or need manual review (e.g. encrypted sections)

This lets the user gauge migration scope and expected Drive storage usage before running the full migration.

## Google Authentication

Uses OAuth 2.0 with scopes:
- `https://www.googleapis.com/auth/drive.file` — create/manage files the app creates
- `https://www.googleapis.com/auth/documents` — create and edit Google Docs

Credentials stored locally in `.config/` under the project directory after first-run authorization.

## Output Modes

### `google` (default)

Uploads directly to Google Drive. Creates Google Docs for text notes; embeds images inline; uploads PDF attachments as separate Drive files and inserts clickable links into the Doc.

### `local`

Writes notes to a local folder tree on disk (mirroring the stack/notebook hierarchy). Intended as a staging area: the folder can then be uploaded to Google Drive manually, with Drive's "Convert uploads" setting to auto-convert `.docx` files to Google Docs.

| Note type | Local output |
|---|---|
| Attachment-only, single | Raw file (`<title>.<ext>`) |
| Attachment-only, multi (`--attachments=doc`) | `<title>_0.docx` (if any siblings) with images embedded inline; PDFs/other written as sibling files (`<title>_pdf_1.pdf` etc.) and linked in the doc |
| Attachment-only, multi (`--attachments=files`) | One raw sibling file per attachment (`<title>_img_1.jpg`, `<title>_pdf_1.pdf`, etc.) |
| Attachment-only, multi (`--attachments=both`) | `<title>_0.docx` with images embedded AND all attachments kept as sibling files |
| Text-only | `<title>.docx` |
| Text + attachments (`--attachments=doc`) | `<title>_0.docx` with images embedded inline; PDFs/other as sibling files linked in the doc |
| Text + attachments (`--attachments=files` or `both`) | `<title>_0.docx` with images embedded inline; all attachments also kept as sibling files |

RTL paragraphs (Hebrew, Arabic) are detected and marked as bidirectional in the `.docx` XML so Word, LibreOffice, and Google Docs all render them correctly.

For Google Docs uploads, if the note title or any paragraph contains RTL characters, all paragraphs in the document are set to `RIGHT_TO_LEFT` direction via the Docs API `batchUpdate`. This is a document-level setting (not per-paragraph) due to API constraints.

## CLI Interface

```
evernote-to-gdrive COMMAND [OPTIONS]

Commands:
  analyze    Inspect .enex files and report statistics (no upload)
  migrate    Migrate notes to Google Drive or a local folder

evernote-to-gdrive analyze INPUT [OPTIONS]
  --output-json PATH    Also write stats to a JSON file

evernote-to-gdrive migrate INPUT [OPTIONS]
  --output [gdrive|local]    Output mode [default: gdrive]
  --dest TEXT                Output destination: Drive folder path (gdrive, supports a/b/c) or local folder
                             (local, relative or absolute). Default: 'Evernote Migration'
  --dry-run                  Authenticate and create root Drive folder only (gdrive mode only)
  --stack TEXT               Only migrate notebooks in this stack (repeatable)
  --notebook TEXT            Only migrate this notebook (repeatable)
  --attachments [doc|files|both]  How to handle attachments [default: doc]
  --log-file PATH            Write migration log (CSV) [default: migration.log]
```

## Progress & Logging

- Console progress bar per note (via `rich`)
- Log file records: note title, notebook, classification, Drive file ID(s), status (success / skipped / error)
- Final summary: total notes, counts per category, total uploaded size, errors

## Google API Usage

### Quota Management

All operations use the **Drive API v3 only** (Google Docs are created via Drive's HTML import with MIME type conversion). There is no separate Docs API quota to manage.

#### Google Drive API Limits

| Limit | Value | Scope |
|---|---|---|
| **Queries** | 12,000 per minute | Per user |
| **Write requests (sustained)** | 3 per second | Per user, not increasable |
| **Daily upload volume** | 750 GB per 24 hours | Per user, includes copies |

Write operations include: `files.create`, `files.update`, `files.delete`, `permissions.create`. Read operations include: `files.list`, `files.get`.

#### Write throttling

Proactively throttle all write API calls to stay within the 3 writes/sec sustained limit. Insert a ~0.34 s delay between consecutive write calls. Read calls (`files.list`) are **not** throttled — the 12,000/min read limit is far above what sequential processing can reach.

For batch requests (`batch.execute`), account for N sub-requests by sleeping `N × 0.34 s` before executing the batch.

**Batch calls** — use batch requests wherever the API supports them. Specifically:
- Group Drive metadata updates (e.g. `modifiedTime`, `description`) into a single `files.update` call.
- Batch permission and delete operations when handling multiple files per note.

**Batch request limit** — Drive batch HTTP requests support a maximum of 100 sub-requests. To stay within this limit, at most 100 embeddable images are uploaded per note. Any images beyond 100 are skipped and a warning is logged. Non-image attachments are unaffected by this limit.

#### 750 GB daily upload limit

Track total bytes uploaded during the session. When a persistent `403`/`429` error occurs after all retries are exhausted and a substantial amount of data has been uploaded (>100 GB), include a hint in the error message suggesting the user may have hit the 750 GB daily limit and can resume tomorrow. The 750 GB limit error is indistinguishable from other rate-limit errors (same `403`/`429` status codes), so the byte counter provides the context needed to give the user a useful recommendation.

Do **not** proactively stop the migration at a threshold — let Google enforce the limit and use the byte counter to enrich the error message.

#### Exponential backoff on quota errors

If a `429 Resource Exhausted` or `403 rateLimitExceeded` response is received:
1. Catch the error and extract the retry-after hint if present.
2. Wait `base_delay × 2^attempt` seconds (base delay: 1 s; max delay: 64 s).
3. Retry up to 5 times before marking the note as failed and continuing.
4. Log each retry with attempt number and delay at DEBUG level.

#### Resuming interrupted runs

Existing notes are always skipped (never duplicated). If a migration run is interrupted (e.g. quota exhausted for the day), rerunning will resume without re-uploading completed notes. This is an important quota-conservation mechanism. To keep the skip check itself parsimonious, existing files in each target folder must be fetched in a single `files.list` call (with the folder ID as parent) rather than issuing one lookup per note. The result is cached in memory for the duration of the run.

## Error Handling

- **Duplicate filenames**: append `(2)`, `(3)`, etc.
- **Unsupported attachment types**: upload as-is with original MIME type; log a warning
- **API rate limits**: exponential backoff with retry (max 5 attempts) — see Google API Usage above
- **Partial failures**: continue migration; report failed notes at the end without halting the run

## Out of Scope (v1)

- Evernote note links between notes
- Evernote internal resources (ink notes, encrypted text sections)
- Shared notebooks or collaboration features
- Incremental sync / two-way sync

## Dependencies (planned)

- `lxml` — ENEX parsing
- `google-api-python-client` + `google-auth-oauthlib` — Drive and Docs APIs
- `rich` — progress display and console output
- `html2text` — converting Evernote HTML body to plain text / Docs content
- `python-docx` — generating `.docx` files for local output mode (text, embedded images, and hyperlinked PDF attachments)
