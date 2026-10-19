#!/usr/bin/env python3
"""Read-only PS2 porting research tools. Python 3.10+, standard library only."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import sys

SECTOR = 2048


class FormatError(ValueError):
    pass


class Region:
    """Bounded view into a file; nested archives never escape their parent."""
    def __init__(self, stream, start=0, size=None):
        self.stream = stream
        self.start = start
        if size is None:
            stream.seek(0, 2)
            size = stream.tell() - start
        self.size = size

    def read(self, offset, size):
        if offset < 0 or size < 0 or offset + size > self.size:
            raise FormatError(f"Read outside container: offset={offset:#x}, size={size:#x}")
        self.stream.seek(self.start + offset)
        data = self.stream.read(size)
        if len(data) != size:
            raise FormatError("Truncated input")
        return data

    def child(self, offset, size):
        if offset < 0 or size < 0 or offset + size > self.size:
            raise FormatError("File extent outside container")
        return Region(self.stream, self.start + offset, size)


def both(data, offset, width):
    lo = int.from_bytes(data[offset:offset + width], 'little')
    hi = int.from_bytes(data[offset + width:offset + 2 * width], 'big')
    if lo != hi:
        raise FormatError("ISO both-endian fields disagree")
    return lo


def directory_record(data):
    if len(data) < 34 or data[0] != len(data) or 33 + data[32] > len(data):
        raise FormatError("Malformed ISO directory record")
    if data[1] or data[26] or data[27] or data[25] & 0x80:
        raise FormatError("Extended attributes, interleaved or multi-extent files unsupported")
    if both(data, 28, 2) != 1:
        raise FormatError("Multi-volume ISO unsupported")
    name = data[33:33 + data[32]]
    return both(data, 2, 4), both(data, 10, 4), data[25], name


def iso_files(region):
    """Find an ISO volume at a sector-aligned base in the first 1 MiB.

    CVM wrappers are detected from an actual primary descriptor, not assumed
    to have a fixed header. Encrypted directories are not supported.
    """
    candidates = []
    for base in range(0, min(1024 * 1024, region.size), SECTOR):
        pos = base + 16 * SECTOR
        if pos + SECTOR > region.size:
            break
        if region.read(pos, 7) == b'\x01CD001\x01':
            candidates.append(base)
            # Later descriptors can belong to files inside this volume.
            # Select the earliest descriptor and validate it strictly below.
            break
    if not candidates:
        raise FormatError("No ISO9660 primary descriptor; unsupported, encrypted or damaged image")
    base = candidates[0]
    pvd = region.read(base + 16 * SECTOR, SECTOR)
    if both(pvd, 128, 2) != SECTOR:
        raise FormatError("Only 2048-byte logical sectors supported")
    volume_size = both(pvd, 80, 4) * SECTOR
    volume = region.child(base, volume_size)
    root_len = pvd[156]
    extent, size, flags, _ = directory_record(pvd[156:156 + root_len])
    if not flags & 2:
        raise FormatError("ISO root is not a directory")
    pending = [('', extent, size, 0)]
    visited = set()
    files = []
    names = set()
    while pending:
        prefix, extent, size, depth = pending.pop()
        if depth > 64 or len(visited) > 100000:
            raise FormatError("Directory traversal limit exceeded")
        if (extent, size) in visited:
            raise FormatError("Repeated or cyclic ISO directory")
        visited.add((extent, size))
        directory = volume.child(extent * SECTOR, size)
        pos = 0
        while pos < size:
            record_start = pos
            length = directory.read(pos, 1)[0]
            if length == 0:
                pos = (pos // SECTOR + 1) * SECTOR
                continue
            if pos % SECTOR + length > SECTOR:
                raise FormatError("Directory record crosses sector boundary")
            child_extent, child_size, child_flags, raw_name = directory_record(directory.read(pos, length))
            pos += length
            if raw_name in (b'\0', b'\1'):
                continue
            try:
                name = raw_name.decode('ascii').split(';', 1)[0]
            except UnicodeDecodeError as exc:
                raise FormatError("Non-ASCII ISO filename unsupported") from exc
            if name in ('', '.', '..') or any(c in name for c in '/\\!'):
                raise FormatError("Unsafe or unsupported ISO filename")
            path = f'{prefix}/{name}'.lstrip('/')
            if path.casefold() in names:
                raise FormatError("Ambiguous ISO filenames")
            names.add(path.casefold())
            child = volume.child(child_extent * SECTOR, child_size)
            if child_flags & 2:
                pending.append((path, child_extent, child_size, depth + 1))
            else:
                files.append({'path': path, 'size': child_size,
                              'offset': child.start - region.start,
                              'record_offset': directory.start + record_start - region.start})
    return {'volume_base': base, 'volume_size': volume_size,
            'volume_id': pvd[40:72].decode('ascii', errors='replace').strip(),
            'files': sorted(files, key=lambda f: f['path'])}


def select(region, selector):
    for part in selector.split('!'):
        entries = iso_files(region)['files']
        matches = [e for e in entries if e['path'].casefold() == part.strip('/').casefold()]
        if len(matches) != 1:
            raise FormatError(f"No unique file matching {part!r}")
        entry = matches[0]
        region = region.child(entry['offset'], entry['size'])
    return region


def inventory(region, nested=True):
    report = iso_files(region)
    if nested:
        for entry in report['files']:
            if entry['path'].lower().endswith('.cvm'):
                try:
                    entry['archive'] = inventory(region.child(entry['offset'], entry['size']), False)
                except FormatError as exc:
                    entry['archive_error'] = str(exc)
    return report


def chunks(region):
    """Top-level headers, including the bounded Atlus E0 extension length.

    The E0 byte array is excluded from the outer declared payload length.
    Its own count at payload +8 supplies the additional serialized bytes.
    No resynchronization or inferred animation slots are used.
    """
    atlus = region.size >= 14 and region.read(0, 12) == struct.pack('<III', 0xf0f000f0, 2, 0x40000000)
    result = []
    pos = 0
    while pos < region.size:
        if len(result) >= 100000:
            raise FormatError("Chunk count limit exceeded")
        if region.size - pos < 12:
            raise FormatError(f"Incomplete chunk header at {pos:#x}")
        kind, size, version = struct.unpack('<III', region.read(pos, 12))
        extra = 0
        if atlus and kind == 0xf0f000e0:
            if version != 0x40000000 or size < 20 or pos + 24 > region.size:
                raise FormatError(f'Unsupported Atlus E0 header at {pos:#x}')
            blob_size, extra = struct.unpack('<II', region.read(pos + 16, 8))
            if blob_size + 20 != size:
                raise FormatError(f'Atlus E0 lengths disagree at {pos:#x}')
        payload_size = size + extra
        if pos + 12 + payload_size > region.size:
            raise FormatError(f'Chunk at {pos:#x}, type={kind:#010x}, declares {size} payload bytes; '
                              f'only {region.size - pos - 12} remain. '
                              'Possible format-specific length rule; input was not altered.')
        payload = region.child(pos + 12, payload_size)
        digest = hashlib.sha256()
        for offset in range(0, payload_size, 1024 * 1024):
            digest.update(payload.read(offset, min(1024 * 1024, payload_size - offset)))
        result.append({'offset': pos, 'type': f'0x{kind:08x}', 'size': size,
                       'version_raw': f'0x{version:08x}', 'payload_sha256': digest.hexdigest()})
        if atlus and kind == 0xf0f000e0:
            result[-1].update({'payload_size': payload_size, 'atlus_extra_size': extra})
        pos += 12 + payload_size
    if not result:
        raise FormatError("Empty chunk stream")
    return {'size': region.size, 'chunks': result,
            'note': 'Structural headers only; does not establish RMD validity or game compatibility.'}


def scan_models(region, prefixes):
    """Batch inspection with explicit failures; unsupported layouts stay untouched."""
    index = iso_files(region)
    results = []
    for entry in index['files']:
        path = entry['path']
        if not path.lower().endswith('.rmd') or not any(
                path.casefold().startswith(p.casefold()) for p in prefixes):
            continue
        model = region.child(entry['offset'], entry['size'])
        digest = hashlib.sha256()
        for offset in range(0, model.size, 1024 * 1024):
            digest.update(model.read(offset, min(1024 * 1024, model.size - offset)))
        result = {'path': path, 'size': model.size, 'sha256': digest.hexdigest()}
        try:
            result['structure'] = chunks(model)
            result['status'] = 'headers_parsed'
        except FormatError as exc:
            result['status'] = 'unsupported_structure'
            result['error'] = str(exc)
        results.append(result)
    return {'volume_base': index['volume_base'], 'prefixes': prefixes,
            'summary': dict(Counter(r['status'] for r in results)), 'models': results,
            'note': 'Header parsing is not game compatibility validation. Errors do not establish corrupt files.'}


def compare(left, right):
    a, b = chunks(left), chunks(right)
    signature = lambda r: [(c['type'], c['version_raw']) for c in r['chunks']]
    return {'source': a, 'target': b,
            'same_top_level_types_and_versions': signature(a) == signature(b),
            'size_difference': a['size'] - b['size'],
            'source_type_counts': dict(Counter(c['type'] for c in a['chunks'])),
            'target_type_counts': dict(Counter(c['type'] for c in b['chunks'])),
            'verdict': 'Inspection only. Skeletons, animations and in-game compatibility remain unverified.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    for command in ('inventory', 'chunks'):
        sub = subs.add_parser(command)
        sub.add_argument('input', type=Path)
    extract = subs.add_parser('extract')
    extract.add_argument('input', type=Path)
    extract.add_argument('selector', help='e.g. DATA.CVM!MODEL/P0001.RMD')
    extract.add_argument('output', type=Path)
    diff = subs.add_parser('compare')
    diff.add_argument('source', type=Path)
    diff.add_argument('target', type=Path)
    scan = subs.add_parser('scan-models')
    scan.add_argument('input', type=Path)
    scan.add_argument('--archive', help='Archive selector inside the input ISO, e.g. BTL.CVM')
    scan.add_argument('--prefix', action='append', help='Model path prefix; repeat for multiple paths')
    args = parser.parse_args()
    try:
        if args.command == 'compare':
            with args.source.open('rb') as a, args.target.open('rb') as b:
                report = compare(Region(a), Region(b))
        else:
            with args.input.open('rb') as stream:
                region = Region(stream)
                if args.command == 'extract':
                    chosen = select(region, args.selector)
                    # Exclusive creation also prevents overwriting the original input.
                    with args.output.open('xb') as output:
                        for offset in range(0, chosen.size, 1024 * 1024):
                            output.write(chosen.read(offset, min(1024 * 1024, chosen.size - offset)))
                    report = {'output': str(args.output.resolve()), 'size': chosen.size}
                elif args.command == 'scan-models':
                    if args.archive:
                        region = select(region, args.archive)
                    report = scan_models(region, args.prefix or [
                        'MODEL/PERSONA/', 'MODEL_BTL/PERSONA/', 'MODEL/FACILITYP/'])
                else:
                    report = inventory(region) if args.command == 'inventory' else chunks(region)
        print(json.dumps(report, indent=2))
        return 0
    except (OSError, FormatError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
