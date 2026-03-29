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

### Multi-attachment policy (`--multi-attachment`)

Controlled by a CLI flag, default: `doc`.

| Flag value | Behavior |
|---|---|
| `doc` *(default)* | Create a Google Doc listing all attachments; upload each file to Drive and insert links |
| `files` | Upload each attachment as a separate file, named `<note_title>_<n>.<ext>` (e.g. `xxx_1.pdf`, `xxx_2.pdf`) |

### Image embedding in Google Docs

The Google Docs API supports **inline image insertion** (`insertInlineImage`) for JPEG, PNG, and GIF. Images are embedded directly in the document body.

**PDFs cannot be embedded** via the Docs API. PDF attachments in text+attachment notes are uploaded as separate Drive files named `<note_title>_<n>.pdf` and linked from the Google Doc (a clearly labelled hyperlink is inserted at the attachment's position in the document).

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
| Attachment-only, multi (`--multi-attachment=doc`) | `.docx` listing all attachments as clickable hyperlinks; each attachment written as a sibling file |
| Attachment-only, multi (`--multi-attachment=files`) | One raw file per attachment (`<title>_<n>.<ext>`) |
| Text-only | `<title>.docx` |
| Text + attachments | `<title>.docx` with images embedded inline; PDFs written as sibling files and referenced as clickable hyperlinks within the doc |

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
  --skip-existing            Skip notes whose output file already exists in the target folder
  --multi-attachment [doc|files]  How to handle notes with multiple attachments [default: doc]
  --log-file PATH            Write migration log (CSV) [default: migration.log]
```

## Progress & Logging

- Console progress bar per note (via `rich`)
- Log file records: note title, notebook, classification, Drive file ID(s), status (success / skipped / error)
- Final summary: total notes, counts per category, total uploaded size, errors

## Error Handling

- **Duplicate filenames**: append `(2)`, `(3)`, etc.
- **Unsupported attachment types**: upload as-is with original MIME type; log a warning
- **API rate limits**: exponential backoff with retry (max 5 attempts)
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
