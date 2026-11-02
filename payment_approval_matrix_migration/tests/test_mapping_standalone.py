"""Run directly with Python; no Odoo database is required."""
import importlib.util
from pathlib import Path
import unittest
import base64
from datetime import datetime, timedelta

spec = importlib.util.spec_from_file_location('migration_mapping', Path(__file__).parents[1] / 'mapping.py')
mapping = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mapping)


class MappingTests(unittest.TestCase):
    def test_legacy_alternative_approvers_at_same_level_are_unioned(self):
        lines = [{'level': 1, 'user_ids': [2], 'min_amount': 0, 'max_amount': 100},
                 {'level': 1, 'user_ids': [3, 2], 'min_amount': 0, 'max_amount': 100}]
        self.assertEqual(mapping.coalesce_levels(lines, 'amount')[0]['user_ids'], [2, 3])
        lines[1]['max_amount'] = 200
        with self.assertRaises(ValueError):
            mapping.coalesce_levels(lines, 'amount')
        self.assertEqual(mapping.coalesce_levels(lines, 'none')[0]['user_ids'], [2, 3])

    def test_signature_matching_ignores_base64_representation(self):
        a = base64.b64encode(b'original signature')
        self.assertEqual(mapping.signature_digest(a), mapping.signature_digest(a.decode() + '\n'))
        self.assertNotEqual(mapping.signature_digest(a), mapping.signature_digest(base64.b64encode(b'other signature')))
        self.assertIsNone(mapping.signature_digest(None))
    def test_same_second_chatter_matches_microsecond_creation(self):
        source = datetime(2026, 8, 26, 7, 31, 18, 186535)
        message = datetime(2026, 8, 26, 7, 31, 18)
        self.assertTrue(mapping.decision_time_matches(message, source))
        self.assertEqual(source.microsecond, 186535)

    def test_earlier_second_chatter_is_rejected(self):
        source = datetime(2026, 8, 26, 7, 31, 18, 186535)
        self.assertFalse(mapping.decision_time_matches(datetime(2026, 8, 26, 7, 31, 17, 999999), source))

    def test_equal_and_later_chatter_times_match(self):
        source = datetime(2026, 8, 26, 7, 31, 18, 186535)
        self.assertTrue(mapping.decision_time_matches(source, source))
        self.assertTrue(mapping.decision_time_matches(source + timedelta(seconds=1), source))

    def test_missing_decision_dates_are_rejected(self):
        source = datetime(2026, 8, 26, 7, 31, 18)
        self.assertFalse(mapping.decision_time_matches(None, source))
        self.assertFalse(mapping.decision_time_matches(source, None))

    def test_legacy_status_must_match_last_level(self):
        self.assertEqual(mapping.legacy_status('Level 2 Approved', [1, 2]), 'pending')
        self.assertEqual(mapping.legacy_status('Rejected By 2 [ Admin ]', [1, 2]), 'rejected')
        self.assertEqual(mapping.legacy_status('Done', [1, 2]), 'done')
        for status in ['Draft', 'Level 3 Approved', 'Rejected By 1 [ Admin ]', 'garbage']:
            with self.subTest(status=status), self.assertRaises(ValueError):
                mapping.legacy_status(status, [1, 2])

    def test_real_legacy_action(self):
        self.assertEqual(mapping.parse_legacy_action(
            'self.env["account.move"].browse(42).with_context(rollback=True).action_post()'),
            ('account.move', 42, 'action_post'))

    def test_untrusted_actions_are_not_accepted(self):
        for action in ['__import__("os").system("echo bad")',
                       'self.env["account.move"].browse(True).with_context(rollback=True).action_post()',
                       'self.env["account.move"].browse(1).with_context(rollback=False).action_post()',
                       'self.env["account.move"].browse(1).with_context(rollback=True)._private()',
                       'self.env["account.move"].browse(1).with_context(rollback=True).action_post(x=1)']:
            with self.subTest(action=action):
                self.assertIsNone(mapping.parse_legacy_action(action))

    def test_valid_pending_with_nonconsecutive_levels(self):
        mapping.validate_sequence([1, 3], ['approved', 'approved'], [1, 3, 5], 'pending')

    def test_duplicate_cycles_and_gaps_blocked(self):
        for levels in [[1, 1], [2], []]:
            with self.assertRaises(ValueError):
                mapping.validate_sequence(levels, ['approved'] * len(levels), [1, 2, 3], 'pending')

    def test_unknown_is_never_approved(self):
        with self.assertRaises(ValueError):
            mapping.validate_sequence([1], ['unknown'], [1], 'done')

    def test_completed_requires_all_levels(self):
        with self.assertRaises(ValueError):
            mapping.validate_sequence([1], ['approved'], [1, 2], 'done')
        mapping.validate_sequence([1, 2], ['approved', 'approved'], [1, 2], 'done')

    def test_rejection_must_be_last(self):
        mapping.validate_sequence([1, 2], ['approved', 'rejected'], [1, 2, 3], 'rejected')
        with self.assertRaises(ValueError):
            mapping.validate_sequence([1, 2], ['rejected', 'approved'], [1, 2], 'rejected')

    def test_fully_approved_is_not_pending(self):
        with self.assertRaises(ValueError):
            mapping.validate_sequence([1], ['approved'], [1], 'pending')


if __name__ == '__main__':
    unittest.main()
