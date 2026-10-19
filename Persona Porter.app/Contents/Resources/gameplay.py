"""Bounded gameplay-table edits for the supported NTSC-U PS2 editions.

Format references: tge-was-taken/010-Editor-Templates (p3f and p4g).
PS2 offsets, record sizes, names and campaign differences checked on local dumps.
No template source or game data is distributed with this module.
"""
from dataclasses import dataclass, asdict
import struct
from persona_porter import FormatError
from mokoi_test import tbl_sections

ROOT = 'BTL.CVM!BATTLE/'
AFFINITIES = {'Normal': 20, 'Weak': 2048, 'Resist': 4096,
              'Null': 256, 'Repel': 512, 'Drain': 1024}
P3_ELEMENTS = (('Slash', 0), ('Strike', 1), ('Pierce', 2), ('Fire', 3), ('Ice', 4),
               ('Electric', 5), ('Wind', 6), ('Light', 8), ('Dark', 9))
P4_ELEMENTS = (('Physical', 0), ('Fire', 1), ('Ice', 2), ('Electric', 3),
               ('Wind', 4), ('Light', 6), ('Dark', 7))


@dataclass(frozen=True)
class Skill:
    skill_id: int
    level: int = 0  # 0 = starting skill; otherwise absolute level, not a delta.


@dataclass(frozen=True)
class GameplayEdit:
    campaign: str = 'journey'
    level: int | None = None
    stats: tuple | None = None
    affinities: tuple | None = None
    skills: tuple | None = None


@dataclass(frozen=True)
class Record:
    level: int
    stats: tuple
    affinities: tuple
    skills: tuple


class GameplayTables:
    def __init__(self, game):
        self.p3 = game.serial == 'SLUS_216.21'
        if game.serial not in ('SLUS_216.21', 'SLUS_217.82'):
            raise FormatError('Gameplay editor supports only the two NTSC-U PS2 editions')
        self.elements = P3_ELEMENTS if self.p3 else P4_ELEMENTS
        self.campaigns = ('journey', 'answer') if self.p3 else ('main',)
        self.raw = {}
        self.sections = {}
        for campaign in self.campaigns:
            for kind in ('PERSONA', 'UNIT'):
                path = self.path(kind, campaign)
                data = game.read(path)
                parts = tbl_sections(data)
                if kind == 'PERSONA':
                    valid = len(parts) >= 2 and parts[0][1] == 14 * 256 and parts[1][1] == 70 * 256
                else:
                    valid = len(parts) == 3 and parts[2][1] == 32 * 256
                if not valid:
                    raise FormatError(f'Unsupported gameplay table layout: {path}')
                self.raw[path], self.sections[path] = data, parts
        parts = tbl_sections(game.names_data)
        offset, size = parts[1]
        count = 624 if self.p3 else 576
        if size != count * 19:
            raise FormatError('Unsupported skill name table')
        self.skill_names = {}
        for i in range(count):
            field = game.names_data[offset+i*19:offset+(i+1)*19]
            if b'\0' not in field:
                raise FormatError('Unterminated skill name')
            name = field.split(b'\0', 1)[0]
            if name and all(32 <= c <= 126 for c in name):
                label = name.decode('ascii')
                if not label.upper().startswith(('RESERVE', 'DUMMY', 'UNUSED')) and any(c.isalpha() for c in label):
                    self.skill_names[i] = label
        if self.skill_names.get(1) != 'Agi':
            raise FormatError('Unexpected skill names for the supported edition')

    def path(self, kind, campaign):
        if campaign not in self.campaigns:
            raise FormatError('Choose a campaign belonging to the target game')
        return ROOT + kind + ('_F' if campaign == 'answer' else '') + '.TBL'

    def offsets(self, index, campaign):
        if type(index) is not int or not 1 <= index < 192:
            raise FormatError('Gameplay editing currently supports regular Persona slots 001–0BF; party slots use separate progression tables')
        path = self.path('PERSONA', campaign)
        stats = self.sections[path][0][0] + index*14
        growth = self.sections[path][1][0] + index*70
        raw = self.raw[path]
        if raw[stats:stats+2] == b'\x08\0' or raw[stats+2] == 0 or raw[stats] & 0x10:
            raise FormatError('This slot is a reserved or party Persona; its gameplay layout is unsupported')
        unit = self.path('UNIT', campaign)
        affinity = self.sections[unit][2][0] + index*32
        return path, stats, growth, unit, affinity

    def record(self, index, campaign, payloads=None):
        path, pos, growth, unit, affinity = self.offsets(index, campaign)
        payloads = payloads or {}
        data, resist = payloads.get(path, self.raw[path]), payloads.get(unit, self.raw[unit])
        level = data[pos+3]
        skills = []
        for i in range(16):
            delta, flag, skill_id = struct.unpack_from('<BBH', data, growth+6+i*4)
            if flag == 1:
                skills.append(Skill(skill_id, 0 if delta == 0 else level+delta))
            elif flag not in (0, 4):
                raise FormatError('Unsupported skill progression flag in this slot')
        return Record(level, tuple(data[pos+4:pos+9]),
                      tuple((name, struct.unpack_from('<H', resist, affinity+field*2)[0]) for name, field in self.elements),
                      tuple(skills))

    def validate(self, index, edit):
        if not isinstance(edit, GameplayEdit):
            raise FormatError('Invalid gameplay edit')
        original = self.record(index, edit.campaign)
        if (edit.level is None) != (edit.stats is None):
            raise FormatError('Base level and base stats must be saved together')
        if edit.level is not None:
            if type(edit.level) is not int or not 1 <= edit.level <= 99:
                raise FormatError('Base level must be 1–99')
            if len(edit.stats) != 5 or any(type(n) is not int or not 1 <= n <= 99 for n in edit.stats):
                raise FormatError('STR, MAG, END, AG and LU must each be 1–99')
        if edit.affinities is not None:
            old = dict(original.affinities)
            if len(edit.affinities) != len(old) or {k for k, _ in edit.affinities} != set(old):
                raise FormatError('Affinity elements must match the target game')
            for name, value in edit.affinities:
                if type(value) is not int or (value not in AFFINITIES.values() and value != old[name]):
                    raise FormatError(f'Unsupported {name} affinity; keep the original value or choose a standard response')
        if edit.skills is not None:
            maximum = 15 if self.p3 else 16
            if len(edit.skills) > maximum:
                raise FormatError(f'This game supports at most {maximum} skill entries in this editor')
            seen = set()
            base = original.level if edit.level is None else edit.level
            starting = 0
            for skill in edit.skills:
                if not isinstance(skill, Skill) or type(skill.skill_id) is not int or skill.skill_id not in self.skill_names:
                    raise FormatError('Choose a named skill from the target game')
                if skill.skill_id in seen:
                    raise FormatError('A Persona cannot learn the same skill twice in the edited list')
                seen.add(skill.skill_id)
                if type(skill.level) is not int or (skill.level != 0 and not base < skill.level <= 99):
                    raise FormatError('Learned skills must unlock above the base level and at or below level 99')
                starting += skill.level == 0
            if starting > 8:
                raise FormatError('A Persona can have at most eight starting skills')
        return original

    def apply(self, index, edits, payloads):
        seen = set()
        descriptions = []
        for edit in edits:
            if edit.campaign in seen:
                raise FormatError('Only one gameplay edit per campaign is allowed for a slot')
            seen.add(edit.campaign)
            original = self.validate(index, edit)
            path, pos, growth, unit, affinity = self.offsets(index, edit.campaign)
            data = bytearray(payloads.get(path, self.raw[path]))
            resist = bytearray(payloads.get(unit, self.raw[unit]))
            if edit.level is not None:
                data[pos+3:pos+9] = bytes((edit.level, *edit.stats))
            if edit.skills is not None:
                base = original.level if edit.level is None else edit.level
                # Keep the stat-growth weights and unknown byte preceding the list.
                slots = bytearray(64)
                ordered = sorted(edit.skills, key=lambda s: s.level)
                for i, skill in enumerate(ordered):
                    struct.pack_into('<BBH', slots, i*4, 0 if skill.level == 0 else skill.level-base, 1, skill.skill_id)
                if self.p3:
                    last_delta = max((s.level-base for s in ordered if s.level), default=0)
                    struct.pack_into('<BBH', slots, len(ordered)*4, last_delta, 4, 0)
                data[growth+6:growth+70] = slots
            if edit.level is not None or edit.skills is not None:
                payloads[path] = bytes(data)
            if edit.affinities is not None:
                indices = dict(self.elements)
                for name, value in edit.affinities:
                    struct.pack_into('<H', resist, affinity+indices[name]*2, value)
                payloads[unit] = bytes(resist)
            after = self.record(index, edit.campaign, payloads)
            if edit.level is not None and (after.level, after.stats) != (edit.level, edit.stats):
                raise FormatError('Gameplay stats readback failed')
            if edit.affinities is not None and dict(after.affinities) != dict(edit.affinities):
                raise FormatError('Affinity readback failed')
            if edit.skills is not None and after.skills != tuple(sorted(edit.skills, key=lambda s: s.level)):
                raise FormatError('Skill progression readback failed')
            descriptions.append({'campaign': edit.campaign, 'edits': asdict(edit),
                                 'before': asdict(original), 'after': asdict(after)})
        return descriptions


def tables_for(game):
    if not hasattr(game, '_gameplay_tables'):
        game._gameplay_tables = GameplayTables(game)
    return game._gameplay_tables
