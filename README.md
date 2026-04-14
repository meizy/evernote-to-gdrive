# evernote-to-gdrive

[![PyPI version](https://img.shields.io/pypi/v/evernote-to-gdrive)](https://pypi.org/project/evernote-to-gdrive/)
[![Python >=3.10](https://img.shields.io/pypi/pyversions/evernote-to-gdrive)](https://pypi.org/project/evernote-to-gdrive/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

A migration tool that converts an [evernote-backup](https://github.com/vzhd1701/evernote-backup) `.enex` export into Google Drive or a local folder tree. Preserves your notebook and stack hierarchy, attachments, tags, timestamps, inter-note links, and web-clip fidelity.

> **Not a sync tool.** Run it once to migrate, then you're done.


## Features 

- Mirrors your **Stack / Notebook / Note** hierarchy in Google Drive or on disk
- **Two output modes** — Google Drive (Google Docs) or local (`.docx`)
- **Smart per-note layout** — text-only → single doc · single attachment → raw file · mixed → doc + numbered siblings
- **Formatting preserved** — headings, bold, italic, tables, lists, font sizes and colors
- **Inline image embedding** — JPEG/PNG/GIF/WebP, checkboxes preserved
- **Inter-note links** — links between notes are preserved (Google Doc URLs in gdrive mode; relative .docx links in local mode)
- **Web clips** — rendered as "Reader view" PDFs
- **Resume-safe** — interrupted runs pick up where they left off
- **Analyze mode** — pre-flight inspection: analyze an export for note counts, tag census, MIME breakdown, and much more.


## Platform support

macOS, Windows, and Linux.  


## Overview


1. [Export your Evernote data](#export-your-evernote-data)

2. [Install the package](#install-the-package)

3. [Set up Google credentials](#set-up-google-credentials) (gdrive mode)

4. [Migrate](#migrate-to-google-drive)
 

## Setup

### Export your Evernote data

The tool takes a directory of `.enex` files as its input — one file per notebook. Notebooks that belong to a stack should be grouped inside a subdirectory named after the stack; the tool uses that folder structure to reconstruct your stack hierarchy at the destination.

**Recommended — `evernote-backup`** (whole account, resumable, stack-aware):

```bash
pipx install evernote-backup
evernote-backup init-db
evernote-backup sync
evernote-backup export ./export
```

This produces an `./export/` tree where stack-named subdirectories contain one `.enex` per notebook — exactly the layout `evernote-to-gdrive` expects.
See the [evernote-backup docs](https://github.com/vzhd1701/evernote-backup) for more details.

**Alternative — Evernote's built-in export:**
Right-click a notebook → *Export Notes* → `.enex`. Only practical for a small number of notebooks. Drop the files into a folder and pass that as input.

You can pass either a directory of `.enex` files (optionally grouped into stack-named subdirectories) or a single `.enex` file.

### Install

Two options — pick one. 

#### Option 1: Standalone binary (no Python required)

Pre-built single-file binaries for macOS, Windows, and Linux are attached to each [GitHub Release](https://github.com/meizy/evernote-to-gdrive/releases/latest). Download the archive for your OS and extract it — you'll get a single `evernote-to-gdrive` executable.

```bash
# macOS / Linux
tar -xzf evernote-to-gdrive-*.tar.gz
chmod +x evernote-to-gdrive
./evernote-to-gdrive install-browsers   # only needed for web clips
```

> On macOS, if you get a "cannot be verified" error - remove the quarantine attribute:
> ```bash
> xattr -d com.apple.quarantine ./evernote-to-gdrive
> ```

On Windows, unzip the archive, then from that folder:

```powershell
.\evernote-to-gdrive.exe install-browsers   # only needed for web clips
```

#### Option 2: pipx (requires Python 3.10+)

[pipx](https://pipx.pypa.io) installs the tool in an isolated environment and makes the `evernote-to-gdrive` command globally available without touching your other Python packages.

If you don't have pipx yet:
```bash
pip install pipx
python -m pipx ensurepath
```
Then restart your terminal.

```bash
pipx install evernote-to-gdrive
evernote-to-gdrive install-browsers
```

You can skip the install-browsers command if you don't have web clips in your evernote.

### Set up Google credentials

> Migrating to a **local folder** (`--output local`)? Skip this section — no Google account needed.

If you're familiar with Google Cloud Console, the short version follows. Otherwise, follow the step-by-step walkthrough instructions: [Google credentials setup](./docs/google-credentials-setup.md). The short version:

1. Create a project, enable the **Google Drive API**, create a **Desktop app** OAuth 2.0 client, and add yourself as a test user.
2. Download the client JSON and save it as `client_secrets.json` in the folder where you'll run the tool.
3. Authenticate once:
   ```bash
   evernote-to-gdrive auth
   ```
   A browser window will open for consent. Credentials are cached as `token.json` in the same folder and reused on subsequent runs.


## Usage

Once you have your `.enex` export and (for gdrive mode) your credentials in place:

### Inspect before migrating: 

This is optional, but always a good first step

```bash
evernote-to-gdrive analyze ./export --all
```

### Migrate to Google Drive:

By default, the tool will migrate your notes to Google Drive.

```bash
evernote-to-gdrive migrate ./export
```

Notes are created under a folder called `Evernote Migration` in your My Drive. Use `--dest` to choose a different location.

> **Migration time** — gdrive mode is throttled to ~3 Drive API writes/second. Actual duration depends on note count, attachment sizes, and network speed. As a rough reference, ~1,000 notes took about 1 hour. Local mode is much faster: less than a minue for 1,000 notes.

### Migrate to a local folder (no Google account needed):

```bash
evernote-to-gdrive migrate ./export --output local
```

Files are written to an `Evernote Migration` folder in the current directory. Use `--dest` to choose a different path.

### Migrate a subset:

```bash
evernote-to-gdrive migrate ./export --notebook "Recipes"
evernote-to-gdrive migrate ./export --stack "Work" --stack "Personal"
```

## Output 

### Folder structure

The destination mirrors your Evernote **Stack → Notebook → Note** hierarchy. Notebooks that belong to a stack live inside a folder named after the stack; notebooks without a stack sit directly under the destination root. The root is a folder in **My Drive** in gdrive mode, or a directory on disk in local mode — in both cases controlled by `--dest` (default: `Evernote Migration`).

Within each notebook folder, every note becomes one or more files according to the rules in the next section. A concrete example:

```
Evernote Migration/                   ← destination root (--dest)
├── World Cuisines/                   ← stack folder
│   ├── Italian/                      ← notebook inside the stack
│   │   ├── Pasta Basics              ← text-only note → single doc
│   │   ├── Pasta Shapes.pdf          ← attachment-only, single file → raw file
│   │   ├── Homemade Tagliatelle      ← text + inline images → single doc (images embedded)
│   │   ├── Ragù Bolognese_0          ← text + non-image attachments → doc ...
│   │   ├── Ragù Bolognese_1.pdf      ← ... + numbered sibling file
│   │   └── Ragù Bolognese_2.mp4      ← ... + numbered sibling file
│   └── Japanese/                     ← another notebook in the same stack
│       └── Dashi Stock
├── Baking/                           ← another stack
│   └── Breads/
│       ├── Sourdough Starter
│       └── Focaccia
└── Kitchen Basics/                   ← notebook with no stack (directly under root)
    └── Pantry Essentials
```

In **gdrive mode**, docs are Google Docs (no file extension shown above). In **local mode**, the same docs are written as `.docx` files (`Pasta Basics.docx`, `Ragù Bolognese_0.docx`, …).

### Note to File conversion

Each note is migrated as one or more files depending on its content:

| Note content | Output |
|---|---|
| Text only (no attachments) | Single document |
| Single attachment, no text | The raw file as-is (`myNote.pdf`, `myNote.png`, ...) |
| Text with images | Single document with images embedded inline |
| Text with non-image attachments | Document + sibling files numbered `_1`, `_2`, … (`myNote_0` + `myNote_1.pdf` + `myNote_2.pdf`) |
| Multiple non-image attachments, no text | A document with links to siblings (default), or just the sibling files with `--attachments files` |

Images (JPEG, PNG, GIF, WebP) are always embedded in the document. Non-image attachments (PDFs, audio, etc.) are always written as separate sibling files alongside the document — except for the last case above, where `--attachments files` skips creating the links document entirely.

### Tags

Tags are preserved differently depending on the output mode:

- **Gdrive mode** — written into the Drive file's **description** field (alongside `Created:`, `Updated:`, and source URL), not into the document body. The same description is also set on sibling attachment files.
- **Local mode** — written as a `[tag:Foo, tag:Bar]` line at the top of the document body. Not present on raw attachment files or sibling files.

To omit tags from the output entirely:

```bash
evernote-to-gdrive migrate ./export --no-tags
```

### Note timestamps

**Gdrive mode** — the Drive API does not allow setting a file's creation time. Drive's `modifiedTime` can be set to either the note's `updated` or `created` time, depending on the `--gdrive-modified` flag (default `created`):

```bash
evernote-to-gdrive migrate ./export --gdrive-modified created   # default: note's original creation date
evernote-to-gdrive migrate ./export --gdrive-modified updated   # note's last-modified date
```

Both `Created:` and `Updated:` are written into the file's **description** field

**Local mode** — file `mtime` is set to the note's `updated` (falling back to `created`). On macOS and Windows the file's **birth/creation time** is also set to the note's `created`; on Linux only `mtime` is set. Applies to the main document, raw single-file attachments, and all sibling files.

### Source URL

When a note has a source URL (set by the Evernote Web Clipper, or manually), it is written as the first line of the generated PDF file. In gdrive mode it is also written into the Drive file's **description** field (alongside `Created:`, `Updated:`, and tags).

### RTL language support

Notes written in Hebrew, Arabic, or other right-to-left scripts are rendered with correct text direction in both gdrive and local output.

### What you keep / what you lose

Most Evernote content migrates cleanly, including notebook structure, note body content, formatting, attachments, timestamps, and links between notes when they can be matched.

There are a few limitations to be aware of:

- Tags - are stored in the file Description (gdrive) and as the first line in docx (local).
- Metadata - Author and geolocation, Tasks and Reminders - not preserved.
- Encrypted Evernote content - removed during migration.
- external image URLs - Some web clips may reference external image URLs that are not embedded in the export. When that happens, those images cannot be preserved.
- Inter-note links - links are matched by note title. If the target note cannot be matched by title, the link will remain unresolved. This can happen if the linked note was renamed after the link was created. 

Additional notes:

- Web clips are converted into a clean reading view, so their layout may differ from how they looked in Evernote.

In gdrive mode:

- Timestamps - Google Drive does not allow the original Evernote creation time to be stored as the file creation timestamp. The Drive "Date modified" value is set from the Evernote created time by default, or from the updated time if you use `--gdrive-modified updated`.
  - Both the original Evernote `Created:` and `Updated:` timestamps are written to the Drive file description.
- images limit - Google Docs is limited to 100 embedded images per note. Any images after the 100th are skipped with a warning. Local mode does not have this limit.


## Analyze

`analyze` inspects your `.enex` export without uploading anything — useful for gauging scope and spotting issues before migrating. Default (no flags) prints a summary table. Combine any of the flags below; sections print in the order you list them.

Example flags:
```bash
evernote-to-gdrive analyze ./export                              # summary (default)
evernote-to-gdrive analyze ./export --all                        # every report
evernote-to-gdrive analyze ./export --report-counts              # note counts per notebook
evernote-to-gdrive analyze ./export --list-dups                  # notes with duplicate titles
evernote-to-gdrive analyze ./export --findnote "Carbonara"       # which notebook has a note
evernote-to-gdrive analyze ./export --all --write-csv ./reports  # write output to csv files
```

See the Reference section below for the full reference.


## Advanced

### Filtering partial migrations

`--stack`, `--notebook`, and `--note` are repeatable and composable:

```bash
evernote-to-gdrive migrate ./export --stack "Work"
evernote-to-gdrive migrate ./export --notebook "Recipes" --notebook "Travel"
evernote-to-gdrive migrate ./export --notebook "Recipes" --note "Pasta Carbonara"
```

Useful for incremental runs or for re-migrating a single notebook after a change.

### Resume and force re-export

By default the tool never re-uploads a note that already exists at the destination — interrupted runs resume automatically. To override:

```bash
evernote-to-gdrive migrate ./export --force
```

### Duplicate note titles

Evernote can have two notes with the same title in a notebook. Both are migrated — neither is skipped:

- **Local mode** — the second, third, … get ` (2)`, ` (3)`, … appended to the filename to stay unique on disk.
- **Gdrive mode** — all copies keep the original name. Google Drive allows multiple files with the same name in the same folder, which matches how Evernote itself treats duplicates.

To see notes with same title before running a migration:

```bash
evernote-to-gdrive analyze ./export --list-dups
```

### Web clips

The original page's URL is added as a source bar at the top of the PDF (along with the date it was saved), so you can always jump back to the source.

Notes that originated from a web clip are run through a Readability.js + headless Chromium pipeline to produce a clean, "Reader View"-style PDF — similar to Chrome or Safari Reader View. Because the page is re-flowed rather than reproduced verbatim, **the result will not look exactly like what Evernote displayed** — layout, fonts, and non-article chrome are intentionally discarded in favor of a readable article view.

Requires `playwright install chromium` (see Install).

```bash
evernote-to-gdrive migrate ./export --web-clip pdf          # default
evernote-to-gdrive migrate ./export --clip-theme dark       # PDF will be in dark theme
evernote-to-gdrive migrate ./export --web-clip doc          # Google Doc or .docx instead
```

### Inter-note links

Links between notes are preserved in both output modes.

- **Matching is title-based** — ENEX exports have no note GUIDs, so notes that were renamed between export and migration will lose their link targets.
- **Unresolved links** appear as `[link to "title" not resolved]`, warnings printed to the console.
- **Two-pass approach** — pass 1 migrates all notes, pass 2 rewrites the links. Inter-note links only become live after pass 2 completes.
- **Single note** runs (`--note`) will not resolve links to other notes.
- **Gdrive mode** — links become clickable Google Doc or Drive file URLs.
- **Local mode** — links become relative paths between `.docx` files, clickable in Word/LibreOffice. Moving the entire output folder preserves them; uploading an individual `.docx` to Google Drive does not.

To opt out:

```bash
evernote-to-gdrive migrate ./export --skip-note-links
```

### Attachments layout

Applies only to notes that contain multiple non-image attachments and no text. By default a document is created with links to the sibling files. To skip that document and output only the raw files:

```bash
evernote-to-gdrive migrate ./export --attachments files
```

### Logging

```bash
evernote-to-gdrive migrate ./export --log-file migration.csv   # per-note CSV log
evernote-to-gdrive migrate ./export --verbose                  # per-note progress lines
```

### Custom credentials location

By default credentials are read from the current working directory. Use `--secrets-folder` to point to a different directory — it must contain `client_secrets.json` (and `token.json` after the first `auth` run):

```bash
evernote-to-gdrive auth    --secrets-folder /path/to/creds
evernote-to-gdrive migrate ./export --secrets-folder /path/to/creds
```


## Reference

Type `evernote-to-gdrive <command> --help` to get the same reference below.

### Commands

| Command | Description |
|---------|-------------|
| `auth` | Authorize with Google Drive and save `token.json` |
| `analyze INPUT` | Inspect `.enex` files and print statistics (no upload) |
| `migrate INPUT` | Migrate notes to Google Drive or a local folder |

`INPUT` is a path to a single `.enex` file or to a folder containing `.enex` files (optionally grouped into stack-named subdirectories).

### `auth` options

Authenticate with Google and save `token.json`.

| Flag | Default | Description |
|---|---|---|
| `--secrets-folder PATH` | current directory | Folder containing `client_secrets.json` / `token.json`. |

### `migrate` options

Migrate Evernote notes to Google Drive (`gdrive`) or a local folder (`local`).

**Destination**

| Flag | Default | Description |
|---|---|---|
| `--output {gdrive\|local}` | `gdrive` | `gdrive`: upload to Google Drive. `local`: save to a local folder. |
| `--dest PATH` | `Evernote Migration` | Drive folder path (gdrive) or local folder (local),  can be multi-level (`folder1/folder2`)|
| `--secrets-folder PATH` | current directory | Folder containing `client_secrets.json` / `token.json` (gdrive only). |
| `--log-file PATH` | None | Write migration log (CSV) to this file. |

**Filtering**

| Flag | Description |
|---|---|
| `--stack NAME` | Only migrate notebooks in this stack (repeatable). |
| `--notebook NAME` | Only migrate this notebook (repeatable). |
| `--note TITLE` | Only migrate the note with this exact title (requires `--notebook`). |

**Content handling**

| Flag | Default | Description |
|---|---|---|
| `--attachments {doc\|files}` | `doc` | Attachment-only notes with multiple non-image attachments: `doc` creates a doc listing siblings, `files` writes sibling files only. |
| `--web-clip {pdf\|doc}` | `pdf` | Output format for web clip notes: `pdf` renders a Reader-style PDF, `doc` creates a doc from cleaned HTML. |
| `--clip-theme {light\|dark}` | `light` | Theme for web clip rendering. |
| `--no-tags` | | Do not include Evernote tags in the output. |
| `--skip-note-links` | | Skip rewriting `evernote:///` inter-note links. |
| `--gdrive-modified {created\|updated}` | `created` | Timestamp used as Drive `modifiedTime` (gdrive only). |

**Run control**

| Flag | Description |
|---|---|
| `--force` | Skip existence checks and re-export all notes, overwriting existing files. |
| `--verbose` | Print a line for each note instead of a progress bar. |

### `analyze` options

Inspect `.enex` files and report statistics (no upload).

**Summary reports**

| Flag | Description |
|---|---|
| `--report-summary` | Total notes, notebooks, and stacks. |
| `--report-class` | Note classification breakdown (text-only, attachment-only, mixed). |
| `--report-counts` | Note counts per notebook. |
| `--report-top-size` | Top notebooks by attachment size. |
| `--report-mime` | Attachment MIME types and totals. |

**List reports**

| Flag | Description |
|---|---|
| `--list-clips` | All web clips (notes with a source URL). |
| `--list-dups` | Notes with duplicate titles within the same notebook. |
| `--list-empty` | Empty notes (no text and no attachments). |
| `--list-tags` | Tags with a count of notes per tag, sorted by count. |
| `--list-attachments` | Notes with attachments: counts of images, PDFs, other files per note. |
| `--list-links-notebooks` | Inter-note link counts per notebook, sorted by count. |
| `--list-links-notes` | Inter-note link counts per note, sorted by notebook then note name. |

**Lookup**

| Flag | Description |
|---|---|
| `--findnote TITLE` | Which notebook(s) contain a note with this title. |
| `--mime MIME_TYPE` | Notes with an attachment of this MIME type (e.g. `application/msword`). |

**Shortcuts**

| Flag | Description |
|---|---|
| `--all` | Show all report sections. |
| `--write-csv DIR` | Also write each report table as a CSV file in `DIR`. |


## Getting help

If you found a bug or have a feature request, please [open a new issue](https://github.com/meizy/evernote-to-gdrive/issues).

If you have a question about the tool or have difficulty using it, you are welcome to the [discussions page](https://github.com/meizy/evernote-to-gdrive/discussions).


## License

MIT — see [LICENSE](LICENSE).
