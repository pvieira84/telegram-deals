import unittest
from core import product, price, database, record

class CoreTests(unittest.TestCase):
    def test_identity_and_host(self):
        self.assertEqual(product('https://www.amazon.es/dp/B012345678?tag=group'), 'B012345678')
        self.assertEqual(product('https://amazon.es/gp/product/B012345678/ref=abc'), 'B012345678')
        self.assertIsNone(product('https://amazon.es.evil.test/dp/B012345678'))

    def test_prices(self):
        self.assertEqual(price('Oferta 1.299,99€'),129999)
        self.assertEqual(price('€29.95'),2995)
        self.assertIsNone(price('Antes 40€ ahora 29€'))
        self.assertIsNone(price('Descuento 30%'))

    def test_duplicate_and_realert(self):
        db=database(':memory:')
        record(db,'B012345678','first','source1',10000,1,5)
        db.execute('UPDATE deals SET notified=1,pending=0,last_sent=10000')
        record(db,'B012345678','duplicate','source2',10000,2,5)
        self.assertEqual(db.execute('SELECT pending FROM deals').fetchone()[0],0)
        record(db,'B012345678','minor drop','source3',9700,3,5)
        self.assertEqual(db.execute('SELECT pending FROM deals').fetchone()[0],0)
        record(db,'B012345678','real drop','source4',9400,4,5)
        row=db.execute('SELECT * FROM deals').fetchone()
        self.assertEqual(row['pending'],1)
        self.assertEqual(row['cents'],9400)
        record(db,'B012345678','unclear','source5',None,5,5)
        self.assertEqual(db.execute('SELECT cents FROM deals').fetchone()[0],9400)
        self.assertEqual(db.execute('SELECT count(*) FROM deals').fetchone()[0],1)
        self.assertEqual(db.execute('SELECT count(*) FROM origins').fetchone()[0],5)

if __name__=='__main__':
    unittest.main()
