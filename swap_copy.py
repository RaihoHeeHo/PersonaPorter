#!/usr/bin/env python3
"""Prepare an experimental unchanged RMD swap in a new ISO copy.

Only replacements fitting the original file extents are supported.
No file relocation, archive rebuilding, animation conversion or table edits.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

from persona_porter import FormatError, Region, chunks, iso_files, select

BLOCK = 4 * 1024 * 1024


class BuildCancelled(FormatError):
    pass


def check_cancel(cancel):
    if cancel and cancel():
        raise BuildCancelled('Build cancelled; incomplete output removed')


def locate(region, selector):
    for part in selector.split('!'):
        entries = iso_files(region)['files']
        matches = [e for e in entries if e['path'].casefold() == part.strip('/').casefold()]
        if len(matches) != 1:
            raise FormatError(f'No unique file matching {part!r}')
        entry = matches[0]
        record = region.start + entry['record_offset']
        region = region.child(entry['offset'], entry['size'])
    return region, record


def sha(region):
    digest = hashlib.sha256()
    for offset in range(0, region.size, BLOCK):
        digest.update(region.read(offset, min(BLOCK, region.size - offset)))
    return digest.hexdigest()


def plan_swap(target, source, replacements, additional_files=None, allow_identical=False, allow_growth=False):
    plan = {'target_iso': str(Path(target).resolve()), 'source_iso': str(Path(source).resolve()),
            'operations': [], 'validation': 'Not tested in game. This is an unchanged-model swap experiment.'}
    with open(target, 'rb') as a, open(source, 'rb') as b:
        target_region, source_region = Region(a), Region(b)
        plan['target_iso_size'] = target_region.size
        for donor_path, recipient_path in replacements:
            if not donor_path.lower().endswith('.rmd') or not recipient_path.lower().endswith('.rmd'):
                raise FormatError('This experiment supports direct RMD files only')
            donor, _ = locate(source_region, donor_path)
            recipient, record = locate(target_region, recipient_path)
            if donor.size > recipient.size and not allow_growth:
                raise FormatError(f'{donor_path} ({donor.size} bytes) exceeds original extent '
                                  f'{recipient_path} ({recipient.size} bytes)')
            donor_chunks = chunks(donor)
            recipient_chunks = chunks(recipient)
            donor_hash, recipient_hash = sha(donor), sha(recipient)
            if donor_hash == recipient_hash and not allow_identical:
                raise FormatError('Selected files are already identical; choose a visible test')
            plan['operations'].append({
                'source_selector': donor_path, 'target_selector': recipient_path,
                'source_offset': donor.start, 'target_offset': recipient.start,
                'directory_size_offset': record + 10,
                'source_size': donor.size, 'original_size': recipient.size,
                'source_sha256': donor_hash, 'original_sha256': recipient_hash,
                'source_chunk_types': [c['type'] for c in donor_chunks['chunks']],
                'original_chunk_types': [c['type'] for c in recipient_chunks['chunks']],
            })
        for recipient_path, payload in (additional_files or {}).items():
            if not isinstance(payload, bytes):
                raise FormatError('Generated file payload must be bytes')
            recipient, record = locate(target_region, recipient_path)
            if len(payload) != recipient.size:
                raise FormatError('UI replacement must preserve the exact original file size')
            plan['operations'].append({
                'source_kind': 'generated_ui_file', 'target_selector': recipient_path,
                'target_offset': recipient.start, 'directory_size_offset': record + 10,
                'source_size': len(payload), 'original_size': recipient.size,
                'source_sha256': hashlib.sha256(payload).hexdigest(),
                'original_sha256': sha(recipient)})
    ranges = sorted((lo, hi) for op in plan['operations'] for lo, hi in (
        (op['target_offset'], op['target_offset'] + op['original_size']),
        (op['directory_size_offset'], op['directory_size_offset'] + 8)))
    if not ranges:
        raise FormatError('At least one replacement is required')
    if any(b[0] < a[1] for a, b in zip(ranges, ranges[1:])):
        raise FormatError('Overlapping replacement extents or directory fields')
    plan['allowed_change_ranges'] = ranges
    return plan


def verify_unchanged_outside(original, modified, ranges, progress=None, cancel=None):
    with open(original, 'rb') as a, open(modified, 'rb') as b:
        ra, rb = Region(a), Region(b)
        if ra.size != rb.size:
            raise FormatError('Output ISO size changed')
        cursor = 0
        for lo, hi in ranges + [(ra.size, ra.size)]:
            while cursor < lo:
                check_cancel(cancel)
                size = min(BLOCK, lo - cursor)
                if ra.read(cursor, size) != rb.read(cursor, size):
                    raise FormatError(f'Unexpected change outside planned extents near {cursor:#x}')
                cursor += size
                if progress:
                    progress('Verifying the rest of the ISO', 70 + 30 * cursor / ra.size)
            cursor = hi


def build_swap(target, source, replacements, output, additional_files=None,
               allow_identical=False, progress=None, cancel=None):
    # Always recompute against the live input. Do not execute a saved/untrusted plan.
    plan = plan_swap(target, source, replacements, additional_files, allow_identical)
    output = Path(output)
    created = False
    try:
        check_cancel(cancel)
        with open(target, 'rb') as a, open(source, 'rb') as b, output.open('xb') as out:
            created = True
            copied = 0
            while True:
                check_cancel(cancel)
                block = a.read(BLOCK)
                if not block:
                    break
                out.write(block)
                copied += len(block)
                if progress:
                    progress('Copying the target ISO', 55 * copied / plan['target_iso_size'])
            donor_iso = Region(b)
            for i, op in enumerate(plan['operations']):
                check_cancel(cancel)
                if progress:
                    progress('Writing selected files', 55 + 5 * i / len(plan['operations']))
                if op.get('source_kind') == 'generated_ui_file':
                    payload = additional_files[op['target_selector']]
                    if hashlib.sha256(payload).hexdigest() != op['source_sha256']:
                        raise FormatError('Generated UI payload changed during build')
                    out.seek(op['target_offset'])
                    out.write(payload)
                    continue
                donor = donor_iso.child(op['source_offset'], op['source_size'])
                if sha(donor) != op['source_sha256']:
                    raise FormatError('Source model changed while preparing build')
                out.seek(op['target_offset'])
                for offset in range(0, donor.size, BLOCK):
                    out.write(donor.read(offset, min(BLOCK, donor.size - offset)))
                remaining = op['original_size'] - op['source_size']
                while remaining:
                    size = min(BLOCK, remaining)
                    out.write(bytes(size))
                    remaining -= size
                size = op['source_size']
                out.seek(op['directory_size_offset'])
                out.write(size.to_bytes(4, 'little') + size.to_bytes(4, 'big'))
        with output.open('rb') as f:
            region = Region(f)
            for i, op in enumerate(plan['operations']):
                check_cancel(cancel)
                if progress:
                    progress('Checking inserted files', 60 + 10 * i / len(plan['operations']))
                replacement = select(region, op['target_selector'])
                if replacement.size != op['source_size'] or sha(replacement) != op['source_sha256']:
                    raise FormatError('Output model failed byte-for-byte readback')
                padding = region.child(op['target_offset'] + op['source_size'],
                                       op['original_size'] - op['source_size'])
                for offset in range(0, padding.size, BLOCK):
                    if any(padding.read(offset, min(BLOCK, padding.size - offset))):
                        raise FormatError('Output padding is not zero')
        verify_unchanged_outside(target, output, plan['allowed_change_ranges'], progress, cancel)
        plan['output_iso'] = str(output.resolve())
        plan['verification'] = {'replacement_bytes_match_source': True,
                                'remaining_extent_zeroed': True,
                                'all_bytes_outside_planned_ranges_unchanged': True,
                                'game_tested': False}
        if progress:
            progress('Verified', 100)
        return plan
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target_iso', type=Path)
    parser.add_argument('source_iso', type=Path)
    parser.add_argument('--replace', nargs=2, action='append', required=True,
                        metavar=('SOURCE_SELECTOR', 'TARGET_SELECTOR'))
    parser.add_argument('--output', type=Path, help='Build a new ISO; omit to print a plan only')
    args = parser.parse_args()
    try:
        report = (build_swap(args.target_iso, args.source_iso, args.replace, args.output)
                  if args.output else plan_swap(args.target_iso, args.source_iso, args.replace))
        print(json.dumps(report, indent=2))
        return 0
    except (OSError, FormatError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
