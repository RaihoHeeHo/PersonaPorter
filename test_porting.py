import unittest

from mokoi_test import myth_dialog
from porting import transfer_lore
from persona_porter import FormatError
from test_ui import myth


class LoreGame:
    def __init__(self, index, text, controls):
        self.myth = myth(index, text.encode() + b'\0', controls)

    def lore(self, index):
        d = myth_dialog(self.myth, index)
        return d['text'][6:-1].decode(), d['text_size'] - 6


class PortingTests(unittest.TestCase):
    def test_bidirectional_lore_preserves_recipient_controls(self):
        for source_prefix, target_prefix in ((b'\xf2\x05\xff\xff\xf1A', b'\xf2\x08\xff\xff\xf1?'),
                                              (b'\xf2\x08\xff\xff\xf1?', b'\xf2\x05\xff\xff\xf1A')):
            source = LoreGame(1, 'Donor lore.\n', source_prefix)
            target = LoreGame(1, 'Longer target original lore.\n', target_prefix)
            out, text = transfer_lore(target, source, 1, 1)
            self.assertEqual(myth_dialog(out, 1)['text'], target_prefix + b'Donor lore.\n\0')
            self.assertEqual(text, 'Donor lore.\n')
            self.assertEqual(len(out), len(target.myth))

    def test_custom_lore_can_fit_a_smaller_target(self):
        source = LoreGame(1, 'Long donor lore that will not fit.', b'\xf2\x08\xff\xff\xf1?')
        target = LoreGame(1, 'Short.', b'\xf2\x05\xff\xff\xf1A')
        with self.assertRaisesRegex(FormatError, 'Choose a different target'):
            transfer_lore(target, source, 1, 1)
        out, _ = transfer_lore(target, source, 1, 1, 'New.')
        self.assertEqual(myth_dialog(out, 1)['text'][6:], b'New.\0')

    def test_non_ascii_custom_lore_refused(self):
        source = LoreGame(1, 'Donor.', b'\xf2\x08\xff\xff\xf1?')
        target = LoreGame(1, 'Target.', b'\xf2\x05\xff\xff\xf1A')
        with self.assertRaisesRegex(FormatError, 'ASCII'):
            transfer_lore(target, source, 1, 1, 'Pokémon')


if __name__ == '__main__':
    unittest.main()
