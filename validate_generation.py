# %%
"""Validate and document a batch geometry-generation run.

This is a read-only companion to ``generate_materials_in_parallel.py``. It does
NOT generate, move or delete anything: it inspects an existing ``--data-dir`` and
produces a documented report (Markdown + JSON) covering

* integrity of the successful geometry folders (are all expected files present?),
* a categorised breakdown of the failed attempts in ``error_geometries/``,
* retry statistics derived from ``generation_summary_*.json`` (when available).

Usage:
    python validate_generation.py --data-dir data/batch_dataset
    python validate_generation.py --help
"""

import argparse
import collections
import datetime
import glob
import json
import os
import re

from helper_funcs import wallpaper_groups
from helper_funcs import new_path

# Default directory to validate. Fully overridable with --data-dir.
data_dir = os.path.join('data', 'batch_dataset')

# Folder name pattern: <group>_<shape>_<YYYY-MM-DD>_<HH-MM-SS>.<fraction>
FOLDER_RE = re.compile(
    r'^(?P<group>[a-zA-Z0-9]+)_(?P<shape>[a-zA-Z0-9]+)_'
    r'(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{2}-\d{2}-\d{2})\.(?P<frac>\d+)$'
)

ERROR_DIR_NAME = 'error_geometries'

# %%
# Suffixes of the files a *complete* generation writes into a geometry folder.
# See data_myMeshes.py: .geo (2454), .msh (2523), _fd.pkl (2567), .pkl (2798),
# .mat (3032) and info.txt (3049, the LAST file written -> marks full success).
EXPECTED_ARTIFACTS = {
    '.geo': lambda name: f'{name}.geo',
    '.msh': lambda name: f'{name}.msh',
    '_fd.pkl': lambda name: f'{name}_fd.pkl',
    '.pkl': lambda name: f'{name}.pkl',
    '.mat': lambda name: f'{name}.mat',
    'info.txt': lambda name: 'info.txt',
}

# %%
# Documentation for each known (normalised) error signature. The keys are matched
# against the number-stripped error message; anything not listed is reported as
# "uncategorised" so new failure modes surface instead of being hidden.
ERROR_DOCS = {
    "Exception('Volume fraction is N, which is too high')":
        'Porosity too low: too many holes ended up filled. Normal, expected '
        'rejection that simply triggers a retry. By far the most common case.',
    "AssertionError('all holes filled!')":
        'Every hole got filled, leaving a degenerate (solid) geometry. Rejected and retried.',
    "Exception('Holes too close together')":
        'Generated holes violate the minimum-spacing constraint. Rejected and retried.',
    "RuntimeError('CGAL ERROR: precondition violation!')":
        'The scikit-geometry straight-skeleton (CGAL) step failed on this random '
        'point set. Rejected and retried.',
    "ValueError('Some edges cross each other')":
        'Resulting mesh topology is invalid (self-intersecting edges). Rejected and retried.',
    "ValueError('Some edges are used more than twice')":
        'Invalid mesh topology: an edge is shared by more than two faces. Rejected and retried.',
    "ValueError('Identicality is not transitive!')":
        'Point-uniqueness tolerance in uniquetol() produced inconsistent matches '
        'for this point set. Rejected and retried.',
    "AssertionError('boundaries are the wrong size!')":
        'A refined boundary fell outside the expected size range. Rejected and retried.',
    "ValueError('zero-size array to reduction operation maximum which has no identity')":
        'An empty array reached a reduction (degenerate intermediate result). Rejected and retried.',
    "IndexError('list index out of range')":
        'Indexing edge case while building the geometry. Rejected and retried.',
    'TimeoutExpired':
        'gmsh exceeded its time limit while meshing this geometry. Rejected and retried.',
}


def normalise_error(message):
    """Collapse an error message into a stable category signature.

    Numbers are replaced by ``N`` and only the first line is kept, so e.g. every
    "Volume fraction is 0.83..., which is too high" maps to a single category.
    """
    first_line = message.strip().splitlines()[0] if message.strip() else ''
    # TimeoutExpired carries a long argv list; key it by the exception name only.
    if first_line.startswith('TimeoutExpired'):
        return 'TimeoutExpired'
    # repr() of the CGAL RuntimeError embeds escaped newlines plus an absolute
    # path and line number; collapse it to a single stable signature.
    if first_line.startswith("RuntimeError('CGAL ERROR"):
        return "RuntimeError('CGAL ERROR: precondition violation!')"
    return re.sub(r'\d+\.?\d*', 'N', first_line).strip()


# %%
def parse_folder(name):
    """Return (group, shape) parsed from a folder name, or (None, None)."""
    match = FOLDER_RE.match(name)
    if match is None:
        return None, None
    return match.group('group'), match.group('shape')


def collect_geometry_folders(directory):
    """List immediate sub-directories of ``directory`` excluding error_geometries."""
    folders = []
    for entry in sorted(os.listdir(directory)):
        if entry == ERROR_DIR_NAME:
            continue
        full = os.path.join(directory, entry)
        if os.path.isdir(full):
            folders.append(entry)
    return folders


def check_integrity(directory, folders):
    """Validate that each successful folder contains every expected artifact.

    Returns (complete, incomplete, unrecognised) where ``incomplete`` is a list of
    dicts {folder, missing, has_error} and ``unrecognised`` lists folders whose name
    did not match the expected pattern.
    """
    complete = []
    incomplete = []
    unrecognised = []

    for folder in folders:
        group, shape = parse_folder(folder)
        if group is None:
            unrecognised.append(folder)
            continue

        full = os.path.join(directory, folder)
        present = set(os.listdir(full))
        # Artifacts are written through new_path -> the on-disk name gets a _NN
        # suffix before the extension (e.g. info_00.txt, name_00.mat).
        missing = []
        for label, builder in EXPECTED_ARTIFACTS.items():
            base = builder(folder)
            stem, ext = os.path.splitext(base)
            if not _artifact_present(present, stem, ext):
                missing.append(label)

        has_error = _artifact_present(present, 'error', '.txt')

        if missing or has_error:
            incomplete.append({
                'folder': folder,
                'group': group,
                'shape': shape,
                'missing': missing,
                'has_error': has_error,
            })
        else:
            complete.append(folder)

    return complete, incomplete, unrecognised


def _artifact_present(present, stem, ext):
    """True if ``present`` contains ``stem+ext`` or a numbered ``stem_NN+ext`` variant."""
    if f'{stem}{ext}' in present:
        return True
    pattern = re.compile(r'^' + re.escape(stem) + r'_\d+' + re.escape(ext) + r'$')
    return any(pattern.match(name) for name in present)


# %%
def analyse_errors(directory):
    """Read and categorise every error_00.txt under error_geometries/.

    Returns a dict with totals, counts per category, per group and per shape, plus
    a list of uncategorised raw messages (deduplicated).
    """
    error_root = os.path.join(directory, ERROR_DIR_NAME)
    result = {
        'total_failed_attempts': 0,
        'by_category': collections.Counter(),
        'by_group': collections.Counter(),
        'by_shape': collections.Counter(),
        'category_by_group': collections.defaultdict(collections.Counter),
        'uncategorised_examples': [],
        'unreadable': [],
    }

    if not os.path.isdir(error_root):
        return result

    uncategorised_seen = set()
    for entry in sorted(os.listdir(error_root)):
        full = os.path.join(error_root, entry)
        if not os.path.isdir(full):
            continue

        error_file = _find_error_file(full)
        if error_file is None:
            result['unreadable'].append(entry)
            continue

        try:
            with open(error_file, 'r', encoding='utf-8', errors='replace') as f:
                message = f.read()
        except OSError:
            result['unreadable'].append(entry)
            continue

        category = normalise_error(message)
        group, shape = parse_folder(entry)

        result['total_failed_attempts'] += 1
        result['by_category'][category] += 1
        if group is not None:
            result['by_group'][group] += 1
            result['category_by_group'][category][group] += 1
        if shape is not None:
            result['by_shape'][shape] += 1

        if category not in ERROR_DOCS and category not in uncategorised_seen:
            uncategorised_seen.add(category)
            result['uncategorised_examples'].append(message.strip().splitlines()[0] if message.strip() else '(empty)')

    return result


def _find_error_file(folder):
    """Return the path to error.txt / error_NN.txt inside ``folder``, or None."""
    pattern = re.compile(r'^error(_\d+)?\.txt$')
    for name in os.listdir(folder):
        if pattern.match(name):
            return os.path.join(folder, name)
    return None


# %%
def load_summary(directory):
    """Load the most recent generation_summary_*.json, or None if absent."""
    candidates = glob.glob(os.path.join(directory, 'generation_summary*.json'))
    if not candidates:
        return None
    latest = max(candidates, key=os.path.getmtime)
    try:
        with open(latest, 'r', encoding='utf-8') as f:
            summary = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    summary['_path'] = latest
    return summary


def summarise_retries(summary, error_stats):
    """Build retry statistics from the summary, falling back to error counts."""
    stats = {'source': None}

    if summary and isinstance(summary.get('results'), list):
        results = summary['results']
        attempts = [r.get('attempt', 1) for r in results if isinstance(r, dict)]
        stats['source'] = os.path.basename(summary.get('_path', 'generation_summary.json'))
        stats['total'] = summary.get('total', len(results))
        stats['successes'] = summary.get('successes')
        stats['failures'] = summary.get('failures')
        if attempts:
            stats['mean_attempts'] = round(sum(attempts) / len(attempts), 2)
            stats['max_attempts'] = max(attempts)
        # Retries per group = sum of (attempt - 1).
        per_group = collections.Counter()
        for r in results:
            if isinstance(r, dict) and r.get('group'):
                per_group[r['group']] += max(0, r.get('attempt', 1) - 1)
        stats['retries_by_group'] = dict(per_group)
        # Top geometries by number of attempts.
        ranked = sorted(
            (r for r in results if isinstance(r, dict)),
            key=lambda r: r.get('attempt', 1), reverse=True,
        )
        stats['top_attempts'] = [
            {'group': r.get('group'), 'shape': r.get('shape'), 'attempt': r.get('attempt')}
            for r in ranked[:20]
        ]
    else:
        # No summary: derive what we can from the failed-attempt folders.
        stats['source'] = 'derived from error_geometries/ (no summary file found)'
        stats['retries_by_group'] = dict(error_stats['by_group'])

    return stats


# %%
def build_report(directory, complete, incomplete, unrecognised, error_stats, retry_stats):
    """Assemble the full machine-readable report dict."""
    return {
        'created_at': datetime.datetime.now().isoformat(),
        'data_dir': directory,
        'integrity': {
            'successful_folders': len(complete) + len(incomplete),
            'complete': len(complete),
            'incomplete': len(incomplete),
            'unrecognised_names': unrecognised,
            'incomplete_details': incomplete,
        },
        'errors': {
            'total_failed_attempts': error_stats['total_failed_attempts'],
            'by_category': dict(error_stats['by_category']),
            'by_group': dict(error_stats['by_group']),
            'by_shape': dict(error_stats['by_shape']),
            'uncategorised_examples': error_stats['uncategorised_examples'],
            'unreadable_error_folders': error_stats['unreadable'],
        },
        'retries': retry_stats,
    }


def _md_table(headers, rows):
    """Render a Markdown table from headers and a list of row tuples."""
    lines = ['| ' + ' | '.join(headers) + ' |',
             '| ' + ' | '.join(['---'] * len(headers)) + ' |']
    for row in rows:
        lines.append('| ' + ' | '.join(str(c) for c in row) + ' |')
    return '\n'.join(lines)


def render_markdown(report, error_stats, top):
    """Render the documented Markdown report from the report dict."""
    integrity = report['integrity']
    errors = report['errors']
    retries = report['retries']

    out = []
    out.append('# Generation validation report')
    out.append('')
    out.append(f"- **Data directory:** `{report['data_dir']}`")
    out.append(f"- **Generated at:** {report['created_at']}")
    out.append('')

    # --- Integrity ---
    out.append('## 1. Integrity of successful geometries')
    out.append('')
    out.append(f"- Successful folders inspected: **{integrity['successful_folders']}**")
    out.append(f"- Complete (all expected files present): **{integrity['complete']}**")
    out.append(f"- Incomplete: **{integrity['incomplete']}**")
    if integrity['unrecognised_names']:
        out.append(f"- Folders with unrecognised names: **{len(integrity['unrecognised_names'])}** "
                   f"(e.g. {', '.join(integrity['unrecognised_names'][:5])})")
    out.append('')
    out.append('Expected artifacts per geometry: ' +
               ', '.join(f'`{label}`' for label in EXPECTED_ARTIFACTS))
    out.append('')
    if integrity['incomplete_details']:
        rows = [(d['folder'], d['group'], d['shape'],
                 ', '.join(d['missing']) or '-', 'yes' if d['has_error'] else 'no')
                for d in integrity['incomplete_details'][:top]]
        out.append(_md_table(['Folder', 'Group', 'Shape', 'Missing', 'Has error.txt'], rows))
        if len(integrity['incomplete_details']) > top:
            out.append('')
            out.append(f"_… and {len(integrity['incomplete_details']) - top} more "
                       f"(see the JSON report for the full list)._")
    else:
        out.append('All successful folders are complete. :white_check_mark:')
    out.append('')

    # --- Errors ---
    out.append('## 2. Failed attempts by category')
    out.append('')
    out.append(f"- Total failed attempts (retried): **{errors['total_failed_attempts']}**")
    out.append('')
    if errors['by_category']:
        rows = []
        for category, count in error_stats['by_category'].most_common():
            doc = ERROR_DOCS.get(category, '_Uncategorised — new failure mode, review manually._')
            rows.append((f'`{category}`', count, doc))
        out.append(_md_table(['Error category', 'Count', 'What it means'], rows))
    else:
        out.append('No failed attempts found.')
    out.append('')
    if errors['uncategorised_examples']:
        out.append('### Uncategorised messages')
        out.append('')
        for example in errors['uncategorised_examples']:
            out.append(f'- `{example}`')
        out.append('')

    # --- Errors by group ---
    out.append('## 3. Failed attempts by group')
    out.append('')
    if errors['by_group']:
        rows = [(group, count) for group, count in error_stats['by_group'].most_common()]
        out.append(_md_table(['Group', 'Failed attempts'], rows))
    else:
        out.append('No per-group failures recorded.')
    out.append('')

    # --- Retries ---
    out.append('## 4. Retry statistics')
    out.append('')
    out.append(f"- Source: {retries.get('source')}")
    if 'total' in retries:
        out.append(f"- Final geometries: **{retries.get('total')}** "
                   f"(successes: {retries.get('successes')}, failures: {retries.get('failures')})")
    if 'mean_attempts' in retries:
        out.append(f"- Attempts per geometry: mean **{retries['mean_attempts']}**, "
                   f"max **{retries['max_attempts']}**")
    out.append('')
    if retries.get('retries_by_group'):
        rows = sorted(retries['retries_by_group'].items(), key=lambda kv: kv[1], reverse=True)
        out.append(_md_table(['Group', 'Retries (attempt-1)'], rows))
        out.append('')
    if retries.get('top_attempts'):
        out.append('### Geometries that needed the most attempts')
        out.append('')
        rows = [(t['group'], t['shape'], t['attempt']) for t in retries['top_attempts'][:top]]
        out.append(_md_table(['Group', 'Shape', 'Attempts'], rows))
        out.append('')

    return '\n'.join(out)


# %%
def parse_args():
    parser = argparse.ArgumentParser(
        description='Validate and document a batch geometry-generation run (read-only).')
    parser.add_argument(
        '--data-dir',
        default=data_dir,
        help='Directory to validate. Choose any generation output directory.',
    )
    parser.add_argument(
        '-o',
        '--output-dir',
        default=None,
        help='Where to write the reports. Defaults to --data-dir.',
    )
    parser.add_argument(
        '--no-files',
        action='store_true',
        help='Only print the summary to the console, do not write report files.',
    )
    parser.add_argument(
        '--top',
        type=int,
        default=10,
        help='Number of rows to show in the "top" tables (incomplete / most retried).',
    )
    return parser.parse_args()


def validate_args(args):
    if not os.path.isdir(args.data_dir):
        raise ValueError(f'--data-dir {args.data_dir!r} does not exist or is not a directory')
    if args.top < 1:
        raise ValueError('--top must be at least 1')
    has_folders = any(
        os.path.isdir(os.path.join(args.data_dir, entry))
        for entry in os.listdir(args.data_dir)
    )
    has_summary = bool(glob.glob(os.path.join(args.data_dir, 'generation_summary*.json')))
    if not has_folders and not has_summary:
        raise ValueError(
            f'--data-dir {args.data_dir!r} contains no geometry folders or summary file; '
            'nothing to validate')


def main():
    args = parse_args()
    validate_args(args)

    directory = args.data_dir
    output_dir = args.output_dir or directory

    folders = collect_geometry_folders(directory)
    complete, incomplete, unrecognised = check_integrity(directory, folders)
    error_stats = analyse_errors(directory)
    summary = load_summary(directory)
    retry_stats = summarise_retries(summary, error_stats)

    report = build_report(directory, complete, incomplete, unrecognised, error_stats, retry_stats)

    # --- console summary ---
    print('Validation summary:')
    print(f"  Data dir: {directory}")
    print(f"  Successful folders: {report['integrity']['successful_folders']} "
          f"(complete: {report['integrity']['complete']}, incomplete: {report['integrity']['incomplete']})")
    print(f"  Failed attempts: {error_stats['total_failed_attempts']}")
    top_categories = error_stats['by_category'].most_common(3)
    if top_categories:
        print('  Top error categories:')
        for category, count in top_categories:
            print(f'    {count:>6}  {category}')
    if report['integrity']['incomplete']:
        print('  WARNING: some successful folders are incomplete (see report).')

    if args.no_files:
        return

    os.makedirs(output_dir, exist_ok=True)
    json_path = new_path(os.path.join(output_dir, 'validation_report.json'))
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)

    md_path = new_path(os.path.join(output_dir, 'validation_report.md'))
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(render_markdown(report, error_stats, args.top))

    print(f'  JSON report: {json_path}')
    print(f'  Markdown report: {md_path}')


if __name__ == '__main__':
    main()
