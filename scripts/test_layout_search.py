"""Keyword search over the layout cards. No ollama."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))

import layout_search as LS  # noqa: E402


class T(unittest.TestCase):
    def test_pad_query_hits_the_block_card_first(self):
        rows = LS.cards()
        scores = LS.keyword_scores('bond pad ring adc', rows)
        best = rows[max(range(len(rows)), key=lambda i: scores[i])]
        self.assertIn('not-a-chip', best['id'])

    def test_torn_index_falls_back_to_keyword(self):
        import tempfile
        real = LS.INDEX
        try:
            d = Path(tempfile.mkdtemp())
            LS.INDEX = d / 'index.json'
            LS.INDEX.write_text('{"model": "x", "cards":')  # truncated, as a crash mid-write leaves it
            self.assertIsNone(LS._load_index())
            rc = LS.search('bond pad', k=1)
            self.assertEqual(rc, 0)
        finally:
            LS.INDEX = real

    def test_laygo_query_hits_the_flow_card(self):
        rows = LS.cards()
        scores = LS.keyword_scores('laygo2 placement grid route', rows)
        best = rows[max(range(len(rows)), key=lambda i: scores[i])]
        self.assertIn('laygo-flow', best['id'])


if __name__ == '__main__':
    unittest.main()
