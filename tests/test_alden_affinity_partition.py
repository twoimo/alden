"""Synthetic bounded partition traversal; no source DBs, models or AX calls."""
from array import array
from collections import Counter
from dataclasses import replace
import itertools,json,math
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import alden_semantic_affinity as semantic
from alden_affinity_sampling import build_plan,cover_pairs,PARTITION,TOURNAMENT

class Guard:
    def __init__(self,stop=None):self.calls=0;self.stop=stop
    def check(self):
        self.calls+=1
        if self.calls==self.stop:raise RuntimeError('affinity_cancelled')

def loaded_nodes(n,scope=lambda i:['one']):
    result=[]
    for i in range(n):
        vector=array('f',[math.cos(i/17),math.sin(i/17)]+[0.]*382)
        result.append(({'id':f'n{i:04d}','projects':scope(i)},vector,math.sqrt(sum(x*x for x in vector))))
    return result

def scopes(loaded):
    projects=sorted({p for row in loaded for p in row[0]['projects']})
    return [[i for i,row in enumerate(loaded)if p in row[0]['projects']]
            for p in projects if sum(p in row[0]['projects']for row in loaded)>1]

class PartitionSamplingTests(unittest.TestCase):
    def plan(self,loaded,budgets=None,guard=None):
        groups=scopes(loaded)
        eligible=sum(bool(set(loaded[i][0]['projects'])&set(loaded[j][0]['projects']))
            for i in range(len(loaded))for j in range(i+1,len(loaded)))
        return build_plan(loaded,groups,eligible,budgets or semantic.Budgets(),guard or Guard(),dimension=384)

    def test_complete_and_small_budget_paths_preserve_the_existing_tournament(self):
        for n,budgets in [(12,semantic.Budgets()),(128,replace(semantic.Budgets(),max_pairs=2)),
                (128,replace(semantic.Budgets(),max_products=4000))]:
            loaded=loaded_nodes(n);plan=self.plan(loaded,budgets)
            self.assertEqual(plan.method,TOURNAMENT);self.assertEqual(plan.projection_products,0)
            actual=list(plan.pairs(semantic._project_matchings,Guard()))
            expected=list(semantic._project_matchings(scopes(loaded),n,plan.cap,Guard()))
            self.assertEqual(actual,expected)

    def test_cover_invariant_for_all_six_node_two_project_membership_patterns(self):
        cases=0
        for size in range(7):
            for assignment in itertools.product(range(4),repeat=size):
                groups=[[i for i,mask in enumerate(assignment)if mask&bit]for bit in (1,2)]
                groups=[g for g in groups if len(g)>1]
                pairs=cover_pairs(groups,size,Guard())
                eligible={i for group in groups for i in group}
                self.assertEqual({i for pair in pairs for i in pair},eligible)
                self.assertLessEqual(len(pairs),size)
                self.assertEqual(len(set(pairs)),len(pairs))
                self.assertTrue(all(i<j and assignment[i]&assignment[j] for i,j in pairs))
                cases+=1
        self.assertEqual(cases,5461)

    def test_partial_partition_is_unique_bounded_and_covers_every_eligible_node(self):
        loaded=loaded_nodes(128);budgets=replace(semantic.Budgets(),max_pairs=4096)
        before=[(json.dumps(p,sort_keys=True),v.tobytes(),norm)for p,v,norm in loaded]
        plan=self.plan(loaded,budgets);pairs=list(plan.pairs(semantic._project_matchings,Guard()))
        self.assertEqual(plan.method,PARTITION);self.assertEqual(len(pairs),plan.cap)
        self.assertEqual(len(set(pairs)),len(pairs));self.assertTrue(all(i<j for i,j in pairs))
        degree=Counter(i for pair in pairs for i in pair)
        self.assertEqual(len(degree),128);self.assertLessEqual(len(plan.cover),128)
        self.assertLessEqual(plan.projection_products+len(pairs)*384,budgets.max_products)
        self.assertEqual(before,[(json.dumps(p,sort_keys=True),v.tobytes(),norm)for p,v,norm in loaded])

    def test_overlap_and_isolated_scopes_never_gain_cross_project_pairs(self):
        def scope(i):
            if i>=240:return [f'isolated{i}']
            return ['one']if i<80 else ['one','two']if i<160 else ['two']
        loaded=loaded_nodes(256,scope);plan=self.plan(loaded,replace(semantic.Budgets(),max_pairs=4096))
        pairs=list(plan.pairs(semantic._project_matchings,Guard()));degree=Counter(i for pair in pairs for i in pair)
        self.assertEqual(plan.method,PARTITION);self.assertEqual(len(pairs),plan.cap)
        self.assertEqual(len(set(pairs)),len(pairs));self.assertEqual(set(degree),set(range(240)))
        self.assertTrue(all(set(loaded[i][0]['projects'])&set(loaded[j][0]['projects'])for i,j in pairs))

    def test_projection_reservation_reduces_dot_products_at_the_default_ceiling(self):
        plan=self.plan(loaded_nodes(512));pairs=list(plan.pairs(semantic._project_matchings,Guard()))
        self.assertEqual(plan.method,PARTITION);self.assertLess(plan.cap,65536)
        self.assertEqual(len(pairs),plan.cap)
        self.assertLessEqual(plan.projection_products+len(pairs)*384,25165824)

    def test_cancellation_interrupts_projection_before_a_plan_is_returned(self):
        with self.assertRaisesRegex(RuntimeError,'affinity_cancelled'):
            self.plan(loaded_nodes(128),replace(semantic.Budgets(),max_pairs=4096),Guard(stop=3))

    def test_actual_candidate_evaluator_remains_deterministic_under_input_permutation(self):
        loaded=loaded_nodes(128);budgets=replace(semantic.Budgets(),max_pairs=4096)
        a,coverage=semantic._candidates(loaded,budgets,.65,semantic._Guard(budgets,lambda:False))
        b,other=semantic._candidates(list(reversed(loaded)),budgets,.65,semantic._Guard(budgets,lambda:False))
        self.assertEqual(a,b);self.assertEqual(coverage,other)
        self.assertEqual(coverage['pair_sampling'],PARTITION)
        self.assertEqual(coverage['vector_nodes_compared'],128)
        self.assertEqual(coverage['total_dimension_products'],coverage['dimension_products']+coverage['projection_products'])

if __name__=='__main__':unittest.main()
