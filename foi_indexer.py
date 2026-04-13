"""
foi_indexer.py
==============
Reads all FOI folders, extracts request subjects and response text,
and builds a single foi_index.json for use in the FOI Tool.

Usage:
    python foi_indexer.py "C:\\Users\\User\\Downloads\\FOI responses\\FOI responses"

Output:
    foi_index.json  — upload this to your personal Claude to embed in the tool

Requirements:
    pip install python-docx openpyxl
"""

import os
import re
import sys
import json
import zipfile
import unicodedata
from pathlib import Path
from datetime import datetime

# ── Config ───────────────────────────────────────────────────
MIN_RESPONSE_CHARS = 80      # skip responses shorter than this
MAX_RESPONSE_CHARS = 8000    # truncate very long responses
SKIP_PREFIXES      = ('~$', 'thumbs', 'foi_s43')
SKIP_EXTENSIONS    = {'.msg', '.db', '.lnk', '.py', '.oft', '.vsdx', '.rtf'}
SKIP_FOLDERS       = {'foi test', 'foi test 2025'}
PUBLIC_LINK_PATTERNS = [
    r'https?://\S+',
    r'www\.\S+',
    r'gov\.uk\S*',
]
SEE_ATTACHED_PATTERNS = [
    r'please (find |see )?attached',
    r'see attached',
    r'refer to (the )?attached',
    r'as per attached',
    r'in the attached',
]
# ────────────────────────────────────────────────────────────

FOI_NUM_RE = re.compile(r'(?:FOI|EIR)[_\s\-]?0*(\d{3,6})', re.IGNORECASE)
RESPONSE_RE = re.compile(r'response|draft\s*response|updated\s*response', re.IGNORECASE)
DRAFT_RE    = re.compile(r'draft', re.IGNORECASE)


def clean(text):
    """Normalise unicode, collapse whitespace."""
    text = unicodedata.normalize('NFKD', text)
    text = text.encode('ascii', 'ignore').decode('ascii')
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def extract_docx_text(path):
    """Extract plain text from a .docx file using zipfile (no library needed)."""
    try:
        with zipfile.ZipFile(path, 'r') as z:
            if 'word/document.xml' not in z.namelist():
                return ''
            xml = z.read('word/document.xml').decode('utf-8', errors='ignore')
        # Paragraphs → newlines, strip all XML tags
        xml = re.sub(r'<w:p[ >]', '\n<w:p>', xml)
        xml = re.sub(r'<[^>]+>', '', xml)
        xml = re.sub(r'&amp;', '&', xml)
        xml = re.sub(r'&lt;', '<', xml)
        xml = re.sub(r'&gt;', '>', xml)
        xml = re.sub(r'&quot;', '"', xml)
        xml = re.sub(r'&#x[0-9A-Fa-f]+;', ' ', xml)
        xml = re.sub(r'\n{3,}', '\n\n', xml)
        return xml.strip()
    except Exception:
        return ''


def extract_xlsx_text(path):
    """Extract text from all cells in an xlsx file."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        parts = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                row_text = ' '.join(str(c) for c in row if c is not None and str(c).strip())
                if row_text:
                    parts.append(row_text)
        return '\n'.join(parts)
    except Exception:
        return ''


def has_public_link(text):
    tl = text.lower()
    for pat in PUBLIC_LINK_PATTERNS:
        if re.search(pat, tl):
            return True
    return False


def extract_links(text):
    links = re.findall(r'https?://\S+', text)
    return [l.rstrip('.,;)') for l in links]


def is_see_attached(text):
    tl = text.lower()
    for pat in SEE_ATTACHED_PATTERNS:
        if re.search(pat, tl):
            return True
    return False


def get_foi_num(name):
    """Extract FOI number from a filename or folder name."""
    m = FOI_NUM_RE.search(name)
    if m:
        return m.group(1).lstrip('0') or '0'
    return None


def pick_best_response(candidates):
    """
    Given a list of (path, filename) pairs that are response documents,
    prefer: final > updated > draft. Within same tier, prefer .docx > .xlsx.
    """
    def tier(fname):
        fl = fname.lower()
        if 'draft' in fl: return 2
        if 'updated' in fl: return 1
        return 0  # final / plain response

    def ext_rank(path):
        return 0 if path.suffix.lower() == '.docx' else 1

    candidates.sort(key=lambda x: (tier(x[1]), ext_rank(x[0])))
    return candidates[0][0] if candidates else None


def process_folder(folder_path, foi_num):
    """
    Process one FOI folder. Returns a dict or None.
    """
    folder = Path(folder_path)
    all_files = list(folder.iterdir()) if folder.is_dir() else []

    # Filter to usable files
    usable = []
    for f in all_files:
        if not f.is_file(): continue
        if f.name.startswith(tuple(SKIP_PREFIXES)): continue
        if f.suffix.lower() in SKIP_EXTENSIONS: continue
        usable.append(f)

    if not usable:
        return None

    # Separate response files from request/other files
    response_files = []
    request_files  = []

    for f in usable:
        if f.suffix.lower() not in {'.docx', '.doc', '.xlsx', '.xls'}:
            continue
        if RESPONSE_RE.search(f.name):
            response_files.append((f, f.name))
        else:
            request_files.append(f)

    # Extract subject from request filename (remove FOI number prefix)
    subject = ''
    for f in request_files:
        name = f.stem
        name = re.sub(FOI_NUM_RE, '', name)
        name = re.sub(r'^[\s\-_â€"]+', '', name).strip()
        name = clean(name)
        if len(name) > 10:
            subject = name
            break

    # If no request file, try to get subject from folder name
    if not subject:
        fname = folder.name
        fname = re.sub(FOI_NUM_RE, '', fname)
        fname = re.sub(r'^[\s\-_]+', '', fname).strip()
        subject = clean(fname)

    # Pick best response document
    best_resp_path = None
    if response_files:
        best_resp_path = pick_best_response(response_files)

    if not best_resp_path:
        return None  # No response found — skip

    # Read response text
    ext = best_resp_path.suffix.lower()
    if ext == '.docx':
        resp_text = extract_docx_text(best_resp_path)
    elif ext in {'.xlsx', '.xls'}:
        resp_text = extract_xlsx_text(best_resp_path)
    else:
        return None

    resp_text = clean(resp_text)

    if len(resp_text) < MIN_RESPONSE_CHARS:
        return None  # Too short to be useful

    # Classify the response type
    links = extract_links(resp_text)
    is_quick_win = bool(links) and len(resp_text) < 500  # short answer with a public link
    is_attached_only = is_see_attached(resp_text) and len(resp_text) < 400

    if is_attached_only:
        return None  # Can't generate from this

    # Truncate long responses
    if len(resp_text) > MAX_RESPONSE_CHARS:
        resp_text = resp_text[:MAX_RESPONSE_CHARS] + '…'

    # Extract keywords from subject for matching
    subject_lower = subject.lower()
    keywords = [w for w in re.findall(r'\b[a-z]{4,}\b', subject_lower)
                if w not in {'with', 'from', 'that', 'this', 'have', 'been', 'will',
                             'were', 'they', 'their', 'about', 'would', 'could', 'also',
                             'data', 'info', 'information', 'request', 'please', 'your'}]

    return {
        'foi_num': foi_num,
        'subject': subject,
        'keywords': list(set(keywords)),
        'response': resp_text,
        'response_file': best_resp_path.name,
        'links': links,
        'quick_win': is_quick_win,
        'indexed_at': datetime.now().isoformat(),
    }


def build_index(root_path):
    root = Path(root_path)
    if not root.exists():
        print(f"ERROR: Path not found: {root}")
        sys.exit(1)

    print(f"Indexing: {root}")
    print("Scanning folders...\n")

    entries = []
    skipped_no_response = 0
    skipped_too_short   = 0
    skipped_attached    = 0
    errors = 0
    quick_wins = 0

    # Walk looking for FOI folders (folders whose name contains a FOI number)
    all_dirs = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip junk folders
        dirnames[:] = [d for d in dirnames
                       if d.lower() not in SKIP_FOLDERS
                       and not d.startswith('~')]
        folder_name = Path(dirpath).name
        foi_num = get_foi_num(folder_name)
        if foi_num:
            all_dirs.append((dirpath, foi_num))

    print(f"Found {len(all_dirs):,} FOI folders to process...\n")

    for i, (dirpath, foi_num) in enumerate(all_dirs):
        if i % 200 == 0 and i > 0:
            print(f"  {i:,} / {len(all_dirs):,} processed ({len(entries):,} indexed so far)...")

        try:
            result = process_folder(dirpath, foi_num)
            if result:
                entries.append(result)
                if result['quick_win']:
                    quick_wins += 1
            else:
                skipped_no_response += 1
        except Exception as e:
            errors += 1

    print(f"\nDone.")
    print(f"  Indexed       : {len(entries):,}")
    print(f"  Quick wins    : {quick_wins:,} (public link responses)")
    print(f"  Skipped       : {skipped_no_response:,} (no response doc found)")
    print(f"  Errors        : {errors:,}")

    index = {
        'built_at': datetime.now().isoformat(),
        'root': str(root),
        'total': len(entries),
        'quick_wins': quick_wins,
        'entries': entries,
    }

    out_path = Path('foi_index.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"\n✓ Index saved to: {out_path.resolve()}")
    print(f"  File size: {size_mb:.1f} MB")
    print(f"\nNext step: upload foi_index.json to Claude to embed in the FOI Tool.")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python foi_indexer.py \"C:\\path\\to\\FOI responses\"")
        sys.exit(1)
    build_index(sys.argv[1])
