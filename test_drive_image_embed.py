"""
Test: upload an image to Drive, make it publicly readable, embed it inline
in a Google Doc (surrounded by text), then delete the image.

Run with:
    python test_drive_image_embed.py
"""

from __future__ import annotations

import io
from pathlib import Path

from googleapiclient.http import MediaIoBaseUpload

from src.evernote_to_google.auth import get_services

IMAGE_PATH = Path("test-data/en.jpeg")
MIME = "image/jpeg"

BEFORE_TEXT = (
    "This is a test document.\n\n"
    "The following image was uploaded to Google Drive, made publicly accessible, "
    "and embedded here using the Google Docs insertInlineImage API.\n\n"
)

AFTER_TEXT = (
    "\n\nAbove you can see the embedded image. "
    "After this test the source image file was deleted from Drive.\n"
)


def upload_image_public(drive, data: bytes, name: str) -> tuple[str, str]:
    """Upload image to Drive, grant public read access. Returns (file_id, url)."""
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=MIME, resumable=False)
    file = drive.files().create(
        body={"name": name},
        media_body=media,
        fields="id",
    ).execute()
    file_id = file["id"]

    # Make publicly readable (anyone with link)
    drive.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"},
    ).execute()

    # Direct download URL that Google's servers can fetch
    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    return file_id, url


def create_doc_with_embedded_image(docs, drive, image_url: str) -> str:
    """Create a Google Doc with text before and after an inline image. Returns doc_id."""
    # Create empty doc
    doc = docs.documents().create(body={"title": "Test: Drive Image Embed"}).execute()
    doc_id = doc["documentId"]

    # We insert in reverse order (everything at index 1) so the doc reads top-to-bottom:
    #   BEFORE_TEXT | image | AFTER_TEXT
    requests = [
        # 3. Insert AFTER_TEXT last (goes to bottom)
        {"insertText": {"location": {"index": 1}, "text": AFTER_TEXT}},
        # 2. Insert image in the middle
        {
            "insertInlineImage": {
                "location": {"index": 1},
                "uri": image_url,
                "objectSize": {
                    "width": {"magnitude": 300, "unit": "PT"},
                    "height": {"magnitude": 200, "unit": "PT"},
                },
            }
        },
        # 1. Insert BEFORE_TEXT first (ends up at top)
        {"insertText": {"location": {"index": 1}, "text": BEFORE_TEXT}},
    ]

    docs.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": requests},
    ).execute()

    return doc_id


def main():
    print("Authenticating...")
    drive, docs = get_services()

    image_data = IMAGE_PATH.read_bytes()
    print(f"Uploading {IMAGE_PATH} ({len(image_data):,} bytes) to Drive...")
    image_id, image_url = upload_image_public(drive, image_data, "test-embed-image.jpeg")
    print(f"  Image file ID : {image_id}")
    print(f"  Public URL    : {image_url}")

    print("Creating Google Doc with embedded image...")
    try:
        doc_id = create_doc_with_embedded_image(docs, drive, image_url)
        print(f"  Doc created   : https://docs.google.com/document/d/{doc_id}/edit")
    finally:
        print("Deleting image from Drive...")
        drive.files().delete(fileId=image_id).execute()
        print("  Image deleted.")


if __name__ == "__main__":
    main()
