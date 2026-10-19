import io
from pathlib import Path
import struct
import tempfile
import unittest

from persona_porter import FormatError, Region, select, both
from swap_copy import BuildCancelled
from growth import build_growth, plan_growth, leadout, runtime_record, compare_regions
from test_persona_porter import image, record, dual
from test_swap_copy import model

S = 2048


def make_iso(files):
    data = bytearray(image())
    data[17*S:17*S+7] = b'\xffCD001\x01'
    data[20*S:21*S] = bytes(S)
    directory = record(b'\0', 20, S, 2) + record(b'\1', 20, S, 2)
    data = data[:21*S]
    for name, payload in files:
        directory += record(name.encode() + b';1', len(data)//S, len(payload))
        data += payload + bytes((-len(payload)) % S)
    data[20*S:20*S+len(directory)] = directory
    data[16*S+80:16*S+88] = dual(len(data)//S)
    return data


def cvm(payload, name='MODEL.RMD'):
    inner = make_iso([(name, payload), ('UI.BIN', b'untouched ui')])
    head = bytearray(0x1800)
    head[:4] = b'CVMH'
    size = len(head) + len(inner)
    struct.pack_into('>I', head, 0x20, size)
    head[0x800:0x804] = b'ZONE'
    struct.pack_into('>Q', head, 0x804, size - 0x80c)
    struct.pack_into('>II', head, 0x818, S, S)
    struct.pack_into('>I', head, 0x82c, 3)
    struct.pack_into('>Q', head, 0x830, len(inner))
    head[0x101d:0x1020] = leadout(len(inner)//S)
    return head + inner


def executable(size, lba=21):
    data = bytearray(512)
    data[:7] = b'\x7fELF\x01\x01\x01'
    struct.pack_into('<H', data, 18, 8)
    struct.pack_into('<I', data, 28, 52)
    struct.pack_into('<HH', data, 42, 32, 1)
    struct.pack_into('<IIIII', data, 52, 1, 0, 0, 0, len(data))
    struct.pack_into('<QI', data, 128, size, lba)
    data[142:152] = b'MODEL.RMD\0'
    return data


class GrowthTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.target, self.source, self.output = [self.root / n for n in ('target.iso','source.iso','new.iso')]
        self.old = model(b'old')
        self.new = model(b'larger' * 1200)
        self.target.write_bytes(make_iso([('BTL.CVM', cvm(self.old)),
                                          ('SLUS_216.21', executable(len(self.old))), ('KEEP.BIN', b'keep me')]))
        self.source.write_bytes(make_iso([('DATA.CVM', cvm(self.new))]))
        self.pairs = [('DATA.CVM!MODEL.RMD', 'BTL.CVM!MODEL.RMD')]

    def test_expansion_readback_runtime_index_and_preservation(self):
        before = self.target.read_bytes()
        plan = build_growth(self.target,self.source,self.pairs,self.output,
                            {'BTL.CVM!UI.BIN': b'replaced ui!'})
        self.assertEqual(self.target.read_bytes(),before)
        with self.output.open('rb') as f:
            root = Region(f)
            replacement = select(root,'BTL.CVM!MODEL.RMD')
            self.assertEqual(replacement.read(0,replacement.size),self.new)
            archive = select(root,'BTL.CVM')
            self.assertEqual(archive.start,len(before))
            self.assertEqual(select(root,'BTL.CVM!UI.BIN').read(0,12),b'replaced ui!')
            elf = select(root,'SLUS_216.21')
            size,lba = struct.unpack('<QI',elf.read(128,12))
            self.assertEqual(size,len(self.new))
            self.assertEqual(lba,(replacement.start-archive.start-0x1800)//S)
            self.assertEqual(both(root.read(16*S, S),80,4)*S,root.size)
        self.assertTrue(plan['verification']['cloned_archive_unselected_bytes_unchanged'])

    def test_two_archives_and_unrelocated_ui(self):
        old2, new2 = model(b'second'), model(b'Y' * 9000)
        elf = executable(len(self.old))
        struct.pack_into('<QI', elf, 176, len(old2), 21)
        elf[190:201] = b'MODEL2.RMD\0'
        self.target.write_bytes(make_iso([('BTL.CVM',cvm(self.old)),
                                         ('DATA.CVM',cvm(old2,'MODEL2.RMD')),
                                         ('SLUS_217.82',elf), ('KEEP.BIN',b'keep me')]))
        self.source.write_bytes(make_iso([('BTL.CVM',cvm(self.new)),
                                         ('DATA.CVM',cvm(new2,'MODEL2.RMD'))]))
        pairs = [('BTL.CVM!MODEL.RMD','BTL.CVM!MODEL.RMD'),
                 ('DATA.CVM!MODEL2.RMD','DATA.CVM!MODEL2.RMD')]
        plan = build_growth(self.target,self.source,pairs,self.output,{'KEEP.BIN':b'new UI!'})
        self.assertEqual(plan['expanded_archives'],['BTL.CVM','DATA.CVM'])
        with self.output.open('rb') as f:
            root = Region(f)
            self.assertEqual(select(root,'KEEP.BIN').read(0,7),b'new UI!')
            for selector, expected in [(pairs[0][1],self.new),(pairs[1][1],new2)]:
                r = select(root,selector)
                self.assertEqual(r.read(0,r.size),expected)

    def test_bad_runtime_record_refused_before_output(self):
        self.target.write_bytes(make_iso([('BTL.CVM',cvm(self.old)),('SLUS_216.21',executable(999))]))
        with self.assertRaisesRegex(FormatError,'unique executable'):
            build_growth(self.target,self.source,self.pairs,self.output)
        self.assertFalse(self.output.exists())

    def test_bad_wrapper_refused(self):
        damaged = cvm(self.old)
        damaged[0x20] ^= 1
        self.target.write_bytes(make_iso([('BTL.CVM',damaged),('SLUS_216.21',executable(len(self.old)))]))
        with self.assertRaisesRegex(FormatError,'wrapper'):
            plan_growth(self.target,self.source,self.pairs)

    def test_extra_volume_refused(self):
        data = bytearray(self.target.read_bytes())
        data[17*S] = 2
        self.target.write_bytes(data)
        with self.assertRaisesRegex(FormatError,'Additional ISO'):
            plan_growth(self.target,self.source,self.pairs)

    def test_cancellation_removes_output(self):
        cancelled = False
        def progress(*args):
            nonlocal cancelled
            cancelled = True
        with self.assertRaises(BuildCancelled):
            build_growth(self.target,self.source,self.pairs,self.output,progress=progress,cancel=lambda:cancelled)
        self.assertFalse(self.output.exists())

    def test_existing_output_untouched(self):
        self.output.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):
            build_growth(self.target,self.source,self.pairs,self.output)
        self.assertEqual(self.output.read_bytes(),b'keep')

    def test_unplanned_clone_change_detected(self):
        with self.assertRaisesRegex(FormatError,'Unexpected change'):
            compare_regions(Region(io.BytesIO(b'abcdef')),Region(io.BytesIO(b'abcXef')),6,[(0,2)])

    def test_duplicate_runtime_record_refused(self):
        data = executable(len(self.old))
        data[176:200] = data[128:152]
        with self.assertRaisesRegex(FormatError,'unique executable'):
            runtime_record(Region(io.BytesIO(data)),'MODEL.RMD',len(self.old),21)

    def test_record_outside_loaded_segment_refused(self):
        data = executable(len(self.old))
        struct.pack_into('<I',data,52+16,100)
        with self.assertRaisesRegex(FormatError,'unique executable'):
            runtime_record(Region(io.BytesIO(data)),'MODEL.RMD',len(self.old),21)


if __name__ == '__main__':
    unittest.main()
