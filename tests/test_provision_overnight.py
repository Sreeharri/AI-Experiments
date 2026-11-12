import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock
import run_eurlex_provision_overnight as overnight

class OvernightTests(unittest.TestCase):
    def test_schedule(self):
        self.assertEqual([overnight.retry_delay(i) for i in range(7)], [900,1800,2700,3600,3600,3600,3600])

    def test_errors(self):
        for error in ['<urlopen error [WinError 10060] timeout>', 'HTTP Error 503', 'Connection reset by peer', 'getaddrinfo failed', 'HTTP 429']:
            self.assertTrue(overnight.connection_failure(error), error)
        for error in ['HTTP Error 401: Unauthorized', 'HTTP Error 403', 'Invalid response after correction', 'Enriched inputs changed', '[503/600] Article 400', 'No such file', 'Context limit exceeded']:
            self.assertFalse(overnight.connection_failure(error), error)

    def test_checkpoints(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); audit=root/'audit/act'; audit.mkdir(parents=True)
            rows=[{'level':'DIRECT'}, {'level':'IGNORE'}, {'not_assessable':{'reason':'empty'}}, {'ignore_review':{'status':'pending'}}, {'failure':{'error':'bad'}}]
            for i,row in enumerate(rows):
                (audit/f'{i}.json').write_text(json.dumps(row))
            self.assertEqual(overnight.checkpoint_counts(root),(2,1))

    def test_recover_and_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            def write_json(path,value):
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps(value))
            runner=SimpleNamespace(ROOT=root,write_json=write_json)
            args=SimpleNamespace(output_dir=root,enriched_dir=root/'enriched',anchors=root/'anchors',env_file=root/'.env',thinking='off',confirm_thinking='off',workers=4,document_run=None)
            with patch.object(overnight,'run_attempt',side_effect=[(1,'Provision screening stopped: <urlopen error timed out>')]*5+[(0,'Saved')]) as attempt, patch.object(overnight,'wait_for_retry') as wait:
                self.assertEqual(overnight.run_batch_with_recovery(runner,args,root/'batches/0032',{'act':'v'}),0)
            self.assertEqual([call.args[0] for call in wait.call_args_list],[900,1800,2700,3600,3600])
            self.assertTrue(all(call.args[0]==attempt.call_args.args[0] for call in attempt.call_args_list))
            with patch.object(overnight,'run_attempt',return_value=(1,'HTTP Error 401')), patch.object(overnight,'wait_for_retry') as wait:
                self.assertEqual(overnight.run_batch_with_recovery(runner,args,root/'batches/0032',{}),1)
                wait.assert_not_called()

    def test_real_process(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            code,output=overnight.run_attempt([sys.executable,'-c',"print('HTTP Error 503'); raise SystemExit(1)"],root,root/'log.txt',Mock())
            self.assertEqual(code,1)
            self.assertIn('HTTP Error 503',output)

    def test_countdown(self):
        with patch.object(overnight.time,'monotonic',side_effect=[0,0,5,10]), patch.object(overnight.time,'sleep') as sleep:
            overnight.wait_for_retry(10,Mock())
        self.assertEqual([call.args[0] for call in sleep.call_args_list],[5,5])

    def test_cancel(self):
        with patch.object(overnight.time,'sleep',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                overnight.wait_for_retry(900,Mock())

if __name__=='__main__': unittest.main()
