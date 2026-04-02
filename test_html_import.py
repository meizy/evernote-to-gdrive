"""
Test whether Google Drive HTML import embeds <img src="..."> inline.

Steps:
  1. Upload test-data/en.jpeg to Drive (publicly readable)
  2. Create an HTML doc with <img src="[public_url]"> at a specific position
  3. Import the HTML as a Google Doc
  4. Print the resulting doc ID so you can open it and check manually
  5. Optionally clean up
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from googleapiclient.http import MediaInMemoryUpload
from evernote_to_google.auth import get_services

IMAGE_PATH = Path(__file__).parent / "test-data" / "en.jpeg"
TEST_FOLDER_NAME = "html-import-test"


def main():
    drive = get_services()

    # 1. Create a temporary test folder
    folder = drive.files().create(
        body={"name": TEST_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"},
        fields="id",
    ).execute()
    folder_id = folder["id"]
    print(f"Created folder: https://drive.google.com/drive/folders/{folder_id}")

    # 2. Upload the image and make it publicly readable
    image_data = IMAGE_PATH.read_bytes()
    img_file = drive.files().create(
        body={"name": "en.jpeg", "parents": [folder_id]},
        media_body=MediaInMemoryUpload(image_data, mimetype="image/jpeg"),
        fields="id",
    ).execute()
    img_id = img_file["id"]
    drive.permissions().create(
        fileId=img_id,
        body={"role": "reader", "type": "anyone"},
    ).execute()
    public_url = f"https://drive.google.com/uc?export=download&id={img_id}"
    print(f"Uploaded image: {public_url}")

    # 3. Build HTML with the image inline between paragraphs
    html = f"""<!DOCTYPE html>
<html>
<body>
  <h1>HTML Import Test</h1>
  <p>This paragraph is <strong>before</strong> the image.</p>
  <p><img src="{public_url}" alt="test image" style="max-width:400px"/></p>
  <p>This paragraph is <em>after</em> the image.</p>
  <h2>Section Two</h2>
  <p>A link: <a href="https://example.com">example.com</a></p>
</body>
</html>"""

    # 4. Import the HTML as a Google Doc
    doc_file = drive.files().create(
        body={
            "name": "html-import-test-doc",
            "mimeType": "application/vnd.google-apps.document",
            "parents": [folder_id],
        },
        media_body=MediaInMemoryUpload(html.encode("utf-8"), mimetype="text/html"),
        fields="id",
    ).execute()
    doc_id = doc_file["id"]
    print(f"\nCreated doc: https://docs.google.com/document/d/{doc_id}/edit")
    print("\nOpen the doc and check:")
    print("  - Is the image embedded inline between the two paragraphs?")
    print("  - Are H1/H2 headings styled correctly?")
    print("  - Is 'before' bold and 'after' italic?")
    print("  - Is 'example.com' a clickable link?")
    print(f"\nTo clean up, delete the folder: https://drive.google.com/drive/folders/{folder_id}")


if __name__ == "__main__":
    main()
