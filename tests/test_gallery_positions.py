import unittest
from evaluation.gallery import reconcile_positions


class GalleryPositionTests(unittest.TestCase):
    def setUp(self):
        self.observed=[{'alt':'Bild 1 von 2','src':'https://i.ebayimg.com/images/g/z/s-l140.webp'},
                       {'alt':'Bild 2 von 2','src':'https://i.ebayimg.com/images/g/a/s-l140.webp'}]

    def test_filename_order_cannot_reorder_gallery(self):
        result=reconcile_positions(self.observed,[{'file':'01.webp','url':'https://i.ebayimg.com/images/g/a/s-l1600.webp'},
                                                  {'file':'02.webp','url':'https://i.ebayimg.com/images/g/z/s-l1600.webp'}])
        self.assertEqual([r['file'] for r in result['assets']],['02.webp','01.webp'])
        self.assertTrue(result['all_positions_retained'])

    def test_multiple_versions_do_not_fill_a_missing_position(self):
        result=reconcile_positions(self.observed,[{'url':'https://i.ebayimg.com/images/g/z/s-l500.webp'},
                                                  {'url':'https://i.ebayimg.com/images/g/z/s-l1600.webp'}])
        self.assertEqual(result['retained_positions'],[1])
        self.assertFalse(result['all_positions_retained'])

    def test_incomplete_or_conflicting_dom_binding_fails_closed(self):
        with self.assertRaises(ValueError):reconcile_positions(self.observed[:1],[])
        with self.assertRaises(ValueError):
            reconcile_positions(self.observed+[{'alt':'Bild 2 von 2','src':self.observed[0]['src']}],[])


if __name__=='__main__':unittest.main()
