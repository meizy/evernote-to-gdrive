"""
Run this script once to generate static ENEX fixture files for e2e tests.

    python tests/setup/generate_fixtures.py

Outputs files into tests/input/. Commit the results.
"""

from pathlib import Path

from enex_builder import (
    TEST_JPEG,
    TEST_PDF,
    TEST_PDF_2,
    TEST_PNG,
    make_note,
    write_enex,
)

FIXTURES = Path(__file__).parent.parent / "input" / "sanity"
FIXTURES_EXTENDED = Path(__file__).parent.parent / "input" / "extended"
FIXTURES_WEBCLIP = Path(__file__).parent.parent / "input" / "webclip"


def main() -> None:
    FIXTURES.mkdir(exist_ok=True)
    (FIXTURES / "Test Stack").mkdir(exist_ok=True)
    FIXTURES_EXTENDED.mkdir(exist_ok=True)
    FIXTURES_WEBCLIP.mkdir(exist_ok=True)

    sanity_notes = [
        make_note("Text Only", "<h1>Heading One</h1><div>Normal paragraph</div><div><b>Bold text</b> and <i>italic text</i></div>"),
        make_note("Single Image Attachment", "", [(TEST_PNG, "image/png", "photo.png")]),
        make_note("Single PDF Attachment", "", [(TEST_PDF, "application/pdf", "doc.pdf")]),
        make_note("Multiple Image Attachments", "", [
            (TEST_PNG, "image/png", "img1.png"),
            (TEST_JPEG, "image/jpeg", "img2.jpg"),
        ]),
        make_note("Multiple PDF Attachments", "", [
            (TEST_PDF, "application/pdf", "doc1.pdf"),
            (TEST_PDF_2, "application/pdf", "doc2.pdf"),
        ]),
        make_note("Mixed Attachments No Text", "", [
            (TEST_PNG, "image/png", "photo.png"),
            (TEST_PDF, "application/pdf", "doc.pdf"),
        ]),
        make_note("Text With Image", "Some text", [(TEST_PNG, "image/png", "photo.png")]),
        make_note("Text With PDF", "Some text", [(TEST_PDF, "application/pdf", "doc.pdf")]),
        make_note("Text With Mixed Attachments", "Some text", [
            (TEST_PNG, "image/png", "photo.png"),
            (TEST_PDF, "application/pdf", "doc.pdf"),
        ]),
        make_note("Text Only With Tags", "Tagged note", tags=["tag1", "tag2"]),
        # Inter-note link notes: target must come before source so both exist when pass 2 runs
        make_note("Inter-Note Link Target", "This is the link target."),
        make_note(
            "Inter-Note Link Source",
            'This note links to <a href="evernote:///view/12345/s1/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee">Inter-Note Link Target</a>.',
        ),
        # Duplicate of text+mixed to test duplicate title handling
        make_note("Text With Mixed Attachments", "Some text", [
            (TEST_PNG, "image/png", "photo.png"),
            (TEST_PDF, "application/pdf", "doc.pdf"),
        ]),
    ]

    webclip_notes = [
        make_note("Web Clip Plain", "Article text", source_url="https://example.com/article"),
        make_note("Web Clip With Image", "Article text",
                  [(TEST_PNG, "image/png", "photo.png")],
                  source_url="https://example.com/gallery"),
    ]

    extended_notes = [
        make_note("note: with special char", "Special character in title"),
        make_note("Custom Mime Attachment", "", [(b"custom data", "application/x-custom", "data.bin")]),
        make_note("Encrypted Content", 'Visible text<en-crypt>secret stuff</en-crypt>Also visible'),
        make_note("Checkbox Note", '<en-todo checked="true"/>Buy milk <en-todo/>Call dentist'),
    ]

    write_enex(FIXTURES / "Sanity Notebook.enex", sanity_notes)
    write_enex(FIXTURES / "Test Stack" / "Stacked Notebook.enex",
               [make_note("Note In Stack", "Stack note")])
    write_enex(FIXTURES_EXTENDED / "Extended Notebook.enex", extended_notes)
    write_enex(FIXTURES_WEBCLIP / "Web Clips.enex", webclip_notes)

    print(f"Generated fixtures in {FIXTURES}")
    for p in sorted(FIXTURES.rglob("*.enex")):
        print(f"  {p.relative_to(FIXTURES)}")
    print(f"\nGenerated extended fixtures in {FIXTURES_EXTENDED}")
    for p in sorted(FIXTURES_EXTENDED.rglob("*.enex")):
        print(f"  {p.relative_to(FIXTURES_EXTENDED)}")
    print(f"\nGenerated webclip fixtures in {FIXTURES_WEBCLIP}")
    for p in sorted(FIXTURES_WEBCLIP.rglob("*.enex")):
        print(f"  {p.relative_to(FIXTURES_WEBCLIP)}")


if __name__ == "__main__":
    main()
