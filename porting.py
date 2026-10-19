"""Bidirectional, bounded P3 FES / P4 model and UI porting backend."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from persona_porter import FormatError, Region, inventory
from mokoi_test import NAME_PATH, MYTH_PATH, tbl_sections, myth_dialog
from pak import entries, replace_member
from swap_copy import build_swap, plan_swap
from growth import build_growth, plan_growth
from gameplay import GameplayEdit, tables_for
from tmx_preview import tmx_info, png_bytes

GAMES = {
    'SLUS_216.21': {'title': 'Persona 3 FES', 'key': 'p3fes',
                    'battle': 'BTL.CVM!MODEL/PERSONA/PS{:03X}.RMD',
                    'facility': 'BTL.CVM!MODEL/FACILITYP/FP{:03X}.RMD',
                    'controls': b'\xf2\x08\xff\xff\xf1?'},
    'SLUS_217.82': {'title': 'Persona 4', 'key': 'p4',
                    'battle': 'BTL.CVM!MODEL_BTL/PERSONA/PS{:03X}.RMD',
                    'facility': 'DATA.CVM!MODEL/FACILITYP/FP{:03X}.RMD',
                    'controls': b'\xf2\x05\xff\xff\xf1A'},
}


@dataclass(frozen=True)
class Options:
    name: bool = True
    portrait: bool = True
    card: bool = True
    lore: bool = True
    custom_lore: str | None = None
    expand_models: bool = True


class GameImage:
    def __init__(self, path):
        self.path = Path(path).resolve()
        with self.path.open('rb') as f:
            root = Region(f)
            report = inventory(root)
            self.files = {}
            for outer in report['files']:
                self.files[outer['path'].casefold()] = dict(outer)
                for inner in outer.get('archive', {}).get('files', []):
                    selector = outer['path'] + '!' + inner['path']
                    self.files[selector.casefold()] = {
                        **inner, 'offset': outer['offset'] + inner['offset'],
                        'record_offset': outer['offset'] + inner['record_offset']}
            cnf = self._read(root, 'SYSTEM.CNF')
            serials = [s for s in GAMES if s.encode() in cnf]
            if len(serials) != 1:
                raise FormatError('Supported editions: P3 FES SLUS_216.21 and P4 SLUS_217.82 (PS2 NTSC-U ISOs).')
            self.serial = serials[0]
            self.game = GAMES[self.serial]
            self.title = self.game['title']
            self.names_data = self._read(root, NAME_PATH)
            sections = tbl_sections(self.names_data)
            if len(sections) != 5 or sections[3][1] != 256 * 17:
                raise FormatError('Unsupported Persona name table')
            self.name_offset = sections[3][0]
            self.myth = self._read(root, MYTH_PATH)
            # Locate packed copies by their directory and member names. Never extract
            # archive member names such as ../help/datMyth.bmd to the filesystem.
            self.myth_bundles = []
            self.other_myth_bundles = []
            for key in sorted(self.files):
                if not key.startswith('data.cvm!facility/') or not key.endswith(('.pak', '.pac')):
                    continue
                raw = self._read(root, key)
                try:
                    members = entries(raw)
                except FormatError:
                    # A differently formatted bundle must not silently hide a lore copy.
                    if b'datmyth.bmd' in raw.lower() or self.myth in raw:
                        raise FormatError(f'Unrecognized lore bundle layout: {key}')
                    continue
                for member in members:
                    if member['name'].replace('\\', '/').split('/')[-1].casefold() == 'datmyth.bmd':
                        payload = raw[member['offset']:member['offset'] + member['size']]
                        if payload != self.myth:
                            self.other_myth_bundles.append(key)
                            continue
                        self.myth_bundles.append((key, member['name']))
        self.catalog = []
        for i in range(1, 256):
            name = self.name_bytes(i).decode('ascii', errors='replace')
            if not name or name.casefold() in (f'{i:03x}', '000'):
                continue
            battle, facility = self.model_paths(i)
            if battle.casefold() not in self.files or facility.casefold() not in self.files:
                continue
            self.catalog.append({'id': i, 'name': name,
                                 'battle_size': self.file_info(battle)['size'],
                                 'facility_size': self.file_info(facility)['size']})

    def _read(self, root, selector):
        item = self.file_info(selector)
        return root.read(item['offset'], item['size'])

    def file_info(self, selector):
        try:
            return self.files[selector.casefold()]
        except KeyError as exc:
            raise FormatError(f'Missing file: {selector}') from exc

    def read(self, selector):
        with self.path.open('rb') as f:
            return self._read(Region(f), selector)

    def name_bytes(self, index):
        if not 0 <= index < 256:
            raise FormatError('Persona ID must be between 000 and 0FF')
        field = self.names_data[self.name_offset + index * 17:self.name_offset + (index + 1) * 17]
        if b'\0' not in field:
            raise FormatError('Unterminated Persona name')
        return field.split(b'\0', 1)[0]

    def name(self, index):
        return self.name_bytes(index).decode('ascii', errors='replace')

    def model_paths(self, index):
        return self.game['battle'].format(index), self.game['facility'].format(index)

    def lore(self, index):
        dialog = myth_dialog(self.myth, index)
        if dialog['text'][:6] != self.game['controls']:
            raise FormatError('Unsupported lore control prefix')
        body = dialog['text'][6:]
        if not body.endswith(b'\0') or b'\0' in body[:-1] or any(c != 10 and not 32 <= c <= 126 for c in body[:-1]):
            raise FormatError('This lore uses unsupported non-ASCII text or additional controls')
        return body[:-1].decode('ascii'), dialog['text_size'] - 6

    def preview(self, index):
        return png_bytes(self.read(f'DATA.CVM!CAMP/PERSONA/PST_{index:03X}.TMX'))


def transfer_lore(target, source, target_id, source_id, custom=None, base=None):
    source_text, _ = source.lore(source_id)
    target.lore(target_id)  # Validate target controls/layout as well.
    dialog = myth_dialog(target.myth, target_id)
    lore = source_text if custom is None else custom
    try:
        body = lore.encode('ascii') + b'\0'
    except UnicodeEncodeError as exc:
        raise FormatError('Edited lore must use ASCII characters') from exc
    if any(c != 10 and not 32 <= c <= 126 for c in body[:-1]):
        raise FormatError('Edited lore contains unsupported control characters')
    text = dialog['text'][:6] + body
    if len(text) > dialog['text_size']:
        raise FormatError(f'Lore needs {len(body)} bytes; target has {dialog["text_size"] - 6}. '
                          'Choose a different target, shorten the lore, or turn off lore transfer.')
    result = bytearray(target.myth if base is None else base)
    import struct
    struct.pack_into('<I', result, dialog['start'] + 32, len(text))
    result[dialog['text_start']:dialog['text_start'] + dialog['text_size']] = text.ljust(dialog['text_size'], b'\0')
    result = bytes(result)
    if myth_dialog(result, target_id)['text'] != text:
        raise FormatError('Lore readback failed')
    return result, lore


@dataclass(frozen=True)
class QueueEntry:
    source_id: int
    target_id: int
    options: Options = Options()
    gameplay: tuple = ()


def validate_queue(queue):
    queue = tuple(queue)
    if not queue:
        raise FormatError('Add at least one swap to the queue.')
    if len(queue) > 255:
        raise FormatError('The queue cannot contain more than 255 target slots.')
    targets = set()
    for entry in queue:
        if not isinstance(entry, QueueEntry) or not isinstance(entry.options, Options):
            raise FormatError('Invalid queue entry')
        if not isinstance(entry.gameplay, tuple) or any(not isinstance(edit, GameplayEdit) for edit in entry.gameplay):
            raise FormatError('Invalid queued gameplay edits')
        if entry.target_id in targets:
            raise FormatError(f'Target slot {entry.target_id:03X} is already in the queue. Remove its existing swap first.')
        targets.add(entry.target_id)
    return queue


def prepare_batch(source, target, queue):
    queue = validate_queue(queue)
    if source.serial == target.serial:
        raise FormatError('Choose one P3 FES ISO and one P4 ISO')
    source_ids = {p['id'] for p in source.catalog}
    target_ids = {p['id'] for p in target.catalog}
    payloads, replacements, descriptions = {}, [], []
    names = bytearray(target.names_data)
    lore_data = target.myth
    for number, entry in enumerate(queue, 1):
        source_id, target_id, options = entry.source_id, entry.target_id, entry.options
        try:
            if source_id not in source_ids or target_id not in target_ids:
                raise FormatError('Choose available Personas from both lists')
            pairs = list(zip(source.model_paths(source_id), target.model_paths(target_id)))
            if not options.expand_models:
                for donor, recipient in pairs:
                    if source.file_info(donor)['size'] > target.file_info(recipient)['size']:
                        raise FormatError('Model is larger than its target. Enable Allow larger models for this entry.')
            replacements.extend(pairs)
            if options.name:
                name = source.name_bytes(source_id)
                if len(name) > 16:
                    raise FormatError('Donor name exceeds the target name field')
                offset = target.name_offset + target_id * 17
                names[offset:offset + 17] = name.ljust(17, b'\0')
            for enabled, folder, prefix in (
                    (options.portrait, 'CAMP/PERSONA', 'PST_'),
                    (options.card, 'CARD/PERSONA', 'I_PRC')):
                if not enabled:
                    continue
                path = f'DATA.CVM!{folder}/{prefix}{target_id:03X}.TMX'
                donor = source.read(f'DATA.CVM!{folder}/{prefix}{source_id:03X}.TMX')
                original = target.read(path)
                if tmx_info(donor) != tmx_info(original) or donor[:64] != original[:64]:
                    raise FormatError(f'Incompatible image headers for {folder}')
                payloads[path] = donor
            lore_text = None
            if options.lore:
                lore_data, lore_text = transfer_lore(target, source, target_id, source_id,
                                                      options.custom_lore, base=lore_data)
            gameplay = tables_for(target).apply(target_id, entry.gameplay, payloads) if entry.gameplay else []
            description = annotate({}, source, target, source_id, target_id, options, lore_text)
            description['gameplay'] = gameplay
            descriptions.append(description)
        except FormatError as exc:
            raise FormatError(f'Queue item {number} ({source_id:03X} → {target_id:03X}): {exc}') from exc
    if any(entry.options.name for entry in queue):
        payloads[NAME_PATH] = bytes(names)
    if any(entry.options.lore for entry in queue):
        payloads[MYTH_PATH] = lore_data
        # Build each packed copy once, using all queued lore edits together.
        for path, name in target.myth_bundles:
            original = payloads.get(path, target.read(path))
            payloads[path], _ = replace_member(original, name, target.myth, lore_data)
    return replacements, payloads, descriptions


def prepare_port(source, target, source_id, target_id, options=Options()):
    replacements, payloads, descriptions = prepare_batch(source, target, [QueueEntry(source_id, target_id, options)])
    return replacements, payloads, descriptions[0]['lore_text']


def prepared_plan(source, target, replacements, payloads):
    plan = plan_swap(target.path, source.path, replacements, payloads, allow_identical=True, allow_growth=True)
    if any(op['source_size'] > op['original_size'] for op in plan['operations']):
        plan = plan_growth(target.path, source.path, replacements, payloads)
    return plan


def annotate_batch(plan, descriptions):
    common = dict(descriptions[0])
    if len(descriptions) != 1:
        for key in ('source_persona', 'target_slot', 'transfer', 'lore_text', 'allow_larger_models', 'gameplay'):
            common.pop(key)
    plan.update(common)
    plan['swaps'] = descriptions
    plan['swap_count'] = len(descriptions)
    return plan


def batch_plan(source, target, queue):
    replacements, payloads, descriptions = prepare_batch(source, target, queue)
    return annotate_batch(prepared_plan(source, target, replacements, payloads), descriptions)


def port_plan(source, target, source_id, target_id, options=Options()):
    return batch_plan(source, target, [QueueEntry(source_id, target_id, options)])


def annotate(plan, source, target, source_id, target_id, options, lore):
    plan.update({'source_game': source.title, 'target_game': target.title,
                 'source_persona': {'id': source_id, 'name': source.name(source_id)},
                 'target_slot': {'id': target_id, 'original_name': target.name(target_id)},
                 'transfer': {'name': options.name, 'portrait': options.portrait, 'card': options.card,
                              'lore': options.lore, 'edited_lore': options.lore and options.custom_lore is not None},
                 'lore_text': lore,
                 'allow_larger_models': options.expand_models,
                 'unchanged_alternate_lore_bundles': target.other_myth_bundles,
                 'scope': 'Models, selected UI and explicit gameplay edits. Arcana, inheritance, fusion rules, voices and story scripts remain unchanged; gameplay changes are listed per swap.',
                 'known_test': 'P4 Mokoi (04E) over P3 FES Orpheus (001) including UI and lore confirmed by the user. Other pairs and reverse direction require in-game testing.'})
    return plan


def build_batch(source, target, queue, output, progress=None, cancel=None):
    # Re-read both inputs and revalidate the entire queue before every build.
    queue = validate_queue(queue)
    source, target = GameImage(source.path), GameImage(target.path)
    replacements, payloads, descriptions = prepare_batch(source, target, queue)
    checked = prepared_plan(source, target, replacements, payloads)
    output = Path(output)
    manifest = output.with_suffix('.manifest.json')
    created = False
    completed = False
    try:
        with manifest.open('x', encoding='utf-8') as report:
            created = True
            if checked.get('expanded_archives'):
                plan = build_growth(target.path, source.path, replacements, output, payloads,
                                    progress=progress, cancel=cancel)
            else:
                plan = build_swap(target.path, source.path, replacements, output, payloads,
                                  allow_identical=True, progress=progress, cancel=cancel)
            completed = True
            annotate_batch(plan, descriptions)
            json.dump(plan, report, indent=2)
            report.write('\n')
        return plan
    except BaseException:
        if created:
            manifest.unlink(missing_ok=True)
        if completed:
            output.unlink(missing_ok=True)
        raise


def build_port(source, target, source_id, target_id, output, options=Options(), progress=None, cancel=None):
    return build_batch(source, target, [QueueEntry(source_id, target_id, options)], output, progress, cancel)
