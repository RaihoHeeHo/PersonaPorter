import hashlib
import io
import struct
import unittest

from persona_porter import Region, FormatError, chunks
from pathlib import Path
import tempfile
from test_persona_porter import image


def chunk(kind, payload=b'', version=0x40000000, declared=None):
    return struct.pack('<III',kind,len(payload) if declared is None else declared,version)+payload


def rmd(extra=b'\x04\x08\x0c\x10', blob=b'B'*16, count=None, declared=None):
    payload=struct.pack('<HHII',20,20,len(blob),len(extra) if count is None else count)+bytes(8)+blob+extra
    return (chunk(0xf0f000f0,b'\x07\0')+
            chunk(0xf0f000e0,payload,declared=len(blob)+20 if declared is None else declared)+
            chunk(0xf0f00005,bytes(64))+
            chunk(0xf0f00004))


class RmdTests(unittest.TestCase):
    def test_e0_additional_bytes_are_counted_and_hashed(self):
        raw=rmd();result=chunks(Region(io.BytesIO(raw)))
        e0=result['chunks'][1]
        self.assertEqual(e0['size'],36)
        self.assertEqual(e0['payload_size'],40)
        self.assertEqual(e0['atlus_extra_size'],4)
        self.assertEqual(result['chunks'][2]['offset'],14+12+40)
        self.assertEqual(e0['payload_sha256'],hashlib.sha256(raw[26:66]).hexdigest())
        self.assertEqual(result['chunks'][-1]['type'],'0xf0f00004')

    def test_zero_and_varied_counts(self):
        for count in (0,1,2,4,7,10,13,15):
            with self.subTest(count=count):
                result=chunks(Region(io.BytesIO(rmd(bytes(range(count))))))
                self.assertEqual(result['chunks'][1]['atlus_extra_size'],count)
                self.assertEqual(len(result['chunks']),4)

    def test_oversized_internal_count_is_rejected(self):
        with self.assertRaisesRegex(FormatError,'declares'):
            chunks(Region(io.BytesIO(rmd(count=0xffffffff))))

    def test_inconsistent_declared_length_is_rejected(self):
        with self.assertRaisesRegex(FormatError,'lengths disagree'):
            chunks(Region(io.BytesIO(rmd(declared=35))))

    def test_unsupported_e0_version_is_rejected(self):
        raw=bytearray(rmd())
        struct.pack_into('<I',raw,14+8,0x1234)
        with self.assertRaisesRegex(FormatError,'Unsupported Atlus E0'):
            chunks(Region(io.BytesIO(raw)))

    def test_incomplete_e0_header_is_rejected(self):
        raw=chunk(0xf0f000f0,b'\x07\0')+chunk(0xf0f000e0,b'',declared=10)
        with self.assertRaisesRegex(FormatError,'Unsupported Atlus E0'):
            chunks(Region(io.BytesIO(raw)))

    def test_wrong_internal_count_does_not_resynchronize(self):
        with self.assertRaises(FormatError):
            chunks(Region(io.BytesIO(rmd(count=3))))

    def test_e0_rule_requires_atlus_container(self):
        raw=rmd()[14:]
        with self.assertRaises(FormatError):
            chunks(Region(io.BytesIO(raw)))

    def test_unrecognized_tail_still_refused(self):
        with self.assertRaises(FormatError):
            chunks(Region(io.BytesIO(rmd()+b'broken')))


class RmdBuildTests(unittest.TestCase):
    def test_e0_swap_preserves_source_bytes(self):
        from swap_copy import build_swap
        from persona_porter import select
        with tempfile.TemporaryDirectory() as folder:
            target,source,output=[Path(folder)/name for name in ('target.iso','source.iso','new.iso')]
            target.write_bytes(image(image(rmd(),prefix=0x1800),b'BTL.CVM;1'))
            donor=rmd(extra=b'\x01\x02')
            source.write_bytes(image(image(donor,prefix=0x1800),b'DATA.CVM;1'))
            report=build_swap(target,source,[('DATA.CVM!MODEL.RMD','BTL.CVM!MODEL.RMD')],output)
            self.assertTrue(report['verification']['all_bytes_outside_planned_ranges_unchanged'])
            with output.open('rb') as f:
                replacement=select(Region(f),'BTL.CVM!MODEL.RMD')
                self.assertEqual(replacement.read(0,replacement.size),donor)


if __name__=='__main__':unittest.main()
