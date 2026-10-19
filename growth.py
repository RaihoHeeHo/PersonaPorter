"""Experimental CVM relocation for larger models; always builds a new ISO."""
from pathlib import Path
import hashlib
import struct

from persona_porter import FormatError, Region, iso_files, select, both
from swap_copy import BLOCK, check_cancel, locate, plan_swap, sha

SECTOR = 2048
READ_AHEAD = 1024 * 1024


def aligned(value):
    return (value + SECTOR - 1) // SECTOR * SECTOR


def dual(value):
    if not 0 <= value < 2**32:
        raise FormatError('ISO field exceeds its 32-bit limit')
    return struct.pack('<I', value) + struct.pack('>I', value)


def leadout(sectors):
    minutes, remainder = divmod(sectors + 150, 4500)
    seconds, frames = divmod(remainder, 75)
    def bcd(value):
        value &= 255
        return (value // 10 * 16 + value % 10) & 255
    return bytes(map(bcd, (minutes, seconds, frames)))


def volume(region, expected_base):
    index = iso_files(region)
    if index['volume_base'] != expected_base:
        raise FormatError('Unsupported ISO volume base for archive expansion')
    for sector in range(16, 80):
        desc = region.read(expected_base + sector * SECTOR, 7)
        if desc[1:] != b'CD001\x01':
            raise FormatError('Invalid ISO descriptor sequence')
        if desc[0] == 255:
            return index
        if desc[0] != 1 or sector != 16:
            raise FormatError('Additional ISO volumes are unsupported for expansion')
    raise FormatError('Missing ISO descriptor terminator')


def runtime_record(elf, name, old_size, old_lba):
    data = elf.read(0, elf.size)
    if data[:7] != b'\x7fELF\x01\x01\x01' or struct.unpack_from('<H', data, 18)[0] != 8:
        raise FormatError('Expected a little-endian MIPS ELF32 executable')
    phoff = struct.unpack_from('<I', data, 28)[0]
    stride, count = struct.unpack_from('<HH', data, 42)
    if stride < 32 or phoff + stride * count > len(data):
        raise FormatError('Invalid executable program headers')
    loaded = []
    for i in range(count):
        kind, offset, _, _, size = struct.unpack_from('<IIIII', data, phoff + i * stride)
        if offset + size > len(data):
            raise FormatError('Executable segment outside file')
        if kind == 1:
            loaded.append((offset, offset + size))
    needle = name.encode('ascii') + b'\0'
    candidates = []
    pos = data.find(needle)
    while pos >= 0:
        start = pos - 14
        if start >= 0 and start % 4 == 0 and any(a <= start and pos + len(needle) <= b for a, b in loaded):
            size, lba = struct.unpack_from('<QI', data, start)
            if (size, lba) == (old_size, old_lba):
                candidates.append(start)
        pos = data.find(needle, pos + 1)
    if len(candidates) != 1:
        raise FormatError(f'No unique executable file record for {name}; expansion refused')
    return elf.start + candidates[0]


def plan_growth(target, source, replacements, additional_files=None):
    plan = plan_swap(target, source, replacements, additional_files, allow_identical=True, allow_growth=True)
    oversized = [op for op in plan['operations'] if op['source_size'] > op['original_size']]
    if not oversized:
        raise FormatError('Archive expansion requires a larger model')
    names = sorted({op['target_selector'].split('!')[0] for op in oversized})
    plan['metadata_patches'] = []
    plan['archive_clones'] = []
    prefix_ranges = []
    with open(target, 'rb') as f:
        root = Region(f)
        outer = volume(root, 0)
        serials = [e['path'] for e in outer['files'] if e['path'] in ('SLUS_216.21', 'SLUS_217.82')]
        if len(serials) != 1:
            raise FormatError('Unsupported executable for expansion')
        elf = select(root, serials[0])
        def patch(offset, old, new, prefix=False):
            if len(old) != len(new):
                raise FormatError('Metadata patch must preserve field length')
            plan['metadata_patches'].append({'offset': offset, 'before': old.hex(), 'after': new.hex()})
            if prefix:
                prefix_ranges.append((offset, offset + len(new)))
        cursor = aligned(root.size)
        clones = {}
        for name in names:
            archive, record = locate(root, name)
            index = volume(archive, 0x1800)
            head = archive.read(0, 0x1800)
            if (head[:4] != b'CVMH' or head[0x800:0x804] != b'ZONE'
                    or int.from_bytes(head[0x20:0x24], 'big') != archive.size
                    or int.from_bytes(head[0x30:0x34], 'big') != 0
                    or int.from_bytes(head[0x804:0x80c], 'big') != archive.size - 0x80c
                    or int.from_bytes(head[0x830:0x838], 'big') != index['volume_size']
                    or index['volume_size'] + 0x1800 != archive.size
                    or head[0x101d:0x1020] != leadout(index['volume_size'] // SECTOR)
                    or struct.unpack_from('>II', head, 0x818) != (2048, 2048)
                    or int.from_bytes(head[0x82c:0x830], 'big') != 3):
                raise FormatError(f'Unsupported CVM wrapper: {name}')
            clone = {'name': name, 'original_offset': archive.start, 'original_size': archive.size,
                     'new_offset': cursor, 'unchanged_exceptions': []}
            end = aligned(archive.size)
            for op in plan['operations']:
                if op['target_selector'].split('!')[0] != name:
                    continue
                original_offset = op['target_offset']
                original_size_offset = op['directory_size_offset']
                op['target_offset'] = cursor + original_offset - archive.start
                op['directory_size_offset'] = cursor + original_size_offset - archive.start
                if op['source_size'] > op['original_size']:
                    op['target_offset'] = cursor + end
                    op['allocated_size'] = aligned(op['source_size'])
                    end += op['allocated_size']
                    extent_offset = original_size_offset - 8
                    new_lba = (op['target_offset'] - cursor - 0x1800) // SECTOR
                    old = root.read(extent_offset, 8)
                    patch(cursor + extent_offset - archive.start, old, dual(new_lba))
                    clone['unchanged_exceptions'].append((extent_offset - archive.start, extent_offset - archive.start + 8))
                else:
                    op['allocated_size'] = op['original_size']
                    clone['unchanged_exceptions'].append((original_offset - archive.start, original_offset - archive.start + op['original_size']))
                clone['unchanged_exceptions'].append((original_size_offset - archive.start, original_size_offset - archive.start + 8))
            clone['new_size'] = aligned(end + READ_AHEAD)
            if clone['new_size'] >= 2**32:
                raise FormatError('Expanded CVM exceeds its 32-bit size limit')
            new_volume_size = clone['new_size'] - 0x1800
            fields = [(0x20, struct.pack('>I', clone['new_size'])),
                      (0x804, struct.pack('>Q', clone['new_size'] - 0x80c)),
                      (0x830, struct.pack('>Q', new_volume_size)),
                      (0x101d, leadout(new_volume_size // SECTOR)),
                      (0x1800 + 16 * SECTOR + 80, dual(new_volume_size // SECTOR))]
            for offset, new in fields:
                patch(cursor + offset, archive.read(offset, len(new)), new)
                clone['unchanged_exceptions'].append((offset, offset + len(new)))
            patch(record + 2, root.read(record + 2, 8), dual(cursor // SECTOR), True)
            patch(record + 10, root.read(record + 10, 8), dual(clone['new_size']), True)
            clones[name] = clone
            plan['archive_clones'].append(clone)
            cursor += clone['new_size']
        for op in plan['operations']:
            name = op['target_selector'].split('!')[0]
            recipient, _ = locate(root, op['target_selector'])
            if name not in clones:
                op['allocated_size'] = op['original_size']
                prefix_ranges.extend([(op['target_offset'], op['target_offset'] + op['original_size']),
                                      (op['directory_size_offset'], op['directory_size_offset'] + 8)])
            if op.get('source_kind') != 'generated_ui_file':
                archive, _ = locate(root, name)
                old_lba = (recipient.start - archive.start - 0x1800) // SECTOR
                new_base = clones[name]['new_offset'] if name in clones else archive.start
                new_lba = (op['target_offset'] - new_base - 0x1800) // SECTOR
                record = runtime_record(elf, op['target_selector'].split('!')[-1].split('/')[-1], op['original_size'], old_lba)
                patch(record, root.read(record, 12), struct.pack('<QI', op['source_size'], new_lba), True)
        plan['output_iso_size'] = aligned(cursor + READ_AHEAD)
        patch(16 * SECTOR + 80, root.read(16 * SECTOR + 80, 8), dual(plan['output_iso_size'] // SECTOR), True)
    plan['allowed_change_ranges'] = merge(prefix_ranges)
    plan['expanded_archives'] = names
    plan['validation'] = 'Experimental relocated archives and executable file records; requires in-game testing.'
    # Reject accidental overlap among writes, including metadata.
    writes = [(op['target_offset'], op['target_offset'] + op['allocated_size']) for op in plan['operations']]
    writes += [(op['directory_size_offset'], op['directory_size_offset'] + 8) for op in plan['operations']]
    writes += [(p['offset'], p['offset'] + len(bytes.fromhex(p['after']))) for p in plan['metadata_patches']]
    writes.sort()
    if any(b[0] < a[1] for a, b in zip(writes, writes[1:])):
        raise FormatError('Overlapping archive expansion writes')
    return plan


def merge(ranges):
    result = []
    for lo, hi in sorted(ranges):
        if result and lo <= result[-1][1]:
            result[-1] = (result[-1][0], max(hi, result[-1][1]))
        else:
            result.append((lo, hi))
    return result


def compare_regions(original, modified, length, exceptions, cancel=None, progress=None):
    cursor = 0
    for lo, hi in merge(exceptions) + [(length, length)]:
        while cursor < lo:
            check_cancel(cancel)
            size = min(BLOCK, lo - cursor)
            if original.read(cursor, size) != modified.read(cursor, size):
                raise FormatError(f'Unexpected change outside planned writes at {modified.start + cursor:#x}')
            cursor += size
            if progress:
                progress('Verifying unchanged game data', 75 + 24 * cursor / length)
        cursor = hi


def build_growth(target, source, replacements, output, additional_files=None, progress=None, cancel=None):
    plan = plan_growth(target, source, replacements, additional_files)
    output = Path(output)
    created = False
    try:
        with open(target, 'rb') as a, open(source, 'rb') as b, output.open('xb+') as out:
            created = True
            root, donor_root = Region(a), Region(b)
            total = root.size + sum(c['original_size'] for c in plan['archive_clones'])
            copied = 0
            def copy(region, destination):
                nonlocal copied
                out.seek(destination)
                for offset in range(0, region.size, BLOCK):
                    check_cancel(cancel)
                    out.write(region.read(offset, min(BLOCK, region.size - offset)))
                    copied += min(BLOCK, region.size - offset)
                    if progress:
                        progress('Copying game archives', 60 * copied / total)
            copy(root, 0)
            for clone in plan['archive_clones']:
                copy(root.child(clone['original_offset'], clone['original_size']), clone['new_offset'])
            out.truncate(plan['output_iso_size'])
            for op in plan['operations']:
                check_cancel(cancel)
                original, _ = locate(root, op['target_selector'])
                if sha(original) != op['original_sha256']:
                    raise FormatError('Target file changed during build')
                if op.get('source_kind') == 'generated_ui_file':
                    payload = additional_files[op['target_selector']]
                else:
                    donor = donor_root.child(op['source_offset'], op['source_size'])
                    payload = donor.read(0, donor.size)
                if hashlib.sha256(payload).hexdigest() != op['source_sha256']:
                    raise FormatError('Source file changed during build')
                out.seek(op['target_offset'])
                out.write(payload)
                remaining = op['allocated_size'] - len(payload)
                while remaining:
                    size = min(BLOCK, remaining)
                    out.write(bytes(size))
                    remaining -= size
                out.seek(op['directory_size_offset'])
                out.write(dual(len(payload)))
            for patch in plan['metadata_patches']:
                check_cancel(cancel)
                out.seek(patch['offset'])
                before, after = bytes.fromhex(patch['before']), bytes.fromhex(patch['after'])
                if out.read(len(before)) != before:
                    raise FormatError('Archive metadata preimage changed during build')
                out.seek(patch['offset'])
                out.write(after)
            out.flush()
            result = Region(out)
            for op in plan['operations']:
                check_cancel(cancel)
                replacement = select(result, op['target_selector'])
                if replacement.start != op['target_offset'] or replacement.size != op['source_size'] or sha(replacement) != op['source_sha256']:
                    raise FormatError('Expanded model/UI readback failed')
                padding = result.read(op['target_offset'] + op['source_size'], op['allocated_size'] - op['source_size'])
                if any(padding):
                    raise FormatError('Nonzero model padding')
            for patch in plan['metadata_patches']:
                after = bytes.fromhex(patch['after'])
                if result.read(patch['offset'], len(after)) != after:
                    raise FormatError('Metadata readback failed')
            volume(result, 0)
            compare_regions(root, result, root.size, plan['allowed_change_ranges'], cancel, progress)
            for clone in plan['archive_clones']:
                archive = select(result, clone['name'])
                volume(archive, 0x1800)
                compare_regions(root.child(clone['original_offset'], clone['original_size']), archive,
                                clone['original_size'], clone['unchanged_exceptions'], cancel)
        plan['output_iso'] = str(output.resolve())
        plan['verification'] = {'replacement_bytes_match_source': True, 'metadata_readback_matches': True,
                                'all_bytes_outside_planned_ranges_unchanged': True,
                                'cloned_archive_unselected_bytes_unchanged': True, 'game_tested': False}
        if progress:
            progress('Verified; ready for in-game testing', 100)
        return plan
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise
