"""Analysis-only closure cannot silently accept an unresolved API contract."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from verify_analysis import issue_disposes_interface


class AnalysisDesignResolutionTests(unittest.TestCase):
    def test_completed_internal_design_does_not_dispose_external_interface(self):
        rows=[{'id':f'ISSUE-{i}','status':'resolved',
               'resolution_scope':'first_party_analysis_design',
               'related_ids':['API-001']} for i in range(1,6)]
        self.assertFalse(any(issue_disposes_interface(row) for row in rows))

    def test_open_with_analysis_scope_is_still_open(self):
        self.assertFalse(issue_disposes_interface({'status':'open','resolution_scope':'first_party_analysis_design'}))

    def test_legacy_and_explicit_interface_resolution_stay_compatible(self):
        self.assertTrue(issue_disposes_interface({'status':'resolved'}))
        self.assertTrue(issue_disposes_interface({'status':'resolved','resolution_scope':'interface_contract_disposition'}))
        self.assertTrue(issue_disposes_interface({'status':'accepted_risk'}))

    def test_typo_or_unknown_scope_does_not_fall_back_to_legacy_acceptance(self):
        for scope in ['first_party_analysis_desgin','',None,'anything',[],{},0,True]:
            self.assertFalse(issue_disposes_interface({'status':'resolved','resolution_scope':scope}))

    def test_unrelated_status_never_disposes_interface(self):
        for status in ['failed','pending',None]:
            self.assertFalse(issue_disposes_interface({'status':status}))


if __name__=='__main__':unittest.main()
