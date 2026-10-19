#!/usr/bin/env python3
"""Reproduce the P4 Mokoi -> P3 FES Orpheus model and UI identity test."""
import argparse
import json
from pathlib import Path
import struct
import sys

from persona_porter import FormatError, Region, select
from swap_copy import build_swap, plan_swap
from tmx_preview import tmx_info
from pak import replace_member

MODEL_REPLACEMENTS = [
    ('BTL.CVM!MODEL_BTL/PERSONA/PS04E.RMD', 'BTL.CVM!MODEL/PERSONA/PS001.RMD'),
    ('DATA.CVM!MODEL/FACILITYP/FP04E.RMD', 'BTL.CVM!MODEL/FACILITYP/FP001.RMD'),
]
NAME_PATH = 'BTL.CVM!BATTLE/MSG.TBL'
MYTH_PATH = 'DATA.CVM!HELP/DATMYTH.BMD'
MYTH_BUNDLES = (
    'DATA.CVM!FACILITY/AGS_FCL_ELEVATORGIRL.PAK',
    'DATA.CVM!FACILITY/FCL_ELEVATORGIRL.PAK',
)


def read_file(region, selector):
    file = select(region, selector)
    return file.read(0, file.size)


def tbl_sections(data):
    sections = []
    pos = 0
    while pos < len(data):
        if pos + 4 > len(data):
            raise FormatError('Truncated table section header')
        size = struct.unpack_from('<I', data, pos)[0]
        if pos + 4 + size > len(data):
            raise FormatError('Table section exceeds file')
        sections.append((pos + 4, size))
        pos = (pos + 4 + size + 15) // 16 * 16
    return sections


def persona_name(data, index):
    sections = tbl_sections(data)
    if len(sections) != 5 or sections[3][1] != 256 * 17:
        raise FormatError('Unexpected MSG.TBL Persona-name section layout')
    if not 0 <= index < 256:
        raise FormatError('Persona name index out of range')
    offset = sections[3][0] + index * 17
    field = data[offset:offset + 17]
    if b'\0' not in field:
        raise FormatError('Name field is not NUL terminated')
    return offset, field.split(b'\0', 1)[0]


def replace_persona_name(target, donor, target_id=1, donor_id=0x4e):
    offset, old = persona_name(target, target_id)
    _, new = persona_name(donor, donor_id)
    if old != b'Orpheus' or new != b'Mokoi':
        raise FormatError(f'Unexpected names: {old!r} -> {new!r}; wrong input or slot')
    result = bytearray(target)
    result[offset:offset + 17] = new.ljust(17, b'\0')
    return bytes(result), {'offset_in_file': offset, 'old_name': old.decode(), 'new_name': new.decode()}


def myth_dialog(data, index):
    if len(data) < 32 or data[8:12] != b'MSG1' or data[28] != 0:
        raise FormatError('Expected an unrelocated little-endian MSG1 file')
    file_size, relocation, relocation_size, count = (
        struct.unpack_from('<I', data, o)[0] for o in (4, 16, 20, 24))
    if file_size != len(data) or not 0 <= index < count or 32 + count * 8 > len(data):
        raise FormatError('Invalid MSG1 size, index or dialog table')
    if relocation + relocation_size != len(data):
        raise FormatError('Unexpected MSG1 relocation-table location')
    kind, relative = struct.unpack_from('<II', data, 32 + index * 8)
    start = 32 + relative
    if kind != 0 or not 32 + count * 8 <= start <= relocation - 36:
        raise FormatError('Expected a message dialog within the data area')
    label = data[start:start + 24].split(b'\0', 1)[0]
    if label != f'myth_{index:03d}'.encode():
        raise FormatError('Myth dialog label/index mismatch')
    pages, speaker, page_pointer, text_size = struct.unpack_from('<HHII', data, start + 24)
    text_start = 32 + page_pointer
    if pages != 1 or speaker != 0xffff or text_start != start + 36:
        raise FormatError('Only observed single-page, unnamed-speaker myth dialogs are supported')
    if text_size < 7 or text_start + text_size > relocation:
        raise FormatError('Invalid myth text buffer')
    # Text must fit before every subsequent dialog, not just the relocation table.
    next_offsets = [32 + struct.unpack_from('<I', data, 36 + i * 8)[0]
                    for i in range(count)]
    boundary = min([o for o in next_offsets if o > start] + [relocation])
    if text_start + text_size > boundary:
        raise FormatError('Myth text overlaps another dialog')
    text = data[text_start:text_start + text_size]
    return {'start': start, 'text_start': text_start, 'text_size': text_size,
            'text': text, 'relocation': relocation, 'label': label.decode()}


def replace_myth(target, donor, target_id=1, donor_id=0x4e):
    a, b = myth_dialog(target, target_id), myth_dialog(donor, donor_id)
    # Preserve P3's game-specific leading functions. Only the ASCII lore travels.
    if a['text'][:6] != b'\xf2\x08\xff\xff\xf1?' or b['text'][:6] != b'\xf2\x05\xff\xff\xf1A':
        raise FormatError('Unexpected P3/P4 myth text control prefixes')
    lore = b['text'][6:]
    if not lore.endswith(b'\0') or b'\0' in lore[:-1] or any(c not in (0, 10) and not 32 <= c <= 126 for c in lore):
        raise FormatError('Only the observed ASCII single-page lore is supported')
    text = a['text'][:6] + lore
    if len(text) > a['text_size']:
        raise FormatError('Donor lore exceeds existing target text buffer')
    result = bytearray(target)
    struct.pack_into('<I', result, a['start'] + 32, len(text))
    result[a['text_start']:a['text_start'] + a['text_size']] = text.ljust(a['text_size'], b'\0')
    result = bytes(result)
    # No offset fields, dialog headers, relocation bytes or other dialogs move.
    reparsed = myth_dialog(result, target_id)
    if reparsed['text'] != text or result[a['relocation']:] != target[a['relocation']:]:
        raise FormatError('Myth replacement verification failed')
    return result, {'target_dialog': a['label'], 'source_dialog': b['label'],
                    'original_text_bytes': a['text_size'], 'new_text_bytes': len(text),
                    'p3_controls_preserved': True, 'all_pointer_values_and_relocation_bytes_preserved': True,
                    'lore': lore[:-1].decode('ascii')}


def prepare_ui(target_path, source_path):
    payloads = {}
    details = {}
    with open(target_path, 'rb') as a, open(source_path, 'rb') as b:
        target, source = Region(a), Region(b)
        if b'SLUS_216.21' not in read_file(target, 'SYSTEM.CNF') or b'SLUS_217.82' not in read_file(source, 'SYSTEM.CNF'):
            raise FormatError('This recipe requires P3 FES SLUS_216.21 and P4 SLUS_217.82')
        payloads[NAME_PATH], details['name'] = replace_persona_name(
            read_file(target, NAME_PATH), read_file(source, NAME_PATH))
        original_myth = read_file(target, MYTH_PATH)
        payloads[MYTH_PATH], details['description'] = replace_myth(
            original_myth, read_file(source, MYTH_PATH))
        details['description']['packed_copies'] = []
        for bundle in MYTH_BUNDLES:
            original_bundle = read_file(target, bundle)
            payloads[bundle], member = replace_member(
                original_bundle, '../help/datMyth.bmd', original_myth, payloads[MYTH_PATH])
            details['description']['packed_copies'].append({
                'selector': bundle, 'member': member,
                'original_matches_standalone': True,
                'replacement_matches_updated_standalone': True,
                'other_bundle_bytes_unchanged': True})
        for folder, base, label, size in (
                ('CAMP/PERSONA', 'PST_', 'portrait', 512),
                ('CARD/PERSONA', 'I_PRC', 'card', 256)):
            target_selector = f'DATA.CVM!{folder}/{base}001.TMX'
            source_selector = f'DATA.CVM!{folder}/{base}04E.TMX'
            old, new = read_file(target, target_selector), read_file(source, source_selector)
            info = tmx_info(new)
            if tmx_info(old) != info or info['width'] != size or info['height'] != size or old[:64] != new[:64]:
                raise FormatError(f'Unexpected {label} TMX header or dimensions')
            payloads[target_selector] = new
            details[label] = {'source_selector': source_selector, 'target_selector': target_selector,
                              'transfer': 'Byte-for-byte original P4 TMX, including palette and alpha', **info}
    return payloads, details


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('p3_iso', type=Path, help='Clean original P3 FES ISO')
    parser.add_argument('p4_iso', type=Path, help='Clean original P4 ISO')
    parser.add_argument('--output', type=Path, help='Build a new ISO; omit for plan only')
    args = parser.parse_args()
    try:
        payloads, details = prepare_ui(args.p3_iso, args.p4_iso)
        if args.output:
            plan = build_swap(args.p3_iso, args.p4_iso, MODEL_REPLACEMENTS, args.output, payloads)
        else:
            plan = plan_swap(args.p3_iso, args.p4_iso, MODEL_REPLACEMENTS, payloads)
        plan['recipe'] = 'P4 Mokoi over P3 FES Orpheus: model and UI identity test'
        plan['ui_details'] = details
        plan['prior_model_test'] = 'User confirmed battle model, skills and animations work in build 0.3.'
        plan['prior_ui_test'] = 'User confirmed menu, Compendium and battle in build 0.4; lore still showed Orpheus. This build also updates both packed lore copies.'
        plan['scope'] = 'Name, status portrait, card art, lore, battle/facility models. Arcana, stats, skills and affinities remain the P3 target slot.'
        print(json.dumps(plan, indent=2))
        return 0
    except (OSError, FormatError, struct.error) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
