import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import graduate_radar as g

CFG=json.loads((g.ROOT/'graduate_config.json').read_text())
GOOD=g.row('1','Graduate Software Engineer 2027','New York, NY','Python and SQL. We do sponsor employment visas.','https://example.org/1')

class RadarTests(unittest.TestCase):
    def test_filters(self):
        self.assertIn('Explicit',g.classify(GOOD,CFG)[1])
        for body in ['Without sponsorship','Immediate start','PhD required','Must return to school']:
            self.assertIsNone(g.classify(dict(GOOD,desc=body),CFG))
        self.assertIsNone(g.classify(dict(GOOD,loc='Tokyo'),CFG))
        self.assertIsNone(g.classify(dict(GOOD,deadline='2020-01-01'),CFG))
        self.assertIn('UNVERIFIED',g.classify(dict(GOOD,desc='experience in python'),CFG)[1])
    def test_workable(self):
        job=dict(shortcode='12',title='Graduate Software Engineer',city='New York',country='United States',url=GOOD['url'],description='SQL')
        with patch.object(g,'get_json',return_value={'jobs':[job]}):
            self.assertIn('New York',next(g.fetch('workable','demo'))['loc'])
    def test_strip_tracking(self):
        self.assertEqual(g.identity('https://stripe.com/jobs/search?gh_jid=123'),g.identity('https://stripe.com/careers/listing/job/123'))
    def test_failed_send_retries_and_success_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(g,'STATE',Path(directory)/'seen.json'), patch.object(g,'fetch',return_value=[GOOD]),patch.object(g,'send') as sender:
            cfg=dict(CFG,sources=[['Demo','greenhouse','test']])
            sender.side_effect=RuntimeError('fail')
            with self.assertRaises(RuntimeError):g.run(cfg,False)
            self.assertFalse(g.STATE.exists())
            sender.side_effect=None
            g.run(cfg,False);g.run(cfg,False)
            self.assertEqual(sender.call_args.args[0],'Graduate Radar: no new matches.')
    def test_dry_run(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(g,'STATE',Path(directory)/'seen.json'),patch.object(g,'fetch',return_value=[GOOD]),patch.object(g,'send') as sender,contextlib.redirect_stdout(io.StringIO()):
            g.run(dict(CFG,sources=[['Demo','ashby','test']]),True)
            sender.assert_not_called()
            self.assertFalse(g.STATE.exists())

if __name__=='__main__':unittest.main()
