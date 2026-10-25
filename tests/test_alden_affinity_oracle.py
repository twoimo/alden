"""Independent small cosine/permission oracles; no source DB or inference."""
from pathlib import Path
from dataclasses import asdict
from tempfile import TemporaryDirectory
import hashlib,json,struct,sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from evaluate_alden_affinity_neighbors import evaluate,evaluate_matrix,load_snapshot,_json
from alden_semantic_affinity import E5Profile

class AffinityOracleTests(unittest.TestCase):
    def setUp(self):
        self.nodes=[{'id':name,'projects':['one']}for name in 'abcd']
        # Unit directions0,60,90,180 degrees, with independently known cosines.
        self.matrix=[[1,.5,0,-1],[.5,1,3**.5/2,-.5],[0,3**.5/2,1,0],[-1,-.5,0,1]]
    def pair(self,a,b):
        i,j='abcd'.index(a),'abcd'.index(b)
        return {'source':a,'target':b,'cosine':self.matrix[i][j],'projects':['one']}
    def test_exact_small_neighbors_and_missing_endpoint_penalty(self):
        result=evaluate_matrix(self.nodes,self.matrix,[self.pair('a','b'),self.pair('b','c')],.4)
        self.assertEqual(result['recall_hits'],4)
        self.assertEqual(result['recall_denominator'],4)
        self.assertEqual(result['tie_aware_recall_at3'],1)
        self.assertEqual(result['incident_nodes'],3)
        self.assertEqual(result['mean_best_cosine_regret_on_incident_nodes'],0)
        missing=evaluate_matrix(self.nodes,self.matrix,[self.pair('a','b')],.4)
        self.assertEqual(missing['recall_denominator'],4)
        self.assertEqual(missing['tie_aware_recall_at3'],.5)
        self.assertGreater(missing['mean_best_cosine_regret_on_incident_nodes'],.18)
    def test_ties_do_not_depend_on_arbitrary_neighbor_id_order(self):
        nodes=[{'id':str(i),'projects':['one']}for i in range(6)]
        matrix=[[1.0]*6 for _ in nodes]
        pairs=[{'source':'0','target':str(i),'cosine':1,'projects':['one']}for i in (3,4,5)]
        result=evaluate_matrix(nodes,matrix,pairs,.65)
        self.assertEqual(result['recall_hits'],6)
        self.assertEqual(result['recall_denominator'],18)
        self.assertEqual(result['tie_aware_recall_at3'],1/3)
    def test_no_eligible_neighbor_is_null_not_nan_or_successful_recall(self):
        result=evaluate_matrix(self.nodes,self.matrix,[],.99)
        self.assertIsNone(result['tie_aware_recall_at3'])
        self.assertIsNone(result['mean_best_cosine_regret_on_incident_nodes'])
        json.dumps(result,allow_nan=False)
    def test_disjoint_projects_do_not_enter_denominator_or_candidates(self):
        self.nodes[2]['projects']=['two'];self.nodes[3]['projects']=['two']
        result=evaluate_matrix(self.nodes,self.matrix,[self.pair('a','b')],.4)
        self.assertEqual(result['recall_denominator'],2)
        with self.assertRaisesRegex(ValueError,'oracle_candidate_scope_or_duplicate'):
            evaluate_matrix(self.nodes,self.matrix,[self.pair('b','c')],.4)
    def test_symmetric_duplicates_self_pairs_bad_scores_and_foreign_nodes_are_rejected(self):
        valid=self.pair('a','b')
        for rows in [[valid,self.pair('b','a')],[self.pair('a','a')],
                [{**valid,'cosine':.6}],[{**valid,'target':'unknown'}],
                [{**valid,'cosine':float('nan')}]]:
            with self.subTest(rows=rows),self.assertRaises(ValueError):
                evaluate_matrix(self.nodes,self.matrix,rows,.4)
    def test_duplicate_keys_and_nonfinite_json_are_rejected(self):
        for value in ['{"x":1,"x":2}','{"x":NaN}']:
            with self.subTest(value=value),self.assertRaises(ValueError):_json(value)
        with self.assertRaisesRegex(ValueError,'oracle_candidate_declaration_invalid'):
            evaluate(Path('/not-read'),['variant=unused']*9)

    def test_snapshot_bytes_norm_offsets_and_profile_are_verified(self):
        with TemporaryDirectory()as temp:
            folder=Path(temp).resolve();raw=struct.pack('<384f',1,*([0]*383))
            (folder/'vectors.f32le').write_bytes(raw)
            value={'schema':1,'profile':asdict(E5Profile()),'threshold':.65,
                'vectors':{'file':'vectors.f32le','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'encoding':'little-endian float32'},
                'nodes':[{'id':'fixture','projects':['one'],'offset':0,'bytes':len(raw),'norm':1.,'vector_sha256':hashlib.sha256(raw).hexdigest()}]}
            path=folder/'snapshot.json';path.write_text(json.dumps(value))
            self.assertEqual(len(load_snapshot(path)[1][0]),384)
            for changed in [lambda v:v.update(schema=True),lambda v:v['nodes'][0].update(offset=False),
                    lambda v:v['nodes'][0].update(norm=.5),lambda v:v['profile'].update(dimension=383),
                    lambda v:v['vectors'].update(file='../other')]:
                candidate=json.loads(json.dumps(value));changed(candidate);path.write_text(json.dumps(candidate))
                with self.subTest(candidate=candidate),self.assertRaises(ValueError):load_snapshot(path)
            path.write_text(json.dumps(value));(folder/'vectors.f32le').write_bytes(bytes(len(raw)))
            with self.assertRaisesRegex(ValueError,'oracle_vector_binding_invalid'):load_snapshot(path)

if __name__=='__main__':unittest.main()
