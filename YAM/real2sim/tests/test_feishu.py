import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from real2sim.feishu import Client,FeishuError,normalize

class FeishuTests(unittest.TestCase):
    def setUp(self):
        self.mapping={'material_id':'编号','name':'名称','height_mm':'高度','quantity':'数量','mass_g':'重量','photos':'照片','model_status':'状态'}
        self.schema=[{'field_name':v} for v in self.mapping.values()]
        self.row={'record_id':'rec_test','fields':{'编号':[{'text':'MAT-000001','type':'text'}],
            '名称':'纸杯','高度':80,'数量':3,'重量':8,'状态':'已验证',
            '照片':[{'file_token':'file_test','name':'cup.jpg','tmp_url':'https://temporary.invalid'}]}}
    def test_units_richtext_attachment_and_no_false_validation(self):
        rows,absent=normalize([self.row],self.mapping,self.schema)
        self.assertEqual(rows[0]['dimensions_m']['z'],.08)
        self.assertEqual(rows[0]['mass_kg'],.008)
        self.assertEqual(rows[0]['material_id'],'MAT-000001')
        self.assertNotIn('tmp_url',rows[0]['photos'][0])
        self.assertEqual(rows[0]['validation_status'],'metadata_only_not_simulation_validated')
    def test_legacy_source_uri_attachment_is_not_lost(self):
        self.mapping['source_uri']='原始素材';self.schema.append({'field_name':'原始素材'})
        self.row['fields']['原始素材']=[{'file_token':'mesh_token','name':'mortar.stl','size':84,'tmp_url':'https://temporary.invalid'}]
        rows,_=normalize([self.row],self.mapping,self.schema)
        self.assertEqual(rows[0]['source_files'][0]['name'],'mortar.stl')
        self.assertEqual(rows[0]['source_uri'],'')
        self.assertNotIn('tmp_url',rows[0]['source_files'][0])
    def test_duplicate_rejected(self):
        with self.assertRaisesRegex(ValueError,'Duplicate'):normalize([self.row,self.row],self.mapping,self.schema)
    def test_numeric_units_in_text_rejected(self):
        self.row['fields']['高度']='80 mm'
        with self.assertRaisesRegex(ValueError,'numeric column'):normalize([self.row],self.mapping,self.schema)
    def test_optional_missing_is_null_and_reported(self):
        self.mapping['width_mm']='不存在'
        rows,absent=normalize([self.row],self.mapping,self.schema)
        self.assertIsNone(rows[0]['dimensions_m']['y']);self.assertIn('width_mm',absent)
    def test_required_column_missing_rejected(self):
        with self.assertRaisesRegex(ValueError,'required'):normalize([self.row],self.mapping,[])
    def test_invalid_number_rejected(self):
        for value in (float('nan'),-1,0,True):
            self.row['fields']['高度']=value
            with self.assertRaises(ValueError):normalize([self.row],self.mapping,self.schema)
    def test_pagination_and_repeated_token_rejection(self):
        client=Client();responses=iter([{'data':{'items':[1],'has_more':True,'page_token':'next'}},{'data':{'items':[2],'has_more':False}}])
        paths=[]
        def request(path):paths.append(path);return next(responses)
        client.request=request
        self.assertEqual(client.list_all('base','table','records'),[1,2])
        self.assertIn('page_token=next',paths[1])
        client.request=lambda path:{'data':{'items':[],'has_more':True,'page_token':'same'}}
        with self.assertRaises(FeishuError):client.list_all('base','table','records')

if __name__=='__main__':unittest.main()
