import io
import struct
import unittest

from persona_porter import FormatError, Region, chunks, compare, inventory, iso_files, select, scan_models


def dual(value, width=4):
    return value.to_bytes(width, 'little') + value.to_bytes(width, 'big')


def record(name, sector, size, flags=0):
    length = 33 + len(name) + (len(name) % 2 == 0)
    data = bytearray(length)
    data[0] = length
    data[2:10] = dual(sector)
    data[10:18] = dual(size)
    data[25] = flags
    data[28:32] = dual(1, 2)
    data[32] = len(name)
    data[33:33 + len(name)] = name
    return data


def image(payload=b'hello', name=b'MODEL.RMD;1', prefix=0):
    sectors = 21 + (len(payload) + 2047) // 2048
    data = bytearray(sectors * 2048)
    pvd = bytearray(2048)
    pvd[:7] = b'\x01CD001\x01'
    pvd[80:88] = dual(sectors)
    pvd[128:132] = dual(2048, 2)
    root = record(b'\0', 20, 2048, 2)
    pvd[156:156 + len(root)] = root
    data[16 * 2048:17 * 2048] = pvd
    directory = root + record(b'\1', 20, 2048, 2) + record(name, 21, len(payload))
    data[20 * 2048:20 * 2048 + len(directory)] = directory
    data[21 * 2048:21 * 2048 + len(payload)] = payload
    return bytes(prefix) + data


class Tests(unittest.TestCase):
    def test_plain_and_wrapped_images(self):
        for prefix in (0, 0x1800, 0x4000):
            r = Region(io.BytesIO(image(prefix=prefix)))
            self.assertEqual(iso_files(r)['volume_base'], prefix)
            file = select(r, 'model.rmd')
            self.assertEqual(file.read(0, file.size), b'hello')

    def test_nested_archive(self):
        r = Region(io.BytesIO(image(image(prefix=0x1800), b'DATA.CVM;1')))
        self.assertIn('archive', inventory(r)['files'][0])
        self.assertEqual(select(r, 'DATA.CVM!MODEL.RMD').read(0, 5), b'hello')

    def test_bad_endian_field(self):
        data = bytearray(image())
        data[16 * 2048 + 84] ^= 1
        with self.assertRaisesRegex(FormatError, 'endian'):
            iso_files(Region(io.BytesIO(data)))

    def test_bad_extent(self):
        data = bytearray(image())
        offset = 20 * 2048 + 2 * len(record(b'\0', 20, 2048, 2))
        data[offset + 2:offset + 10] = dual(999)
        with self.assertRaisesRegex(FormatError, 'outside'):
            iso_files(Region(io.BytesIO(data)))

    def test_child_cannot_escape(self):
        r = Region(io.BytesIO(b'abcdef')).child(1, 2)
        with self.assertRaises(FormatError):
            r.read(0, 3)
        with self.assertRaises(FormatError):
            r.child(-1, 2)

    def test_unsafe_name(self):
        with self.assertRaisesRegex(FormatError, 'filename'):
            iso_files(Region(io.BytesIO(image(name=b'../X;1'))))

    def test_truncated_and_non_iso(self):
        for data in (b'abc', image()[:-2048]):
            with self.assertRaises(FormatError):
                iso_files(Region(io.BytesIO(data)))

    def test_chunk_comparison(self):
        a = struct.pack('<III', 16, 3, 0x1803ffff) + b'abc'
        b = struct.pack('<III', 16, 3, 0x1803ffff) + b'xyz'
        result = compare(Region(io.BytesIO(a)), Region(io.BytesIO(b)))
        self.assertTrue(result['same_top_level_types_and_versions'])
        self.assertNotEqual(result['source']['chunks'][0]['payload_sha256'],
                            result['target']['chunks'][0]['payload_sha256'])

    def test_bad_chunks(self):
        for data in (b'', b'ab', struct.pack('<III', 16, 999, 0)):
            with self.assertRaises(FormatError):
                chunks(Region(io.BytesIO(data)))

    def test_scan_keeps_unsupported_models_visible(self):
        report = scan_models(Region(io.BytesIO(image())), ['MODEL'])
        self.assertEqual(report['summary'], {'unsupported_structure': 1})
        self.assertEqual(report['models'][0]['path'], 'MODEL.RMD')
        self.assertIn('sha256', report['models'][0])

    def test_scan_parses_valid_stream(self):
        payload = struct.pack('<III', 16, 3, 0x1803ffff) + b'abc'
        report = scan_models(Region(io.BytesIO(image(payload))), ['MODEL'])
        self.assertEqual(report['summary'], {'headers_parsed': 1})


if __name__ == '__main__':
    unittest.main()
