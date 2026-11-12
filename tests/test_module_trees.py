"""Acceptance checks for the first verified module-derived editions (kept in history)."""
import json
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
EXPECTED={'600':'DecisionDiscountRust','601':'DecisionRefusalDiscountRust','602':'DecisionSurchargeRust','611':'DecisionOkvedKp','612':'DecisionOkvedVp','86':'insuredPerson','87':'insuredPersonResponse','88':'mismatchInfo'}

class RequiredModuleTreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog=json.loads((ROOT/'data/catalog.json').read_bytes())
        cls.status={r['revision_id']:r for r in json.loads((ROOT/'data/fields/index.json').read_bytes())}
    def edition(self,number,env):
        return next(r for r in self.catalog['revisions'] if r['number']==number and r['environment']==env and r.get('source_type')=='module')
    def fields(self,edition):
        return json.loads((ROOT/'data/fields'/(edition['revision_id']+'.json')).read_bytes())['fields']
    def test_all_requested_roots_in_both_contours_are_ready_and_distinct(self):
        ids=set()
        for number,root in EXPECTED.items():
            for env in ['prod','test']:
                r=self.edition(number,env);ids.add(r['revision_id'])
                self.assertEqual(r['root'],root)
                self.assertEqual(self.status[r['revision_id']]['status'],'ready')
                fields=self.fields(r)
                self.assertEqual(fields[0]['name'],root)
                self.assertEqual(len({f['path'] for f in fields}),len(fields))
                self.assertTrue(r['dependencies'])
        self.assertEqual(len(ids),16)
    def test_initial_editions_preserve_actual_constraints_and_enumerations(self):
        # Historical editions remain immutable when a future SFR edition changes rules.
        for env in ['prod','test']:
            for number in ['600','601','602','611','612']:
                r=self.edition(number,env);fields={f['path']:f for f in self.fields(r)}
                code=fields[r['root']+'/department/code']
                self.assertIn(['maxLength','4'],code['facets'])
                self.assertEqual((code['min'],code['max']),('1','1'))
            fields={f['path']:f for f in self.fields(self.edition('86',env))}
            self.assertIn(['minLength','1'],fields['insuredPerson/fullName/firstName']['facets'])
            self.assertIn(['maxLength','60'],fields['insuredPerson/fullName/firstName']['facets'])
            self.assertEqual(fields['insuredPerson/fullName/middleName']['min'],'0')
            fields={f['path']:f for f in self.fields(self.edition('87',env))}
            self.assertIn(['length','11'],fields['insuredPersonResponse/snils']['facets'])
            self.assertEqual({v for v,_ in fields['insuredPersonResponse/status']['values']},{'RECEIVED','ERROR','PROCESSED'})
