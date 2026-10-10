import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from alden_retrieval_time import select_latest,observation_note

NOW=1791500000
def record(identity,stamp,term='구독 조건',**extras):
    return {'id':identity,'name':'중립 제목 '+identity,'aliases':[term],
            'observed_at':stamp,'time_basis':'record_confirmed_at',**extras}

class ObservationSelectionTests(unittest.TestCase):
    def test_dates_select_sources_without_old_new_labels_or_identity_merges(self):
        rows=[record('z',1788102000),record('a',1790643600)]
        ids,policy=select_latest('구독 조건의 최신 기록',rows,now=NOW)
        self.assertEqual(ids,['a']);self.assertEqual(policy['excluded_older_observations'],1)
        self.assertEqual(policy['claim_validity'],'not_established_by_observation_date')
        self.assertEqual(rows[0]['id'],'z')

    def test_history_comparison_and_explicit_ranges_keep_both(self):
        rows=[record('a',1),record('b',2)]
        for query,bounds in [('이전 구독 조건과 최신 비교',{}),('구독 조건 최신',{'time_to':3})]:
            ids,policy=select_latest(query,rows,now=NOW,**bounds);self.assertEqual(ids,['a','b']);self.assertEqual(policy['mode'],'all')

    def test_missing_invalid_future_and_naive_dates_do_not_drop_unknown_records(self):
        for stamp in [None,'missing','2026-10-01T10:00:00',float('nan'),NOW+1000]:
            rows=[record('a',1),record('b',stamp,updated_at=NOW)]
            ids,policy=select_latest('구독 조건 최신',rows,now=NOW)
            self.assertEqual(ids,['a','b']);self.assertEqual(policy['undetermined_mentions'],1)

    def test_ambiguous_people_and_explicit_conflicts_keep_evidence(self):
        for extras,key in [({'identity_kind':'person'},'ambiguous_identity_mentions'),({'has_conflict':True},'conflicting_mentions')]:
            rows=[record('a',1,**extras),record('b',2,**extras)]
            ids,policy=select_latest('구독 조건 최신',rows,now=NOW);self.assertEqual(ids,['a','b']);self.assertEqual(policy[key],1)

    def test_date_only_overlap_does_not_invent_timezone_or_same_day_order(self):
        rows=[record('a','2026-09-29'),record('b','2026-09-29T13:00:00+09:00')]
        ids,_=select_latest('구독 조건 최신',rows,now=NOW);self.assertEqual(ids,['a','b'])

    def test_each_named_subject_gets_its_own_latest_source(self):
        rows=[record('fee-old',1,'요금'),record('hours-old',1,'운영시간'),record('fee',2,'요금'),record('hours',3,'운영시간')]
        ids,_=select_latest('요금과 운영시간 최신 기록',rows,now=NOW);self.assertEqual(ids,['fee','hours'])

    def test_explicitly_pinned_old_record_is_retained(self):
        ids,_=select_latest('구독 조건 최신',[record('a',1,pinned=True),record('b',2)],now=NOW)
        self.assertEqual(set(ids),{'a','b'})

    def test_unrelated_newer_source_does_not_win_named_subject_order(self):
        rows=[record('noise',3,'다른 자료'),record('old',1),record('selected',2)]
        ids,_=select_latest('구독 조건 최신',rows,now=NOW);self.assertEqual(ids,['selected','noise'])

    def test_separately_mentioned_short_term_is_not_erased_by_a_longer_term(self):
        rows=[record('city-old',1,'서울'),record('food-old',1,'서울맛집'),record('city',2,'서울'),record('food',3,'서울맛집')]
        ids,policy=select_latest('서울 최신 기록과 서울맛집 최신 기록',rows,now=NOW)
        self.assertEqual(ids,['city','food']);self.assertEqual(policy['matched_mentions'],2)

    def test_invalid_timestamp_text_is_not_echoed_into_context(self):
        note=observation_note('SECRET_ACCESS_TOKEN','source_published_at')
        self.assertNotIn('SECRET',note);self.assertIn('미확인',note)

    def test_same_publisher_label_does_not_conflate_distinct_declared_targets(self):
        rows=[record('a',1,mention_scopes={'구독 조건':['target-a']}),record('b',2,mention_scopes={'구독 조건':['target-b']})]
        ids,policy=select_latest('구독 조건 최신',rows,now=NOW)
        self.assertEqual(ids,['a','b']);self.assertEqual(policy['ambiguous_identity_mentions'],1)
