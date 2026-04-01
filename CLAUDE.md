# evernote to gdrive

this project migrates an evernote export to the local file system or to google drive.

## requirements
see the `REQUIREMENTS.md` doc.

## python guidelines
- no file should exceed 300 lines
- no function should exceed 50 lines, 30 should be the limit in most cases

## regex guidelines
you make a lot of regex mistakes. whenever you use a new regex, make sure to test it and validate it works as expected.

## debugging 
whenever you suspect an issue, make sure to check the original note data in the enex format 
and validate your assumption, before making changes.

you can find a notebook where a note resides by using evernote-to-gdrive analyze --findnote