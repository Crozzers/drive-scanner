# Drive scanner

Read a drive as a stream of bytes and extract files by looking for magic numbers.
The use case is for HDDs or SSDs that are perfectly functional but the file allocation table has been erased/corrupted.
This script bypasses the file table and recovers files from the raw drive data.


Currently supported files are:
- JPG
- PNG
- ZIP (includes .docx, .xlsx, .pptx)
- PDF

# Usage

This script works best on Linux, as it allows free access to the whole drive. I recommend creating a live USB and running the script from there.

```
> python scan.py --help
usage: scan.py [-h] [--fresh] drive

positional arguments:
  drive       the path to the drive to scan

options:
  -h, --help  show this help message and exit
  --fresh     start from scratch, ignoring the cached index

> python scan.py /dev/sda1
...
```

# How it works

The drive is opened as a binary stream, bypassing the usual file system and the data is read in chunks.
If the chunk contains a known magic number, the next few chunks are read and we try to parse a valid file out of the stream.
If successful, we record the location in the stream that the file occured and move on. At regular intervals during the scan (and at the end),
we write all the discovered files to disk.

On some files (zip) we also postprocess them to convert them to a more specific file format (docx, xlsx, pptx, etc) using the `file` bash util.
