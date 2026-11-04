"""Classifier-lane and error coverage regressions; no credentials or network."""
import importlib.util
from pathlib import Path
import sys
import unittest

TOOLS=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))
spec=importlib.util.spec_from_file_location('calibration_under_test',TOOLS/'deai_calibrate.py')
calibration=importlib.util.module_from_spec(spec);spec.loader.exec_module(calibration)

class CalibrationLaneTests(unittest.TestCase):
    def test_facts_cannot_leak_into_a_style_vote(self):
        for kind in ('continuity_conflict','date_arithmetic','era_conflict'):
            raw=dict(checked_pids=['P1'],issues=[dict(sid='S1',type=kind,reason='事实问题',severity=3)])
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                calibration.validate(raw,{'S1'},'verb_mismatch',{'P1'})
            calibration.validate(raw,{'S1'},'facts')

    def test_missing_paragraphs_do_not_become_no_hits(self):
        with self.assertRaises(ValueError):
            calibration.validate(dict(checked_pids=['P1'],issues=[]),{'S1'},'verb_mismatch',{'P1','P2'})
        calibration.validate(dict(checked_pids=['P1','P2'],issues=[]),{'S1'},'verb_mismatch',{'P1','P2'})

    def test_unknown_ids_and_boolean_severity_are_invalid(self):
        for sid,severity in (('S9',2),('S1',True)):
            with self.subTest(sid=sid,severity=severity),self.assertRaises(ValueError):
                calibration.validate(dict(checked_pids=['P1'],issues=[dict(sid=sid,type='verb_mismatch',reason='依据',severity=severity)]),{'S1'},'verb_mismatch',{'P1'})

    def test_rules_are_not_discarded_by_empty_model_votes(self):
        rule=dict(sid='S1',type='cliche_simile',severity=2,source='rule')
        self.assertEqual(calibration.merge_hits([rule],[]),[rule])
        fact=dict(rule,type='date_arithmetic',source='model')
        self.assertEqual(calibration.merge_hits([rule],[fact]),[rule])
