import tempfile, unittest
from pathlib import Path
from main import DB, atomic_csv

class CollectorTests(unittest.TestCase):
    def test_inclusive_split_has_no_overlap_or_gap(self):
        with tempfile.TemporaryDirectory() as d:
            db=DB(Path(d)/'x.sqlite3'); b=db.batch('youtube',None)
            db.add_task(b['id'],'us','us','en','en',5000,5000)
            t=db.tasks(b['id'])[0]; db.split(t)
            self.assertEqual(t['id'],1)
            # one value cannot split and is explicitly incomplete
            self.assertEqual(db.c.execute('select status from tasks where id=1').fetchone()[0],'incomplete')
            db.add_task(b['id'],'us','us','en','en',6000,6001)
            t=db.tasks(b['id'])[0]; db.split(t)
            leaves=db.c.execute("select low,high from tasks where parent_id=? order by low",(t['id'],)).fetchall()
            self.assertEqual([(x[0],x[1]) for x in leaves],[(6000,6000),(6001,6001)])
            db.close()
    def test_split_preserves_recent_window(self):
        with tempfile.TemporaryDirectory() as d:
            db=DB(Path(d)/'x.sqlite3'); b=db.batch('youtube',None)
            db.add_task(b['id'],'us','us','en','en',5000,9999,recent_days=90)
            db.split(db.tasks(b['id'])[0])
            windows=[x[0] for x in db.c.execute('select recent_days from tasks where parent_id=1')]
            self.assertEqual(windows,[90,90,90,90,90])
            db.close()
    def test_page_and_handles_are_atomic_and_deduped(self):
        with tempfile.TemporaryDirectory() as d:
            db=DB(Path(d)/'x.sqlite3'); b=db.batch('youtube',None)
            db.add_task(b['id'],'us','us','en','en',5000,5499); t=db.tasks(b['id'])[0]
            db.save_page(t['id'],1,['@a'.removeprefix('@'),'b','a'],'one')
            self.assertEqual(db.handles(t['id']),['a','b'])
            self.assertEqual(db.c.execute('select current_page from tasks where id=?',(t['id'],)).fetchone()[0],1)
            db.close()
    def test_csv_bom_header_and_partial_suffix(self):
        with tempfile.TemporaryDirectory() as d:
            p=atomic_csv(Path(d)/'us_en_5000-5499_recentdays30.csv',['alpha'])
            self.assertTrue(p.read_bytes().startswith(b'\xef\xbb\xbfhandle\r\nalpha'))
            q=atomic_csv(Path(d)/'x.csv',['beta'],partial=True)
            self.assertEqual(q.name,'x.partial.csv')
    def test_reset_capture_removes_stale_pages_before_rescan(self):
        with tempfile.TemporaryDirectory() as d:
            db=DB(Path(d)/'x.sqlite3'); b=db.batch('youtube',None)
            db.add_task(b['id'],'us','us','en','en',5000,5499); t=db.tasks(b['id'])[0]
            db.save_page(t['id'],1,['old'],'old-signature')
            db.reset_capture(t['id'])
            self.assertEqual(db.handles(t['id']),[])
            self.assertEqual(db.c.execute('select count(*) from pages where task_id=?',(t['id'],)).fetchone()[0],0)
            self.assertEqual(db.c.execute('select current_page from tasks where id=?',(t['id'],)).fetchone()[0],1)
            db.close()

if __name__=='__main__': unittest.main(verbosity=2)
