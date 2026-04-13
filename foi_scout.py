"""
foi_scout.py
============
Run this on your personal laptop against the FOI directory.
It maps the folder structure and file patterns WITHOUT reading
any file contents — just names, types, and counts.

Usage:
    python foi_scout.py "C:/path/to/foi/folder"

    Or if you run it from inside the folder:
    python foi_scout.py .

Output:
    foi_scout_report.txt  — save this and send to Claude
"""

import os
import sys
import re
from collections import defaultdict, Counter
from pathlib import Path

# ── Config ──────────────────────────────────────────────────
MAX_SAMPLE_FILES  = 8    # sample filenames shown per folder
MAX_FOLDER_DEPTH  = 6    # how deep to recurse
# ────────────────────────────────────────────────────────────

def scan(root: Path, depth=0, report=None):
    if report is None:
        report = []

    if depth > MAX_FOLDER_DEPTH:
        return report

    try:
        entries = sorted(os.scandir(root), key=lambda e: (e.is_file(), e.name.lower()))
    except PermissionError:
        report.append(f"{'  '*depth}[PERMISSION DENIED] {root.name}")
        return report

    files = [e for e in entries if e.is_file()]
    dirs  = [e for e in entries if e.is_dir()]

    # Count file extensions in this folder
    ext_counts = Counter(Path(f.name).suffix.lower() for f in files)
    ext_summary = '  '.join(f"{ext or '(no ext)'}×{n}" for ext, n in ext_counts.most_common())

    indent = '  ' * depth
    folder_label = root.name if depth > 0 else str(root)
    report.append(f"{indent}📁 {folder_label}/  [{len(files)} files{': ' + ext_summary if ext_summary else ''}]")

    # Sample filenames — pick ones with FOI numbers if possible, else first N
    foi_pattern = re.compile(r'(?:FOI|foi)[\s_\-]?\d{4,6}', re.IGNORECASE)
    response_pattern = re.compile(r'response', re.IGNORECASE)

    foi_files      = [f.name for f in files if foi_pattern.search(f.name)]
    response_files = [f.name for f in files if response_pattern.search(f.name)]
    other_files    = [f.name for f in files if not foi_pattern.search(f.name) and not response_pattern.search(f.name)]

    samples = []
    samples += foi_files[:4]
    samples += [f for f in response_files if f not in samples][:3]
    samples += [f for f in other_files    if f not in samples][:2]
    samples = samples[:MAX_SAMPLE_FILES]

    for fname in samples:
        tags = []
        if foi_pattern.search(fname):     tags.append('FOI#')
        if response_pattern.search(fname): tags.append('RESPONSE')
        tag_str = f"  [{', '.join(tags)}]" if tags else ''
        report.append(f"{indent}    • {fname}{tag_str}")

    if len(files) > MAX_SAMPLE_FILES:
        report.append(f"{indent}    … and {len(files) - MAX_SAMPLE_FILES} more files")

    # Recurse into subdirectories
    for d in dirs:
        scan(Path(d.path), depth + 1, report)

    return report


def analyse(root: Path):
    """Walk the full tree and collect aggregate stats."""
    stats = {
        'total_files': 0,
        'total_folders': 0,
        'extensions': Counter(),
        'foi_numbered': 0,
        'response_named': 0,
        'has_both': 0,      # filename has FOI number AND "response"
        'pdf_count': 0,
        'xlsx_count': 0,
        'deepest_path': ('', 0),
    }

    foi_pat  = re.compile(r'(?:FOI|foi)[\s_\-]?\d{4,6}', re.IGNORECASE)
    resp_pat = re.compile(r'response', re.IGNORECASE)

    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        stats['total_folders'] += 1

        for fname in filenames:
            stats['total_files'] += 1
            ext = Path(fname).suffix.lower()
            stats['extensions'][ext] += 1

            has_foi  = bool(foi_pat.search(fname))
            has_resp = bool(resp_pat.search(fname))

            if has_foi:  stats['foi_numbered']  += 1
            if has_resp: stats['response_named'] += 1
            if has_foi and has_resp: stats['has_both'] += 1
            if ext == '.pdf':  stats['pdf_count']  += 1
            if ext == '.xlsx': stats['xlsx_count'] += 1

        if depth > stats['deepest_path'][1]:
            stats['deepest_path'] = (dirpath, depth)

    return stats


def main():
    if len(sys.argv) < 2:
        # Try current directory
        root = Path('.')
        print("No path given — scanning current directory.")
    else:
        root = Path(sys.argv[1])

    if not root.exists():
        print(f"ERROR: Path not found: {root}")
        sys.exit(1)

    root = root.resolve()
    print(f"Scanning: {root}")
    print("Please wait...\n")

    # Build structure report
    structure = scan(root)

    # Build stats
    stats = analyse(root)

    # Compose report
    lines = []
    lines.append("=" * 60)
    lines.append("FOI DIRECTORY SCOUT REPORT")
    lines.append("=" * 60)
    lines.append(f"Root path   : {root}")
    lines.append(f"Total files : {stats['total_files']:,}")
    lines.append(f"Total folders: {stats['total_folders']:,}")
    lines.append(f"Deepest level: {stats['deepest_path'][1]} levels")
    lines.append("")
    lines.append("── File types ──────────────────────────────")
    for ext, count in stats['extensions'].most_common(15):
        bar = '█' * min(40, count // max(1, stats['total_files'] // 40))
        lines.append(f"  {(ext or '(none)'):12}  {count:>6,}  {bar}")
    lines.append("")
    lines.append("── Naming patterns ─────────────────────────")
    lines.append(f"  Files with FOI number in name : {stats['foi_numbered']:,}")
    lines.append(f"  Files with 'response' in name : {stats['response_named']:,}")
    lines.append(f"  Files with BOTH               : {stats['has_both']:,}")
    lines.append(f"  PDF files                     : {stats['pdf_count']:,}")
    lines.append(f"  Excel files                   : {stats['xlsx_count']:,}")
    lines.append("")
    lines.append("── Directory structure (sampled) ───────────")
    lines += structure
    lines.append("")
    lines.append("── End of report ───────────────────────────")

    report_text = '\n'.join(lines)

    # Print to console
    print(report_text)

    # Save to file
    out_path = Path('foi_scout_report.txt')
    out_path.write_text(report_text, encoding='utf-8')
    print(f"\n✓ Report saved to: {out_path.resolve()}")
    print("Send foi_scout_report.txt to Claude.")


if __name__ == '__main__':
    main()
