import json
from pathlib import Path
import tempfile
import unittest

from operation_attribution import FILES
from render_documents import RenderError, render_feature_list
from test_retained_operation_contract import fixture


class RetainedOnlyRenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.facts = fixture(self.root)
        self.save()

    def tearDown(self):
        self.tmp.cleanup()

    def save(self):
        for name, relative in FILES.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.facts[name], ensure_ascii=False), encoding='utf-8')

    def test_valid_direct_retained_operation_renders_without_invented_func(self):
        result = render_feature_list(self.root, 'test').read_text(encoding='utf-8')
        self.assertIn('本期没有新增或迭代功能', result)
        self.assertNotIn('FUNC-001', result)

    def test_unowned_or_uncovered_scenario_is_not_silently_accepted(self):
        self.facts['pages']['pages'][0]['actions'][0]['retained_operation_refs'] = []
        self.save()
        with self.assertRaises(RenderError):
            render_feature_list(self.root, 'test')
        self.facts['pages']['pages'][0]['actions'][0]['retained_operation_refs'] = ['existing-query']
        self.facts['scenarios']['scenarios'].append({'id': 'SC-002', 'scope': 'in_scope'})
        self.save()
        with self.assertRaises(RenderError):
            render_feature_list(self.root, 'test')
