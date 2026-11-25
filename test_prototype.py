from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import subprocess
import sys

import core

START, END = '2026-12-01', '2026-12-08'


class PrototypeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'test.sqlite3'
        core.initialize(self.path)

    def test_filter_and_quote(self):
        with core.connect(self.path) as db:
            self.assertEqual([u['id'] for u in core.catalog(db,START,END,300)], [1,4])
            self.assertEqual([u['id'] for u in core.catalog(db,START,END,0,'Холодильный')], [3])
            self.assertEqual(core.quote(db,1,START,END)[1:], (7,15120,105840))

    def test_period_boundaries(self):
        self.assertEqual(core.period('2028-02-29','2028-03-01'),1)
        self.assertEqual(core.period(START,'2027-12-01'),365)
        for start,end in [(START,START),(END,START),('2026-02-29',END),(START,'2027-12-02'),('20261201',END)]:
            with self.subTest(start=start,end=end), self.assertRaises(ValueError):
                core.period(start,end)

    def test_intervals_and_same_unit(self):
        with core.connect(self.path) as db:
            rid = core.submit(db,1,'ООО Север',START,END)
            db.commit()
            core.decide(db,rid,True)
            for start,end,expected in [(START,END,True),('2026-12-02','2026-12-03',True),
                                       ('2026-11-30','2026-12-10',True),('2026-11-28',START,False),
                                       (END,'2026-12-09',False)]:
                self.assertEqual(core.conflict(db,1,start,end),expected)
            self.assertFalse(core.conflict(db,2,START,END))

    def test_cancel_and_close(self):
        with core.connect(self.path) as db:
            first = core.submit(db,1,'ООО Север',START,END)
            db.commit()
            lease = core.decide(db,first,True)
            core.move_lease(db,lease,'cancel')
            self.assertFalse(core.conflict(db,1,START,END))
            db.commit()
            second = core.submit(db,1,'ООО Восток',START,END)
            db.commit()
            loan = core.decide(db,second,True)
            core.move_lease(db,loan,'activate')
            with self.assertRaises(ValueError):
                core.move_lease(db,loan,'cancel')
            core.move_lease(db,loan,'close')
            self.assertTrue(core.conflict(db,1,START,END))
        with core.connect(self.path) as db:
            self.assertEqual(core.journal(db)[0]['lease_status'],'CLOSED')

    def test_reject_and_repeat(self):
        with core.connect(self.path) as db:
            rid = core.submit(db,1,'ООО Восток',START,END)
        with core.connect(self.path) as db:
            self.assertIsNone(core.decide(db,rid,False))
        with self.assertRaises(ValueError), core.connect(self.path) as db:
            core.decide(db,rid,True)
        with core.connect(self.path) as db:
            self.assertEqual(core.journal(db)[0]['status'],'REJECTED')
            self.assertIsNone(core.journal(db)[0]['lease_id'])

    def test_failed_submit_has_no_side_effect(self):
        with self.assertRaises(ValueError), core.connect(self.path) as db:
            core.submit(db,1,' ',START,END)
        with core.connect(self.path) as db:
            self.assertEqual(core.journal(db),[])

    def test_database_trigger_rolls_back_approval(self):
        ids = []
        for company in ('ООО Север','ООО Восток'):
            with core.connect(self.path) as db:
                ids.append(core.submit(db,1,company,START,END))
        with core.connect(self.path) as db:
            core.decide(db,ids[0],True)
        with self.assertRaisesRegex(sqlite3.IntegrityError,'period overlaps'), core.connect(self.path) as db:
            db.execute("UPDATE request SET status='APPROVED' WHERE id=?",(ids[1],))
        with core.connect(self.path) as db:
            self.assertEqual(core.journal(db)[0]['status'],'NEW')

    def test_invalid_foreign_key_and_date(self):
        sql = 'INSERT INTO request(unit_id,company,start_on,end_on,daily_price,total) VALUES(?,?,?,?,?,?)'
        for args in [(99,'ООО Север',START,END,1,7),(1,'ООО Север','2026-02-29','2026-03-03',1,2)]:
            with self.subTest(args=args), self.assertRaises(sqlite3.IntegrityError), core.connect(self.path) as db:
                db.execute(sql,args)

    def test_initial_request_status(self):
        with self.assertRaisesRegex(sqlite3.IntegrityError,'invalid initial'), core.connect(self.path) as db:
            db.execute(
                "INSERT INTO request(unit_id,company,start_on,end_on,daily_price,total,status) "
                "VALUES(1,'ООО Север',?,?,1,7,'APPROVED')", (START,END)
            )
        with core.connect(self.path) as db:
            self.assertEqual(core.journal(db),[])

    def test_snapshot_stays_fixed(self):
        with core.connect(self.path) as db:
            rid = core.submit(db,1,'ООО Север',START,END)
        with core.connect(self.path) as db:
            db.execute('UPDATE unit SET rate=50 WHERE id=1')
            self.assertEqual(core.journal(db)[0]['total'],105840)
            self.assertEqual(core.quote(db,1,START,END)[-1],147000)
        with self.assertRaises(sqlite3.IntegrityError), core.connect(self.path) as db:
            db.execute('UPDATE request SET total=1 WHERE id=?',(rid,))

    def test_competing_connections(self):
        ids = []
        for company in ('ООО Север','ООО Восток'):
            with core.connect(self.path) as db:
                ids.append(core.submit(db,1,company,START,END))
        barrier = threading.Barrier(2)
        def approve(rid):
            barrier.wait(timeout=3)
            try:
                with core.connect(self.path) as db:
                    core.decide(db,rid,True)
                return 'approved'
            except ValueError:
                return 'conflict'
        with ThreadPoolExecutor(2) as pool:
            self.assertCountEqual(pool.map(approve,ids),['approved','conflict'])
        with core.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM lease').fetchone()[0],1)

    def test_register(self):
        with core.connect(self.path) as db:
            core.register_unit(db,'wh-500','Новый склад','Москва, 1','Сухой',300,32)
            self.assertIn('WH-500',[u['code'] for u in core.catalog(db,START,END,300,'Сухой')])
            with self.assertRaises(sqlite3.IntegrityError):
                core.register_unit(db,'wh-101','Копия','Москва, 1','Сухой',100,1)

    def run_console(self, *commands):
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', str(core.ROOT / 'app.py'), '--db', str(self.path)],
            input='\n'.join(commands) + '\n', encoding='utf-8',
            capture_output=True, timeout=15, check=True,
        )
        return result.stdout

    def test_console_request_and_persistence(self):
        output = self.run_console(
            '1', START, END, '300', '0',
            '2', '1', 'ООО Тест', START, END, '3', '0',
        )
        self.assertIn('WH-101', output)
        self.assertIn('WH-402', output)
        self.assertNotIn('WH-204', output)
        self.assertIn('Заявка № 1 создана', output)
        self.assertIn('Итоговая стоимость: 105 840 руб.', output)
        self.assertIn('На рассмотрении', output)
        self.assertIn('ООО Тест', self.run_console('3', '0'))
        with core.connect(self.path) as db:
            request = core.journal(db)[0]
            self.assertEqual((request['total'], request['status']), (105840, 'NEW'))

    def test_console_rejects_errors_and_runs_lifecycle(self):
        output = self.run_console(
            '2', 'не число',
            '2', '1', 'ООО Тест', START, START,
            '2', '1', 'ООО Тест', START, END,
            '4', '1', '1',
            '2', '1', 'ООО Конфликт', START, END,
            '5', '1', '1', '5', '1', '3', '3', '0',
        )
        for message in (
            'Введите целое число', 'от 1 до 365 суток', 'Бронь № 1 создана',
            'Помещение занято', 'Статус брони: Передано', 'Возвращено',
        ):
            self.assertIn(message, output)
        with core.connect(self.path) as db:
            self.assertEqual([(r['status'], r['lease_status']) for r in core.journal(db)],
                             [('APPROVED', 'CLOSED')])


if __name__=='__main__':
    unittest.main()
