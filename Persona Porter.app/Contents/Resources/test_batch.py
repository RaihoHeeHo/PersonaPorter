import dataclasses
import struct
import unittest

from persona_porter import FormatError
from porting import GameImage, Options, QueueEntry, prepare_batch, validate_queue
from mokoi_test import NAME_PATH, MYTH_PATH, myth_dialog, tbl_sections
from pak import entries
from test_pak import make_pak
from test_ui import names, myth


def texture(value):
    data = bytearray(64 + 1024 + 1)
    struct.pack_into('<I', data, 4, len(data))
    data[8:12] = b'TMX0'
    struct.pack_into('<BBHHBB', data, 16, 1, 0, 1, 1, 0x13, 0)
    data[-1] = value
    return bytes(data)


class Game:
    name_bytes = GameImage.name_bytes
    name = GameImage.name
    lore = GameImage.lore

    def __init__(self, source=True):
        self.serial = 'SLUS_217.82' if source else 'SLUS_216.21'
        self.title = 'Persona 4' if source else 'Persona 3 FES'
        controls = b'\xf2\x05\xff\xff\xf1A' if source else b'\xf2\x08\xff\xff\xf1?'
        self.game = {'controls': controls}
        self.catalog = [{'id': n} for n in (1,2,3)]
        self.names_data = names(1,b'DonorOne' if source else b'TargetOne')
        self.name_offset = tbl_sections(self.names_data)[3][0]
        table = bytearray(self.names_data)
        table[self.name_offset+34:self.name_offset+51] = (b'DonorTwo' if source else b'TargetTwo').ljust(17,b'\0')
        self.names_data = bytes(table)
        self.myth = myth(2,b'Original long lore buffer.\n\0',controls)
        self.bundle = 'DATA.CVM!FACILITY/FCL_ELEVATORGIRL.PAK'
        self.myth_bundles = [(self.bundle, '../help/datMyth.bmd')]
        self.other_myth_bundles = []
        self.data = {self.bundle:make_pak([('unrelated',b'keep'),('../help/datMyth.bmd',self.myth)])}
        for n in (1,2,3):
            for folder,prefix in [('CAMP/PERSONA','PST_'),('CARD/PERSONA','I_PRC')]:
                self.data[f'DATA.CVM!{folder}/{prefix}{n:03X}.TMX'] = texture(n if source else 0)
        self.size = 100 if source else 200

    def read(self,path):
        return self.data[path]

    def file_info(self,path):
        return {'size':self.size}

    def model_paths(self,n):
        return f'BTL.CVM!PS{n:03X}.RMD',f'BTL.CVM!FP{n:03X}.RMD'


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.source, self.target = Game(),Game(False)
        self.queue = [QueueEntry(1,1,Options(custom_lore='First.')),
                      QueueEntry(2,2,Options(custom_lore='Second.'))]

    def test_shared_names_lore_and_packed_copies_keep_all_edits(self):
        original_names,original_lore = self.target.names_data,self.target.myth
        pairs,payloads,descriptions = prepare_batch(self.source,self.target,self.queue)
        self.assertEqual(len(pairs),4)
        self.assertEqual(len({key.casefold() for key in payloads}),len(payloads))
        outnames = payloads[NAME_PATH]
        for n,label in [(1,b'DonorOne'),(2,b'DonorTwo')]:
            offset = self.target.name_offset+n*17
            self.assertEqual(outnames[offset:offset+17].rstrip(b'\0'),label)
        lore = payloads[MYTH_PATH]
        self.assertEqual(myth_dialog(lore,1)['text'][6:],b'First.\0')
        self.assertEqual(myth_dialog(lore,2)['text'][6:],b'Second.\0')
        self.assertEqual(myth_dialog(lore,0),myth_dialog(original_lore,0))
        packed = payloads[self.target.bundle]
        member = entries(packed)[1]
        self.assertEqual(packed[member['offset']:member['offset']+member['size']],lore)
        self.assertEqual(self.target.names_data,original_names)
        self.assertEqual(self.target.myth,original_lore)
        self.assertEqual([d['lore_text'] for d in descriptions],['First.','Second.'])
        for n in (1,2):
            for folder,prefix in [('CAMP/PERSONA','PST_'),('CARD/PERSONA','I_PRC')]:
                path=f'DATA.CVM!{folder}/{prefix}{n:03X}.TMX'
                self.assertEqual(payloads[path],self.source.read(path))

    def test_per_entry_choices_do_not_overwrite_other_slots(self):
        second = Options(name=False,portrait=False,card=False,lore=False)
        _,payloads,_ = prepare_batch(self.source,self.target,[self.queue[0],QueueEntry(2,2,second)])
        offset=self.target.name_offset+34
        self.assertEqual(payloads[NAME_PATH][offset:offset+17],self.target.names_data[offset:offset+17])
        self.assertEqual(myth_dialog(payloads[MYTH_PATH],2),myth_dialog(self.target.myth,2))
        self.assertNotIn('DATA.CVM!CAMP/PERSONA/PST_002.TMX',payloads)

    def test_queue_order_does_not_lose_shared_edits(self):
        forward = prepare_batch(self.source,self.target,self.queue)
        backward = prepare_batch(self.source,self.target,reversed(self.queue))
        self.assertEqual(forward[1],backward[1])

    def test_duplicate_target_refused(self):
        with self.assertRaisesRegex(FormatError,'already in the queue'):
            prepare_batch(self.source,self.target,[self.queue[0],QueueEntry(2,1)])

    def test_donor_can_be_reused_for_different_targets(self):
        pairs,_,_ = prepare_batch(self.source,self.target,[self.queue[0],QueueEntry(1,2,Options(custom_lore='Again.'))])
        self.assertEqual(pairs[0][0],pairs[2][0])
        self.assertNotEqual(pairs[0][1],pairs[2][1])

    def test_empty_queue_refused(self):
        with self.assertRaisesRegex(FormatError,'at least one'):
            validate_queue([])

    def test_bad_later_lore_fails_whole_queue(self):
        with self.assertRaisesRegex(FormatError,'Queue item 2'):
            prepare_batch(self.source,self.target,[self.queue[0],QueueEntry(2,2,Options(custom_lore='x'*500))])

    def test_growth_permission_is_respected_per_entry(self):
        self.source.size = 300
        with self.assertRaisesRegex(FormatError,'Queue item 2.*larger'):
            prepare_batch(self.source,self.target,[self.queue[0],QueueEntry(2,2,Options(expand_models=False,custom_lore='Two.'))])

    def test_entries_are_immutable_snapshots(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.queue[0].options.custom_lore='Changed'

    def test_reverse_direction_merges_all_edits(self):
        _,payloads,_=prepare_batch(self.target,self.source,self.queue)
        self.assertEqual(myth_dialog(payloads[MYTH_PATH],1)['text'],self.source.game['controls']+b'First.\0')
        self.assertEqual(myth_dialog(payloads[MYTH_PATH],2)['text'],self.source.game['controls']+b'Second.\0')


if __name__=='__main__':
    unittest.main()
