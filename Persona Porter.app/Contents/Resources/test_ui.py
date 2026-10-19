import struct
import unittest
import zlib

from mokoi_test import myth_dialog, replace_myth, replace_persona_name, persona_name
from persona_porter import FormatError
from tmx_preview import png_bytes, tmx_info


def names(index, text):
    sections = [bytes(8), bytes(16), bytes(24), bytes(17 * 256), bytes(32)]
    table = bytearray(sections[3])
    table[index * 17:index * 17 + len(text)] = text
    table[169 * 17:169 * 17 + 13] = b'Orpheus Telos'
    sections[3] = bytes(table)
    result = bytearray()
    for section in sections:
        result += struct.pack('<I', len(section)) + section
        result += bytes((-len(result)) % 16)
    return bytes(result)


def myth(index, text, prefix):
    count = index + 2
    result = bytearray(32 + 8 * count)
    result[:1] = b'\x07'
    result[8:12] = b'MSG1'
    struct.pack_into('<I', result, 24, count)
    for i in range(count):
        result += bytes((-len(result)) % 4)
        start = len(result)
        body = prefix + (text if i == index else b'Unchanged.\n\0')
        struct.pack_into('<II', result, 32 + i * 8, 0, start - 32)
        result += f'myth_{i:03d}'.encode().ljust(24, b'\0')
        result += struct.pack('<HHII', 1, 0xffff, start + 36 - 32, len(body)) + body
    relocation = len(result)
    result += b'\x02\x04'
    struct.pack_into('<I', result, 4, len(result))
    struct.pack_into('<II', result, 16, relocation, 2)
    return bytes(result)


class UITests(unittest.TestCase):
    def test_name_slot_only(self):
        a, b = names(1, b'Orpheus'), names(78, b'Mokoi')
        out, _ = replace_persona_name(a, b)
        self.assertEqual(persona_name(out, 1)[1], b'Mokoi')
        self.assertEqual(persona_name(out, 169)[1], b'Orpheus Telos')
        offset, _ = persona_name(a, 1)
        self.assertEqual(a[:offset], out[:offset])
        self.assertEqual(a[offset + 17:], out[offset + 17:])

    def test_name_wrong_input_rejected(self):
        with self.assertRaises(FormatError):
            replace_persona_name(names(1, b'Izanagi'), names(78, b'Mokoi'))

    def test_lore_controls_pointers_and_other_dialogs_preserved(self):
        a = myth(1, b'Original longer lore.\n\0', b'\xf2\x08\xff\xff\xf1?')
        b = myth(78, b'Mokoi lore.\n\0', b'\xf2\x05\xff\xff\xf1A')
        out, _ = replace_myth(a, b)
        d, new = myth_dialog(a, 1), myth_dialog(out, 1)
        self.assertEqual(len(out), len(a))
        self.assertEqual(new['text'], b'\xf2\x08\xff\xff\xf1?Mokoi lore.\n\0')
        allowed = set(range(d['start'] + 32, d['start'] + 36)) | set(range(d['text_start'], d['text_start'] + d['text_size']))
        self.assertTrue(all(i in allowed for i, (x, y) in enumerate(zip(a, out)) if x != y))
        self.assertEqual(myth_dialog(a, 0)['text'], myth_dialog(out, 0)['text'])
        self.assertEqual(myth_dialog(a, 2)['text'], myth_dialog(out, 2)['text'])

    def test_lore_too_long_rejected(self):
        with self.assertRaisesRegex(FormatError, 'exceeds'):
            replace_myth(myth(1, b'A\0', b'\xf2\x08\xff\xff\xf1?'),
                         myth(78, b'Much longer text.\0', b'\xf2\x05\xff\xff\xf1A'))

    def test_lore_extra_controls_rejected(self):
        with self.assertRaisesRegex(FormatError, 'ASCII'):
            replace_myth(myth(1, b'Long original text.\0', b'\xf2\x08\xff\xff\xf1?'),
                         myth(78, b'A\xf1B\0', b'\xf2\x05\xff\xff\xf1A'))

    def test_myth_bad_pointer_rejected(self):
        a = bytearray(myth(1, b'A\0', b'\xf2\x08\xff\xff\xf1?'))
        d = myth_dialog(a, 1)
        struct.pack_into('<I', a, d['start'] + 28, 0xfffffffc)
        with self.assertRaises(FormatError):
            myth_dialog(a, 1)

    def test_tmx_decoding_preserves_palette_alpha(self):
        data = bytearray(64 + 1024 + 2)
        struct.pack_into('<I', data, 4, len(data))
        data[8:12] = b'TMX0'
        struct.pack_into('<BBHHBB', data, 16, 1, 0, 2, 1, 0x13, 0)
        # Logical index 8 maps to physical palette index 16.
        data[64 + 16 * 4:68 + 16 * 4] = bytes((100, 110, 120, 128))
        data[64:68] = bytes((255, 254, 130, 0))
        data[1088:] = bytes((0, 8))
        png = png_bytes(bytes(data))
        # Locate/decompress IDAT to check exact RGBA output, including transparent RGB.
        pos = 8
        while png[pos + 4:pos + 8] != b'IDAT':
            pos += struct.unpack_from('>I', png, pos)[0] + 12
        n = struct.unpack_from('>I', png, pos)[0]
        self.assertEqual(zlib.decompress(png[pos + 8:pos + 8 + n]),
                         bytes((0, 255, 254, 130, 0, 100, 110, 120, 255)))
        self.assertEqual(tmx_info(data)['width'], 2)


if __name__ == '__main__':
    unittest.main()
