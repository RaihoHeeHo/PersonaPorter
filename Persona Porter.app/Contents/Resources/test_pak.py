import struct
import unittest

from pak import entries, replace_member
from persona_porter import FormatError


def make_pak(members):
    result = bytearray()
    for name, payload in members:
        result += name.encode().ljust(252, b'\0') + struct.pack('<I', len(payload)) + payload
        result += bytes((-len(result)) % 64)
    return bytes(result) + bytes(256)


class PakTests(unittest.TestCase):
    def test_only_selected_payload_changes(self):
        data = make_pak([('unrelated.spr', b'SPR content'), ('../help/datMyth.bmd', b'old lore')])
        result, entry = replace_member(data, '../HELP/DATMYTH.BMD', b'old lore', b'new lore')
        offset, size = entry['offset'], entry['size']
        self.assertEqual(len(result), len(data))
        self.assertEqual(result[:offset], data[:offset])
        self.assertEqual(result[offset + size:], data[offset + size:])
        self.assertEqual(result[offset:offset + size], b'new lore')
        self.assertEqual(entries(data), entries(result))

    def test_size_change_rejected(self):
        with self.assertRaisesRegex(FormatError, 'exact original size'):
            replace_member(make_pak([('myth', b'old')]), 'myth', b'old', b'longer')

    def test_nonzero_alignment_padding_preserved(self):
        data = bytearray(make_pak([('myth', b'old')]))
        data[259:320] = b'X' * 61
        out, _ = replace_member(bytes(data), 'myth', b'old', b'new')
        self.assertEqual(out[259:320], b'X' * 61)

    def test_unexpected_original_rejected(self):
        with self.assertRaisesRegex(FormatError, 'differs'):
            replace_member(make_pak([('myth', b'old')]), 'myth', b'wrong', b'new')

    def test_duplicate_casefold_names_rejected(self):
        with self.assertRaisesRegex(FormatError, 'Duplicate'):
            entries(make_pak([('myth', b'a'), ('MYTH', b'b')]))

    def test_payload_outside_archive_rejected(self):
        data = bytearray(make_pak([('myth', b'old')]))
        struct.pack_into('<I', data, 252, len(data) * 2)
        with self.assertRaisesRegex(FormatError, 'exceeds'):
            entries(data)

    def test_data_after_terminator_rejected(self):
        with self.assertRaisesRegex(FormatError, 'after PAK terminator'):
            entries(make_pak([('myth', b'old')]) + b'x')


if __name__ == '__main__':
    unittest.main()
