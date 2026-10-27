"""Round-trip the actual terminal glyphs back to the original QR module matrix."""
import re
import unittest
from oiu.qr import matrix, terminal


class TerminalQRTests(unittest.TestCase):
    def test_half_block_rendering_round_trips_modules_at_both_sizes(self):
        url='https://192.168.1.2:27891/capture?token='+'a'*64
        rows=matrix(url); size=len(rows)
        glyphs={' ':(False,False),'▀':(True,False),'▄':(False,True),'█':(True,True)}
        for scale,columns in ((1,size),(2,size*2)):
            rendered=terminal(url,ansi=True,columns=columns)
            self.assertIn('\x1b[30;47m',rendered)
            raster=[]
            for line in re.sub(r'\x1b\[[0-9;]*m','',rendered).splitlines():
                self.assertLessEqual(len(line),columns)
                raster.extend([[glyphs[c][half] for c in line] for half in (0,1)])
            recovered=[[raster[y*scale][x*scale] for x in range(size)] for y in range(size)]
            self.assertEqual(recovered,rows)
        self.assertTrue(all(not any(row) for row in rows[:4]+rows[-4:]))
        self.assertTrue(all(not any(row[:4]+row[-4:]) for row in rows))
        narrow=terminal(url,columns=size-1)
        self.assertIn('too narrow',narrow)
        self.assertFalse(any(c in narrow for c in ('▀','▄','█')))
