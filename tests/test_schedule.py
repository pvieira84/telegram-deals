import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from schedule import Cron, DigestSchedule

class ScheduleTests(unittest.TestCase):
    def dt(self,s):
        return datetime.fromisoformat(s).replace(tzinfo=ZoneInfo('Europe/Lisbon'))

    def test_poll_late_and_coalesce(self):
        s=DigestSchedule({'digest_cron':'*/15 * * * *'})
        self.assertEqual(s.latest_due(self.dt('2026-10-28T17:31:27')),'2026-10-28 17:30')

    def test_legacy_and_priority(self):
        cfg={'digest_times':['12:30','17:00','20:30']}
        self.assertEqual(DigestSchedule(cfg).latest_due(self.dt('2026-10-28T17:01')),'2026-10-28 17:00')
        cfg['digest_cron']='0 18 * * *'
        self.assertIsNone(DigestSchedule(cfg).latest_due(self.dt('2026-10-28T17:01')))

    def test_multiple_expressions(self):
        s=DigestSchedule({'digest_cron':['30 12,20 * * *','0 17 * * *']})
        self.assertEqual(s.latest_due(self.dt('2026-10-28T17:03')),'2026-10-28 17:00')

    def test_weekday_and_or(self):
        self.assertTrue(Cron('0 9 * * 1-5').matches(self.dt('2026-10-28T09:00')))
        self.assertFalse(Cron('0 9 * * 1-5').matches(self.dt('2026-11-01T09:00')))
        self.assertTrue(Cron('0 9 1 * 3').matches(self.dt('2026-10-28T09:00')))
        self.assertTrue(Cron('0 9 * * 7').matches(self.dt('2026-11-01T09:00')))

    def test_validation(self):
        for expr in ['60 * * * *','*/0 * * * *','0 24 * * *','0 1 * *','0 1 * * MON','0 1 * * 9']:
            with self.assertRaises(ValueError): Cron(expr)

    def test_dst_missing_time(self):
        s=DigestSchedule({'digest_cron':'30 1 * * *'})
        self.assertIsNone(s.latest_due(self.dt('2026-03-29T03:00')))

    def test_dst_repeated_slot_identity(self):
        s=DigestSchedule({'digest_cron':'30 1 * * *'})
        first=self.dt('2026-10-25T01:45').replace(fold=0)
        second=first.replace(fold=1)
        self.assertEqual(s.latest_due(first),s.latest_due(second))
