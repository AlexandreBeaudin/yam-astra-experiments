"""Offline summary extraction and actual replay JavaScript behavior."""
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import first_action_mock as first
from policy_evidence import response_evidence


class PolicyViewTest(unittest.TestCase):
    def test_only_readable_summary_is_exported(self):
        response = {'output': [
            {'type': 'reasoning', 'encrypted_content': 'OPAQUE_SECRET',
             'content': [{'text': 'not a summary'}],
             'summary': [{'type': 'summary_text', 'text': 'First summary.'}]},
            {'type': 'reasoning', 'summary': [{'type': 'summary_text', 'text': 'Second summary.'}]},
            {'type': 'function_call', 'call_id': 'call-1', 'name': 'observe', 'arguments': '{}'},
        ], 'credentials': 'SECRET', 'usage': {'output_tokens_details': {'reasoning_tokens': 12}}}
        result = response_evidence(response)
        self.assertEqual(result['reasoning_summary'], 'First summary.\n\nSecond summary.')
        self.assertEqual(result['reasoning_tokens'], 12)
        self.assertEqual(result['tool_calls'][0]['call_id'], 'call-1')
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertNotIn('not a summary', json.dumps(result))
        self.assertEqual(response_evidence({'output': [{'type': 'reasoning', 'summary': []}]})['summary_status'], 'NOT RETURNED')

    def test_saved_response_and_causal_ui(self):
        with tempfile.TemporaryDirectory(dir=first.HERE) as tmp:
            output = Path(tmp) / 'view'
            first.run(output)
            page = (output / 'replay.html').read_text()
            payload = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S)[1]
            data = json.loads(payload)
            snapshots = data['snapshots']
            self.assertEqual(snapshots[0]['policy_evidence']['summary_status'], 'NOT RETURNED')
            self.assertNotIn('validation', snapshots[0]['policy_evidence'])
            self.assertNotIn('acceptance', snapshots[1]['execution_evidence'])
            self.assertFalse(snapshots[2]['execution_evidence']['acceptance']['accepted'])
            self.assertNotIn('completion', snapshots[2]['execution_evidence'])
            self.assertIsNone(snapshots[3]['execution_evidence']['completion']['completed'])
            self.assertNotIn('encrypted_content', page)
            self.assertTrue((output / 'saved-frame.jpg').is_file())
            code = re.search(r'<script>\s*(.*?)</script>', page, re.S)[1]
            # Run the real UI code with a minimal DOM, including scrub, missing,
            # multiple-summary and hostile text cases. Browser layout is separate.
            harness = '''
const vm=require('node:vm'), assert=require('node:assert/strict');
const nodes=new Map();
const document={getElementById(id){if(!nodes.has(id))nodes.set(id,{textContent:'',value:0,setAttribute(){},addEventListener(k,fn){this[k]=fn}});return nodes.get(id)}};
document.getElementById('data').textContent=PAYLOAD;
const context=vm.createContext({document,URLSearchParams,location:{search:''},setInterval,clearInterval});
vm.runInContext(CODE,context);
assert.match(nodes.get('reasoning-summary').textContent,/NOT RETURNED/);
assert.match(nodes.get('acceptance').textContent,/MISSING/);
nodes.get('scrub').value=2;nodes.get('scrub').input();
assert.match(nodes.get('acceptance').textContent,/"accepted": false/);
assert.match(nodes.get('completion').textContent,/MISSING/);
nodes.get('scrub').value=3;nodes.get('scrub').input();
assert.match(nodes.get('completion').textContent,/not_available/);
vm.runInContext('showPolicy({policy_evidence:{reasoning_summary:"<img onerror=alert(1)>",summary_status:"RETURNED"}})',context);
assert.equal(nodes.get('reasoning-summary').textContent,'<img onerror=alert(1)>');
vm.runInContext('showPolicy({})',context);
assert.match(nodes.get('proposal').textContent,/MISSING/);
assert.equal(nodes.get('summary-status').textContent,'MISSING');
'''.replace('PAYLOAD', json.dumps(payload)).replace('CODE', json.dumps(code))
            subprocess.run(['node', '-e', harness], check=True, capture_output=True, text=True)


if __name__ == '__main__':
    unittest.main()
