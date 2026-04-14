"""PyInstaller entry shim — PyInstaller needs a plain script, not a console-script."""

from evernote_to_gdrive.cli import main

if __name__ == "__main__":
    main()
