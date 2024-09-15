import argparse
import os
import re
import subprocess
import zipfile
from io import BufferedReader, BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image

drive: str

PNG_OPEN_SIG = b'\x89\x50\x4e\x47\x0d\x0a\x1a\x0a'
PNG_END_SIG = b'\x00\x00\x00\x00\x49\x45\x4E\x44\xAE\x42\x60\x82'

JPG_OPEN_SIG = b'\xff\xd8'
JPG_OPEN_SIG1 = b'\xFF\xD8\xFF\xdb'
JPG_OPEN_SIG2 = b'\xff\xd8\xff\xe0'
JPG_OPEN_SIG3 = b'\xff\xd8\xff\xee'
JPG_OPEN_SIG4 = b'\xff\xd8\xff\xe1'
JPG_SOS_HEADER = b'\xff\xda'
JPG_END_SIG = b'\xff\xd9'

ZIP_OPEN_SIG = b'\x50\x4b\x03\x04'
ZIP_END_SIG = b'\x50\x4b\x05\x06'

PDF_OPEN_SIG = b'%PDF-'
PDF_END_SIG = b'%%EOF'

CHUNK_SIZE = 1024
LAST_INDEX_WRITE = -1
LAST_FILE_SAVE_INDEX = -1

os.makedirs('recovered', exist_ok=True)

def parse_jpg(f: BufferedReader, file_start: int):
    '''f.seek(file_start)
    print(f.read(1024))
    import sys
    sys.exit(0)'''
    jpg_offset = file_start + 2
    while True:
        if (jpg_offset - file_start) > 5_000_000:
            # if offset has reached 5mb we've clearly missed an end marker and have to abandon the file
            jpg_offset = file_start
            break

        f.seek(jpg_offset)
        marker = f.read(2)

        if marker[0] != 0xff:
            # haven't read a marker. Something is wrong
            # jpg_offset += 1
            # no marker therefore not a jpg
            return -1

        if marker == JPG_END_SIG:
            # hit end marker
            jpg_offset += 2
            break

        if marker == JPG_OPEN_SIG or 0xd0 <= marker[1] <= 0xd7:
            # skip markers with no length
            jpg_offset += 2
            continue

        s_bytes = f.read(2)
        size = int.from_bytes(s_bytes, 'big')
        jpg_offset += 2 + size

        if marker == JPG_SOS_HEADER:
            # start of scan header length bytes include the length of the header, but not the data.
            # this just runs until the end of the file
            f.seek(jpg_offset)
            sos_offset = 0
            while sos_offset < 5_000_000:
                chunk = f.read(1024)
                index = chunk.find(JPG_END_SIG)
                if index > -1:
                    jpg_offset += sos_offset + index + 2
                    # EOF marker found
                    return jpg_offset
                sos_offset += CHUNK_SIZE

            # EOF marker not found. Exit
            return -1

    if jpg_offset == file_start:
        # error. No file
        return -1
    else:
        return jpg_offset


def parse_png(f: BufferedReader, file_start: int):
    png_offset = file_start + len(PNG_OPEN_SIG) - 1
    '''print(f.read(1024))
    import sys;sys.exit(0)'''
    f.seek(png_offset)
    chunk = f.read(len(PNG_END_SIG) * 2)

    while (png_offset - file_start) < 5_000_000:
        # whilst under 5mb
        index = chunk.find(PNG_END_SIG)
        if index > -1:
            file_end = png_offset + index + len(PNG_END_SIG)
            '''if (file_end - file_start) < 10_000:
                # png under 10kb probably invalid
                continue'''
            return file_end

        chunk = chunk[len(PNG_END_SIG):] + f.read(len(PNG_END_SIG))
        png_offset = f.tell() - len(PNG_END_SIG)

    return -1


def parse_zip(f: BufferedReader, file_start: int):
    zip_offset = file_start + len(ZIP_OPEN_SIG) - 1
    f.seek(zip_offset)
    chunk = f.read(len(ZIP_END_SIG) * 2)

    while zip_offset - file_start < 50_000_000:
        index = chunk.find(ZIP_END_SIG)
        if index > -1:
            file_end = zip_offset + index
            # add all the extra end metadata
            file_end += 20
            # parse comment section
            f.seek(file_end)
            file_end += int.from_bytes(f.read(2), 'little')
            return file_end


        chunk = chunk[len(ZIP_END_SIG):] + f.read(len(ZIP_END_SIG))
        zip_offset = f.tell() - len(ZIP_END_SIG)

    return -1


def parse_pdf(f: BufferedReader, file_start: int):
    pdf_offset = file_start + len(PDF_OPEN_SIG) - 1
    f.seek(pdf_offset)
    chunk = f.read(len(PDF_END_SIG) * 2)

    nested_count = 1

    while pdf_offset - file_start < 10_000_000:
        start_index = chunk.find(PDF_OPEN_SIG)
        end_index = chunk.find(PDF_END_SIG)
        if start_index > -1 and end_index > -1:
            if nested_count == 1 and end_index < start_index:
                # if the next start is outside of the current PDF being closed completely
                nested_count -= 1
            else:
                # otherwise, +=1 and -=1 to the nested index
                pass
        elif start_index > -1:
            nested_count += 1
        elif end_index > -1:
            nested_count -= 1

        if nested_count == 0:
            return pdf_offset + end_index + len(PDF_END_SIG)

        chunk = chunk[len(PDF_END_SIG):] + f.read(len(PDF_END_SIG))
        pdf_offset = f.tell() - len(PDF_END_SIG)

    return -1


def save_index(files):
    with open('index.txt', 'w') as f:
        for file_start, file_end, file_type in files:
            f.write(f'{file_start},{file_end},{file_type}\n')
    global LAST_INDEX_WRITE
    LAST_INDEX_WRITE = len(files)


def load_index():
    files = []
    if not os.path.isfile('index.txt'):
        return files
    with open('index.txt', 'r') as f:
        for line in f:
            try:
                file_start, file_end, extension = line.strip().split(',')
                files.append((int(file_start), int(file_end), extension))
            except Exception as e:
                print(f'Error parsing index file. Stopping. Err: {e}')

    return sorted(files, key=lambda f: f[0])


def image_is_valid(contents: bytes, index: int):
    try:
        img = Image.open(BytesIO(contents))
        # filter out invalid/corrupt files
        if img.verify() is not None:
            raise ValueError('invalid img')
        # filter out any icons
        if img.size[0] == img.size[1] and img.size[0] <= 64:
            return False
    except ValueError:
        return False
    except Exception as e:
        print(f'{index}: Invalid image: {e}')
        return False
    return True


def zip_is_valid(contents: bytes, index: int):
    try:
        zip = zipfile.ZipFile(BytesIO(contents), 'r')
        if zip.testzip() is not None:
            print(f'{index}: bad files in zip')
            return False
    except Exception as e:
        print(f'{index}: invalid zip: {e}')
        return False
    return True


def pdf_is_valid(contents: bytes, index: int):
    try:
        import pypdf
    except (ImportError, ModuleNotFoundError) as e:
        print(f'{index}: failed to import PDF parsing lib, cannot validate file: e')
        raise
        return False

    try:
        pypdf.PdfReader(BytesIO(contents))
    except Exception as e:
        print(f'{index}: invalid pdf: {e}')
        return False
    return True


def save_files(files, f: Optional[BufferedReader] = None):
    save_index(files)

    def _save(f: BufferedReader, files):
        global LAST_FILE_SAVE_INDEX
        print(f'Saving files, skipping first {LAST_FILE_SAVE_INDEX}')
        for index, (file_start, file_end, file_type) in enumerate(files):
            if index <= LAST_FILE_SAVE_INDEX:
                continue

            f.seek(file_start)
            contents = f.read(file_end - file_start)
            if (
                    (
                        file_type == 'zip'
                        and zip_is_valid(contents, index)
                    )
                    or (
                        file_type in ('png', 'jpg')
                        and image_is_valid(contents, index)
                    )
                    or (
                        file_type == 'pdf'
                        and pdf_is_valid(contents, index)
                    )
            ):
                folder = f'recovered/{file_type}'
                os.makedirs(folder, exist_ok=True)
                with open(f'{folder}/{index}.{file_type}', 'wb') as g:
                    g.write(contents)
                postprocess_file(f'recovered/{file_type}/{index}.{file_type}')
        LAST_FILE_SAVE_INDEX = index
        with open('last_write_index.txt', 'w') as g:
            g.write(str(LAST_FILE_SAVE_INDEX))

    if f:
        pos = f.tell()
        _save(f, files)
        f.seek(pos)
    else:
        with open(drive, 'rb') as f:
            _save(f, files)


def postprocess_file(file: str):
    '''
    Run `file` on a path to determine the "real" file type of the file and move it to an appropriate location
    '''
    path = Path(file)

    if path.suffix not in ('.zip',):
        return

    assert path.exists()
    file_type = subprocess.check_output(['file', str(path)]).decode().split(': ', 1)[1]
    if (regex := re.match(r'microsoft (\w+) 2007\+', file_type, re.I)):
        program = regex.group(1)
        ext = {
            'Word': 'docx', 'Excel': 'xlsx', 'PowerPoint': 'pptx'
        }.get(program, None)

        if ext:
            os.makedirs(path.parent.parent / 'office', exist_ok=True)
            os.rename(str(path), str(path.parent.parent / 'office' / f'{path.stem}.{ext}'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()

    parser.add_argument('drive', type=str, help='the path to the drive to scan')
    parser.add_argument('--fresh', action='store_true', default=False, help='start from scratch, ignoring the cached index')

    args = parser.parse_args()

    if args.fresh:
        if os.path.isfile('index.txt'):
            os.remove('index.txt')
        if os.path.isfile('last_write_index.txt'):
            os.remove('last_write_index.txt')

    drive = args.drive

    # pick up from where we left off
    files = load_index()
    LAST_INDEX_WRITE = len(files)
    if os.path.isfile('last_write_index.txt'):
        with open('last_write_index.txt', 'r') as f:
            LAST_FILE_SAVE_INDEX = int(f.read().strip())

    print(f'Starting scan with {len(files):,} indexes')
    if files:
        print(f'Index info: lowest: {files[0]}, highest: {files[-1]}')

    offset_print_count = files[-1][1] // 1_000_000_000 if files else 0
    with open(drive, 'rb') as f:
        offset = 0
        buf = None
        try:
            while True:
                if files and files[-1][1] > offset:
                    # file was found in last chunk. Seek to latest EOF and read from there
                    f.seek(files[-1][1])
                    buf = f.read(CHUNK_SIZE)
                elif buf:
                    # no files found in last chunk. Biggest sig we check for is 12 bytes, so keep last 11 (could be incomplete sig) and top up with the rest of the chunk
                    buf = buf[-11:] + f.read(CHUNK_SIZE)
                else:
                    # no files found and no existing buffer. We are starting fresh
                    buf = f.read(CHUNK_SIZE)

                if not buf:
                    print('Exit due to empty buffer')
                    break

                offset = f.tell() - len(buf)
                if offset // 1_000_000_000 > offset_print_count:
                    print(f'Offset: {offset:,}, files found: {len(files):,}')
                    offset_print_count += 1
                    save_files(files, f)

                file_start = -1
                file_end = -1

                # scan for zip files first since they can contain other file formats
                index = buf.find(ZIP_OPEN_SIG)
                if index > -1:
                    file_start = offset + index
                    file_end = parse_zip(f, file_start)
                    if file_end == -1:
                        print(f'Error parsing ZIP file at offset {file_start}')
                    else:
                        print(f'ZIP found - Index: {file_start, file_end}, Size: {file_end - file_start:,}')
                        files.append((file_start, file_end, 'zip'))
                        continue

                index = buf.find(PDF_OPEN_SIG)
                if index > -1:
                    file_start = offset + index
                    file_end = parse_pdf(f, file_start)
                    if file_end == -1:
                        print(f'Error parsing PDF at offset {file_start}')
                    else:
                        print(f'PDF found - Index: {file_start, file_end}, Size: {file_end - file_start:,}')
                        files.append((file_start, file_end, 'pdf'))
                        continue

                for marker in (JPG_OPEN_SIG1, JPG_OPEN_SIG2, JPG_OPEN_SIG3, JPG_OPEN_SIG4):
                    index = buf.find(marker)
                    if index > -1:
                        file_start = offset + index
                        file_end = parse_jpg(f, file_start)
                        if file_end == -1:
                            # error. File not found. Seek to just after file start
                            print(f'Error parsing JPG at offset {file_start}. Skip 4 bytes')
                            f.seek(offset + index + 4)
                            buf = b''
                            continue
                        else:
                            # file found. Advance the seek and break loop
                            print(f'JPG found - Index: {file_start, file_end}, Size: {file_end - file_start:,}')
                            files.append((file_start, file_end, 'jpg'))
                            break

                if file_end != -1:
                    # file was found in JPG loop. Restart loop to refresh buffer
                    continue

                index = buf.find(PNG_OPEN_SIG)
                if index > -1:
                    file_start = offset + index
                    file_end = parse_png(f, file_start)

                    if file_end != -1:
                        print(f'PNG found - Index: {file_start, file_end}, Size: {file_end - file_start:,} ')
                        files.append((file_start, file_end, 'png'))
                    else:
                        print(f'File size limit exceeded when looking for PNG at index {file_start}')

                if file_end != -1:
                    if len(files) - LAST_INDEX_WRITE > 100:
                        save_index(files)
        except KeyboardInterrupt:
            print('KeyboardInterrupt. Saving files')

    save_files(files)
