import struct
import unittest
from dataclasses import replace

from persona_porter import FormatError
from gameplay import GameplayTables, GameplayEdit, Skill, AFFINITIES
from mokoi_test import tbl_sections
from test_batch import Game as BatchGame
from porting import QueueEntry, Options, prepare_batch


def table(parts):
    out=bytearray()
    for part in parts:
        out += struct.pack('<I',len(part))+part
        out += bytes((-len(out))%16)
    return bytes(out)


class Game:
    def __init__(self,p3=True):
        self.serial='SLUS_216.21' if p3 else 'SLUS_217.82'
        self.data={}
        names=bytearray((624 if p3 else 576)*19)
        for i,name in [(0,'Attack'),(1,'Agi'),(2,'Agilao'),(3,'Agidyne'),(112,'Bash'),(600,'P3 only')]:
            if i*19<len(names):names[i*19:i*19+len(name)]=name.encode()
        self.names_data=table([bytes(672),names,bytes(21),bytes(256*17),bytes(16)])
        for suffix,level in ([('',1),('_F',25)] if p3 else [('',1)]):
            stats=bytearray(14*256)
            growth=bytearray(70*256)
            affinities=bytearray(32*256)
            for i in range(256):
                pos=i*14
                stats[pos:pos+14]=bytes((128,0,1,level,2,3,4,5,6,0xAA,1,0xBB,0xCC,0xDD))
                pos=i*70
                growth[pos:pos+6]=b'\x0a\x0b\x0c\x0d\x0e\xff'
                struct.pack_into('<BBHBBH',growth,pos+6,0,1,112,1,1,1)
                if p3:struct.pack_into('<BBH',growth,pos+14,1,4,0)
                struct.pack_into('<16H',affinities,i*32,*([20]*16))
            # Preserve unusual original fields outside the standard editor controls.
            struct.pack_into('<H',affinities,32+7*2,10)
            self.data['BTL.CVM!BATTLE/PERSONA'+suffix+'.TBL']=table([stats,growth,bytes(32)])
            self.data['BTL.CVM!BATTLE/UNIT'+suffix+'.TBL']=table([bytes(64),bytes(32),affinities])
    def read(self,path):return self.data[path]


class GameplayTests(unittest.TestCase):
    def setUp(self):
        self.game=Game()
        self.tables=GameplayTables(self.game)

    def edit(self,**kw):
        return GameplayEdit(**kw)

    def test_only_selected_fields_change_and_layout_is_preserved(self):
        t=self.tables
        original=t.record(1,'journey')
        affinities=tuple((name,AFFINITIES['Drain']) for name,_ in original.affinities)
        edit=self.edit(level=10,stats=(11,12,13,14,15),affinities=affinities,skills=(Skill(112),Skill(1,12),Skill(2,14)))
        payloads={};description=t.apply(1,(edit,),payloads)
        new=t.record(1,'journey',payloads)
        self.assertEqual((new.level,new.stats,new.affinities,new.skills),(10,(11,12,13,14,15),affinities,edit.skills))
        path,pos,growth,unit,affinity=t.offsets(1,'journey')
        allowed=set(range(pos+3,pos+9))|set(range(growth+6,growth+70))
        self.assertTrue(all(i in allowed for i,(a,b) in enumerate(zip(t.raw[path],payloads[path])) if a!=b))
        allowed={affinity+field*2+byte for _,field in t.elements for byte in (0,1)}
        self.assertTrue(all(i in allowed for i,(a,b) in enumerate(zip(t.raw[unit],payloads[unit])) if a!=b))
        self.assertEqual(len(t.raw[path]),len(payloads[path]))
        self.assertEqual(len(t.raw[unit]),len(payloads[unit]))
        self.assertEqual(tbl_sections(t.raw[path]),tbl_sections(payloads[path]))
        self.assertEqual(t.record(2,'journey'),t.record(2,'journey',payloads))
        self.assertEqual(t.record(1,'answer'),t.record(1,'answer',payloads))
        self.assertEqual(description[0]['after']['level'],10)

    def test_both_campaigns_and_multiple_slots_accumulate(self):
        payloads={}
        for i in (1,2):
            self.tables.apply(i,(self.edit(level=i+10,stats=(i,)*5),
                                 self.edit(campaign='answer',level=i+30,stats=(i+1,)*5)),payloads)
        for i in (1,2):
            self.assertEqual(self.tables.record(i,'journey',payloads).level,i+10)
            self.assertEqual(self.tables.record(i,'answer',payloads).level,i+30)

    def test_stats_only_keeps_growth_offsets_and_affinities(self):
        payloads={};t=self.tables
        t.apply(1,(self.edit(level=10,stats=(9,)*5),),payloads)
        path,_,growth,unit,_=t.offsets(1,'journey')
        self.assertEqual(payloads[path][growth:growth+70],t.raw[path][growth:growth+70])
        self.assertNotIn(unit,payloads)
        self.assertEqual(t.record(1,'journey',payloads).skills[-1].level,11)

    def test_p3_final_marker_and_p4_zero_tail(self):
        for p3 in (True,False):
            t=GameplayTables(Game(p3));camp=t.campaigns[0];payloads={}
            t.apply(1,(self.edit(campaign=camp,skills=(Skill(1),Skill(2,4))),),payloads)
            path,_,growth,_,_=t.offsets(1,camp)
            self.assertEqual(struct.unpack_from('<BBH',payloads[path],growth+14),(3,4,0) if p3 else (0,0,0))
            self.assertFalse(any(payloads[path][growth+18:growth+70]))

    def test_bad_levels_stats_duplicates_and_schedule_refused(self):
        for edit in (self.edit(level=0,stats=(1,)*5),self.edit(level=100,stats=(1,)*5),
                     self.edit(level=1,stats=(0,1,1,1,1)),self.edit(level=1,stats=(1,)*4),
                     self.edit(level=1),self.edit(skills=(Skill(1),Skill(1,5))),
                     self.edit(skills=(Skill(1,1),)),self.edit(skills=(Skill(1,100),))):
            with self.subTest(edit=edit),self.assertRaises(FormatError):self.tables.validate(1,edit)

    def test_wrong_campaign_and_party_slots_refused(self):
        for index,edit in [(192,self.edit()),(0,self.edit()),(1,self.edit(campaign='main'))]:
            with self.assertRaises(FormatError):self.tables.validate(index,edit)

    def test_duplicate_campaign_refused(self):
        with self.assertRaisesRegex(FormatError,'one gameplay edit'):
            self.tables.apply(1,(self.edit(),self.edit()),{})

    def test_unknown_affinity_can_be_preserved_but_not_invented(self):
        old=self.tables.record(1,'journey').affinities
        self.tables.validate(1,self.edit(affinities=old))
        invalid=tuple((name,12345) for name,_ in old)
        with self.assertRaisesRegex(FormatError,'Unsupported'):
            self.tables.validate(1,self.edit(affinities=invalid))
        with self.assertRaisesRegex(FormatError,'elements'):
            self.tables.validate(1,self.edit(affinities=(('Physical',20),)))

    def test_skill_capacity_and_starting_limit(self):
        t=self.tables
        t.skill_names.update({i:f'Named {i}' for i in range(1,17)})
        with self.assertRaisesRegex(FormatError,'eight starting'):
            t.validate(1,self.edit(skills=tuple(Skill(i) for i in range(1,10))))
        with self.assertRaisesRegex(FormatError,'at most 15'):
            t.validate(1,self.edit(skills=tuple(Skill(i,i+1) for i in range(1,17))))
        payloads={}
        t.apply(1,(self.edit(skills=tuple(Skill(i,i+1) for i in range(1,16))),),payloads)
        self.assertEqual(len(t.record(1,'journey',payloads).skills),15)
        path,_,growth,_,_=t.offsets(1,'journey')
        self.assertEqual(struct.unpack_from('<BBH',payloads[path],growth+66),(15,4,0))

    def test_disabling_gameplay_keeps_the_old_payload_set(self):
        payloads={'unrelated':b'keep'}
        self.assertEqual(self.tables.apply(1,(),payloads),[])
        self.assertEqual(payloads,{'unrelated':b'keep'})

    def test_cross_game_skill_id_is_rejected(self):
        self.tables.validate(1,self.edit(skills=(Skill(600),)))
        p4=GameplayTables(Game(False))
        with self.assertRaisesRegex(FormatError,'named skill'):
            p4.validate(1,self.edit(campaign='main',skills=(Skill(600),)))

    def test_unknown_layout_refused(self):
        self.game.data['BTL.CVM!BATTLE/PERSONA.TBL']=table([bytes(10)])
        with self.assertRaisesRegex(FormatError,'layout'):GameplayTables(self.game)

    def test_batch_ui_and_gameplay_edits_coexist(self):
        source,target=BatchGame(),BatchGame(False)
        target._gameplay_tables=self.tables
        queue=[QueueEntry(1,1,Options(custom_lore='One.'),(self.edit(level=10,stats=(10,)*5),)),
               QueueEntry(2,2,Options(custom_lore='Two.'),(self.edit(skills=(Skill(1),Skill(2,5))),))]
        pairs,payloads,descriptions=prepare_batch(source,target,queue)
        self.assertEqual(len(pairs),4)
        self.assertEqual(self.tables.record(1,'journey',payloads).level,10)
        self.assertEqual(self.tables.record(2,'journey',payloads).skills,(Skill(1),Skill(2,5)))
        self.assertTrue(all(d['gameplay'] for d in descriptions))


if __name__=='__main__':unittest.main()
