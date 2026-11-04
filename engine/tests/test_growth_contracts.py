"""Local draft regressions: standard library helpers, no model or network calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import yaml
from bookengine.contracts import (dashboard_guard, power_merge, valid_extract, observations,
                       reduce_growth, payoff_ok, growth_warnings, choose_contract)
HOOKS = ['隐瞒', '危机', '选择', '旧事反转', '关系变化']
MENU = ['升级', '打脸', '收获', '身份抬升', '碾压']
PACK = {'ladder': {'strength': ['凡人', '入门', '通脉', '凝元', '化境', '宗师'],
                   'wealth_unit': '灵石', 'status': ['无名', '同门', '执事']},
        'genre_kind': '爽文', 'payoff_menu': MENU, 'hook_types': HOOKS,
        'solution_menu': ['抢先夺物', '诱敌离位', '切断退路']}
DATA = {'packs': {'xuanhuan-power-fantasy': PACK}}

class ContractsTests(unittest.TestCase):
    def test_malformed_extraction_keeps_finished_new_fields_but_not_incomplete_ledgers(self):
        from bookengine.longutil import loose_object
        fields=dict(summary='拿回旧物。',state={},type='收获',
            payoff={'type':'收获','fulfilled':True,'quote':'她拿回旧物。'},
            gains=[{'type':'收获','content':'取回旧物','quote':'她拿回旧物。'}],
            costs=[{'type':'代价','content':'用掉绳索','quote':'绳索断了。'}],
            solutions=[{'type':'实力压制','content':'当场制住对手','quote':'她按住对手。'}],
            demotion_event={'quote':'他失去了一级修为。'},structure='潜入')
        # A malformed unrelated container forces field-level recovery, while
        # every extraction field below is still a complete JSON literal.
        broken='{"broken":[BROKEN},'+json.dumps(fields,ensure_ascii=False)[1:]
        recovered=loose_object(broken)
        for key,value in fields.items():
            with self.subTest(field=key):self.assertEqual(recovered.get(key),value)
        self.assertTrue(recovered.recovered_output)
        partial={k:v for k,v in fields.items() if k!='gains'}
        truncated='{"broken":[BROKEN},'+json.dumps(partial,ensure_ascii=False)[1:-1]+',"gains":[{"type":"收获","content":"未完成'
        recovered=loose_object(truncated)
        self.assertNotIn('gains',recovered)
        for key,value in partial.items():self.assertEqual(recovered.get(key),value)

    def test_extract_first_request_contains_all_six_actual_types_and_original_quote(self):
        from bookengine import q1
        text='她拿回了自己的灵石。'
        nested=dict(gains=[dict(type='收获',content='取回自己的灵石',quote=text)],costs=[],solutions=[],advances=[])
        reply=dict(type='收获',hook_type='危机',state=deepcopy(nested),
                   payoff=dict(type='收获',fulfilled=True,quote=text))
        class CaptureStage:
            prefix=''
            def __init__(self): self.calls=[]
            def stage(self,name,prompt,schema=None,**kwargs):
                self.calls.append((name,prompt,schema,kwargs))
                return deepcopy(reply)
        with tempfile.TemporaryDirectory() as directory:
            client=CaptureStage()
            client.out=Path(directory)
            result,failed,retries=q1.extract_state(client,'type-input-regression',text,{},
                {'canon':deepcopy(PACK)},{'number':1,'payoff_due':True,'payoff_type':'收获'},[])
        self.assertEqual((failed,retries),(False,0))
        for key,value in {**reply,**nested}.items():
            with self.subTest(field=key):self.assertEqual(result[key],value)
        self.assertEqual(q1.observations(result,text,1)['gains'],[dict(chapter=1,type='收获',content='取回自己的灵石')])
        self.assertEqual(len(client.calls),1)
        prompt=client.calls[0][1]
        legal=json.loads(prompt.split('## 合法值\n',1)[1].split('\n\n## ',1)[0])
        self.assertEqual(legal.get('types'),['对抗','谋划','情感','日常','揭秘','收获'])
        self.assertEqual(legal['hook_types'],HOOKS)
        self.assertIn(text,prompt)
        self.assertEqual(result['payoff']['quote'],text)
        self.assertNotIn('recovered_previous',json.loads(client.calls[0][2]))
        self.assertEqual(client.calls[0][3]['effort'],'low')

    def test_power_realm_tracks_guarded_dashboard_without_removing_injury_or_blocking_real_demotion(self):
        previous=dict(dashboard={'strength':'通脉'},power_status={'境界':'通脉','限制':'左臂不能发力'})
        raw=dict(state={'dashboard':{'strength':'入门'},'power_status':{'境界':{'值':'入门','最后更新':3}}})
        result,warnings=reduce_growth(previous,raw,{'canon':PACK},'他按住伤口。',3)
        self.assertEqual(result['dashboard']['strength'],'通脉')
        self.assertEqual(result['power_status']['境界']['值'],'通脉')
        self.assertEqual(result['power_status']['限制']['值'],'左臂不能发力')
        self.assertIn('unsupported_demotion',warnings)
        self.assertIn('power_realm_synced_to_dashboard',warnings)
        text='他从通脉跌回入门。';raw['demotion_event']={'quote':text}
        result,warnings=reduce_growth(previous,raw,{'canon':PACK},text,3)
        self.assertEqual(result['dashboard']['strength'],'入门')
        self.assertEqual(result['power_status']['境界']['值'],'入门')
        self.assertFalse(warnings)

    def test_deep_ledger_lift_is_not_a_substitute_for_missing_chapter_classification(self):
        from bookengine.q1 import normalize_extract
        text='她拿回了自己的灵石。'
        ledger=dict(gains=[dict(type='收获',content='取回灵石',quote=text)],costs=[],solutions=[])
        raw=dict(state=dict(power_status={'当前状态':{'值':dict(ledger,症状='仍然手痛'),'最后更新':1}}),
                 payoff=dict(type='收获',fulfilled=True,quote=text),hook_type='危机')
        before=deepcopy(raw);result=normalize_extract(raw)
        self.assertNotIn('type',result)
        self.assertEqual(raw,before)
        self.assertEqual(result['gains'],ledger['gains'])
        self.assertEqual(observations(result,text,1)['gains'],[dict(chapter=1,type='收获',content='取回灵石')])
        power=power_merge({'代价':'旧伤'},raw['state']['power_status'],2)
        self.assertEqual(power['代价']['值'],'旧伤')
        self.assertEqual(power['当前状态']['值'],{'症状':'仍然手痛'})
        self.assertEqual(power_merge({}, {'costs':[{'value':'旧伤'}]},2)['代价']['值'],[{'值':'旧伤'}])

    def test_canon_strength_requires_exact_level_and_real_demotion_quote_and_handles_null_patch(self):
        canon = deepcopy(PACK)
        old = {'strength': '通脉', 'wealth': '3灵石', 'unknown': 'keep'}
        text = '毒针封住经脉，他从通脉跌回入门。'
        for value in ('普通', '入门', '通脉境', '通脉（受伤）', {'realm': '凝元'}):
            result, warnings = dashboard_guard(old, {'strength': value}, canon, text)
            self.assertEqual(result['strength'], '通脉'); self.assertTrue(warnings)
            self.assertNotIn('unknown',result)
        for patch in (None, [], {'strength': None}):
            self.assertEqual(dashboard_guard(old, patch, canon, text), ({k:v for k,v in old.items() if k!='unknown'}, []))
        self.assertEqual(dashboard_guard(None, None, canon, text), ({}, []))
        result, warnings = dashboard_guard(old, {'strength': '入门'}, canon, text, {'quote': '他从通脉跌回入门。'})
        self.assertEqual(result['strength'], '入门'); self.assertFalse(warnings)
        self.assertEqual(dashboard_guard(old, {'strength': '入门'}, canon, text, {'quote': '编造失阶'})[0]['strength'], '通脉')
        self.assertEqual(dashboard_guard(old, {'strength': '凝元'}, canon, text)[0]['strength'], '凝元')
        self.assertEqual(old, {'strength': '通脉', 'wealth': '3灵石', 'unknown': 'keep'})

    def test_power_six_chinese_fields_unknown_english_names_are_values_and_lru_uses_update_time(self):
        old = {'cost': {'value': '折寿', 'last_updated': 1}, 'limit': '每次一击',
               'blocked': ['剑意'], 'balances': {'life': 2}, 'status': '行动受限', 'realm': '通脉'}
        snap = deepcopy(old)
        result = power_merge(old, {'代价': '新代价', 'foreign_rule': {'unknown_domain': 7}}, 8)
        self.assertEqual(set(result), {'代价', '限制', '封禁', '余额', '当前状态', '境界'})
        self.assertEqual(result['境界']['值'], '通脉')
        self.assertEqual(result['余额']['值'], {'寿命': 2})
        self.assertEqual(result['代价'], {'值': '新代价', '最后更新': 8})
        self.assertEqual(result['当前状态']['值']['状态'], '行动受限')
        self.assertEqual(result['当前状态']['值']['扩展'][0]['名称'], 'foreign_rule')
        self.assertEqual(result['当前状态']['值']['扩展'][0]['值']['扩展'][0], {'名称': 'unknown_domain', '值': 7})
        def assert_chinese_keys(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    self.assertFalse(any(c.isascii() and c.isalpha() for c in key), key)
                    assert_chinese_keys(item)
            elif isinstance(value, list):
                for item in value: assert_chinese_keys(item)
        assert_chinese_keys(result)
        self.assertEqual(old, snap)
        self.assertEqual(power_merge(result, {'境界': {'值': None, '最后更新': None}}, 9), result)
        domains = [dict(名称='旧域', 值='old', 最后更新=1), dict(名称='常用域', 值='hot', 最后更新=7),
                   dict(名称='次旧域', 值='warm', 最后更新=3)]
        result = power_merge({'当前状态': {'值': {'扩展': domains}, '最后更新': 7}}, {'新域': 'new'}, 9, limit=3)
        self.assertEqual({x['名称'] for x in result['当前状态']['值']['扩展']}, {'常用域', '次旧域', '新域'})
        self.assertEqual(next(x for x in result['当前状态']['值']['扩展'] if x['名称']=='常用域')['最后更新'], 7)

    def test_extract_never_fills_plan_values_and_false_needs_no_negative_evidence(self):
        text = '他抢回灵石，逼退了守门人。'
        raw = dict(type='对抗', hook_type='危机', state={}, payoff=dict(type='收获', fulfilled=True, quote='他抢回灵石'))
        snap = deepcopy(raw)
        self.assertTrue(valid_extract(raw, text, HOOKS, MENU)); self.assertEqual(raw, snap)
        for bad in ({}, {k:v for k,v in raw.items() if k!='state'}, dict(raw,state=None), dict(raw,state=[]), dict(raw,state='partial'), dict(raw, type=''), dict(raw, hook_type='计划钩子'),
                    dict(raw, payoff=dict(raw['payoff'], fulfilled='true')),
                    dict(raw, payoff=dict(raw['payoff'], fulfilled=1)),
                    dict(raw, payoff=dict(raw['payoff'], quote='')),
                    dict(raw, payoff=dict(raw['payoff'], quote='没有发生的句子')),
                    dict(raw, payoff=dict(raw['payoff'], type='收获子型'))):
            with self.subTest(bad=bad): self.assertFalse(valid_extract(bad, text, HOOKS, MENU))
        for quote in ('', None, '他抢回灵石'):
            value = dict(raw, payoff=dict(type='无兑现', fulfilled=False, quote=quote))
            self.assertTrue(valid_extract(value, text, HOOKS, MENU))
            self.assertFalse(payoff_ok(value['payoff'], '无兑现', text, MENU))
        for quote in ('',None,text):
            self.assertFalse(valid_extract(dict(raw,payoff=dict(type='升级',fulfilled=False,quote=quote)),text,HOOKS,MENU))
        self.assertFalse(valid_extract(dict(raw, payoff=dict(type='无兑现', fulfilled=True, quote=text)), text, HOOKS, MENU))
        self.assertFalse(valid_extract(dict(raw, payoff=dict(type='无兑现', fulfilled=False, quote='伪造证据')), text, HOOKS, MENU))
        self.assertTrue(payoff_ok(raw['payoff'], '收获', text, MENU))
        self.assertFalse(payoff_ok(raw['payoff'], '升级', text, MENU))

    def test_partial_power_update_keeps_unmentioned_action_limits_and_balance_units(self):
        old={'当前状态':{'值':{'行动限制':{'右臂':'不可用','左腿':'活动受限'},'记忆':'缺失旧片段'},'最后更新':3},
             '封禁':{'值':{'剑意':'封禁'},'最后更新':2},
             '余额':{'值':{'寿命':{'数量':2,'单位':'天'},'灵石':{'数量':3,'单位':'枚'}},'最后更新':3}}
        patch={'当前状态':{'值':{'行动限制':{'左腿':'活动正常'}},'最后更新':4},
               '余额':{'值':{'寿命':{'数量':1}},'最后更新':4}}
        before=deepcopy((old,patch))
        result=power_merge(old,patch,4)
        self.assertEqual(result['当前状态']['值'],{'行动限制':{'右臂':'不可用','左腿':'活动正常'},'记忆':'缺失旧片段'})
        self.assertEqual(result['封禁'],old['封禁'])
        self.assertEqual(result['余额']['值']['寿命'],{'数量':1,'单位':'天'})
        self.assertEqual(result['余额']['值']['灵石'],old['余额']['值']['灵石'])
        self.assertEqual(result['当前状态']['最后更新'],4)
        self.assertEqual((old,patch),before)

    def test_data_aliases_record_joint_naming_as_same_canonical_solution(self):
        data=yaml.safe_load((Path(__file__).resolve().parents[1]/'data/growth_payoff.yaml').read_text())
        canon=dict(deepcopy(data['packs']['suspense-mystery']),solution_aliases=data['solution_aliases'])
        self.assertEqual(canon['solution_aliases']['共同喊名'],'群体表态')
        self.assertNotIn('拆册共管',canon['solution_aliases'])
        self.assertIn('群体表态',canon['solution_menu'])
        self.assertNotIn('集体认同',canon['solution_menu'])
        old={'solutions':[dict(chapter=1,type='集体认同',content='曾共同见证身份')]}
        text='众人共同喊出了真名。'
        raw={'state':{},'solutions':[dict(type='共同喊名',content='当场共同确认身份',quote=text)]}
        before=deepcopy((old,raw,canon))
        state,warnings=reduce_growth(old,raw,{'canon':canon},text,20)
        self.assertEqual(state['solutions'][-1],dict(chapter=20,type='群体表态',content='当场共同确认身份'))
        self.assertNotIn('solution_repeat_30',warnings)
        self.assertNotIn('quote',state['solutions'][-1])
        self.assertEqual((old,raw,canon),before)

    def test_legacy_recognition_and_shared_actions_are_not_oral_group_proxies(self):
        data=yaml.safe_load((Path(__file__).resolve().parents[1]/'data/growth_payoff.yaml').read_text())
        canon=dict(data['packs']['suspense-mystery'],solution_aliases=data['solution_aliases'],final_volume_start=50)
        old={'solutions':[dict(chapter=1,type='集体认同',content='旧记录机理未审'),
                          dict(chapter=50,type='拆册共管',content='旧册分处保管') ]}
        before=deepcopy(old)
        plan=choose_contract(canon,[],90,state=old)
        self.assertTrue(plan['group_allowed']);self.assertNotIn('群体表态',plan['solution_avoid'])
        self.assertEqual(plan['group_history_unknown'],1)
        self.assertIn('未审',data['solution_mechanisms']['集体认同'])
        for name in ('拆册','拆册共管','分印共管','共同见证'):
            self.assertNotEqual(canon['solution_aliases'].get(name),'群体表态')
        text='众人齐声拒绝代签。她将照明储备改供救援设备。'
        raw=dict(state={},solutions=[dict(type='众人作证',content='齐声拒绝解除代签控制',quote='众人齐声拒绝代签。')])
        state,warnings=reduce_growth(old,raw,{'canon':canon},text,90)
        self.assertEqual(state['solutions'][:2],old['solutions']);self.assertEqual(old,before)
        self.assertEqual(state['solutions'][-1]['type'],'群体表态')
        self.assertNotIn('group_solution_book_cap',warnings);self.assertNotIn('group_solution_final_cap',warnings)
        action=dict(state={},solutions=[dict(type='资源重配',content='将照明储备改供救援设备',quote='她将照明储备改供救援设备。')])
        state,warnings=reduce_growth(old,action,{'canon':canon},text,90)
        self.assertEqual(state['solutions'][-1]['type'],'资源重配')
        self.assertNotIn('group_solution_final_cap',warnings)
        self.assertTrue(choose_contract(canon,[],91,state=state)['group_allowed'])

    def test_legacy_unknown_conservatively_reserves_group_budget_without_rewriting_bible(self):
        data=yaml.safe_load((Path(__file__).resolve().parents[1]/'data/growth_payoff.yaml').read_text())
        bible=dict(canon=dict(data['packs']['suspense-mystery'],solution_menu=['集体认同','资源重配']))
        canon=dict(bible['canon'],solution_menu=data['packs']['suspense-mystery']['solution_menu'],
                   solution_aliases=data['solution_aliases'],final_volume_start=50)
        legacy={'solutions':[dict(chapter=n,type='集体认同',content='多人共同认可，主机理待核') for n in (1,50)]}
        before=deepcopy((bible,legacy,canon))
        result=choose_contract(canon,[],90,state=legacy)
        self.assertEqual(result['group_history_unknown'],2);self.assertFalse(result['group_allowed'])
        self.assertIn('群体表态',result['solution_avoid'])
        self.assertEqual((bible,legacy,canon),before)
        text='众人齐声声明拒绝代签。'
        raw=dict(state={},solutions=[dict(type='众人作证',content='共同拒绝使代签失效',quote=text)])
        state,warnings=reduce_growth(legacy,raw,{'canon':canon},text,90)
        self.assertEqual(state['solutions'][:2],legacy['solutions'])
        self.assertIn('group_solution_book_cap',warnings);self.assertIn('group_solution_final_cap',warnings)
        for content in ('众人齐声喊名解除控制','众人作证使伪证失效','众人共同拒绝代签'):
            records={'solutions':[dict(chapter=1,type='集体认同',content=content),legacy['solutions'][1]]}
            plan=choose_contract(canon,[],90,state=records)
            self.assertEqual(plan['group_history_unknown'],1);self.assertFalse(plan['group_allowed'])
            normalized,_=reduce_growth(records,dict(state={}),{'canon':canon},'',90)
            self.assertEqual(normalized['solutions'][0]['type'],'群体表态')
            self.assertEqual(normalized['solutions'][0]['classification_basis'],'legacy_explicit_group_content')
            self.assertEqual(normalized['solution_history'][0],records['solutions'][0])
        for rows in ([],[dict(number=n,solutions=[dict(chapter=n,type='资源重配',content='重配物资')]) for n in range(1,21)]):
            self.assertEqual(choose_contract(canon,rows,21)['group_history_unknown'],0)

    def test_unresolved_levels_do_not_create_upgrade_obligations(self):
        from bookengine.contracts import growth_obligations
        for level in (None,'旧未知等级'):
            old=dict(dashboard={'strength':level},realm_progress={'level':'通脉','upgrade_at':2,'displayed_at':None})
            before=deepcopy(old)
            state,_=reduce_growth(old,dict(state={}),{'canon':PACK},'她仍未确认境界。',5)
            self.assertEqual(state['dashboard']['strength'],level);self.assertEqual(old,before)
            self.assertIsNone(state['realm_progress']['upgrade_at'])
            self.assertFalse(growth_obligations(state,6,PACK)['display_required'])

    def test_observations_plural_solutions_and_growth_merge_do_not_store_quotes_or_duplicate(self):
        text = '他抢回灵石。她诱敌离位。'
        raw = dict(state={'dashboard': {'strength': '通脉'}, 'power_status': {'realm': '通脉'}},
            gains=[dict(type='收获', content='取回灵石', quote='他抢回灵石。')],
            costs=[dict(content='已经付出', quote='他抢回灵石。'), dict(content='虚构付出', quote='未发生')],
            solutions=[dict(category='诱敌离位', quote='她诱敌离位。')])
        old = dict(custom={'unknown': True}, dashboard={'strength': '入门'},
                   solutions=[dict(chapter=1, type='诱敌离位', content='旧解法')])
        snap = deepcopy((old, raw))
        literal = observations(raw, text, 30)
        self.assertEqual(literal['costs'], [dict(chapter=30, type='代价', content='已经付出')])
        self.assertEqual(literal['solutions'], [dict(chapter=30, type='诱敌离位', content='诱敌离位')])
        self.assertNotIn('quote', json.dumps(literal))
        state, warnings = reduce_growth(old, raw, {'canon': PACK}, text, 30)
        self.assertNotIn('solution_repeat_30', warnings)
        self.assertEqual(state['dashboard']['strength'], '通脉')
        self.assertEqual(state['power_status']['境界']['值'], '通脉')
        self.assertEqual(state['custom'], old['custom'])
        self.assertEqual(reduce_growth(state, raw, {'canon': PACK}, text, 30)[0], state)
        self.assertNotIn('solution_repeat_30', reduce_growth(old, raw, {'canon': PACK}, text, 31)[1])
        alias_raw = dict(raw,solutions=[dict(category='诱开守卫',quote='她诱敌离位。')])
        alias_canon = dict(PACK,solution_aliases={'诱开守卫':'诱敌离位'})
        alias_state, alias_warn = reduce_growth(old,alias_raw,{'canon':alias_canon},text,30)
        self.assertEqual(alias_state['solutions'][-1]['type'],'诱敌离位')
        self.assertNotIn('solution_repeat_30',alias_warn)
        self.assertEqual((old, raw), snap)
        self.assertEqual(observations({'solutions': None}, text, 30).get('solutions', []), [])
        self.assertIsInstance(reduce_growth(None, None, {'canon': PACK}, text, 1)[0], dict)

    def test_growth_warnings_use_real_volume_ledger_and_ordered_dashboard(self):
        before = {'strength': '入门', 'wealth': '1灵石', 'status': '无名'}
        state = {'gains': [dict(chapter=2), dict(chapter=25)], 'costs': [dict(chapter=3)] * 3}
        self.assertEqual(growth_warnings(state, before, dict(before, strength='通脉'), PACK, 1, 25), [])
        state['costs'].append(dict(chapter=20))
        warnings = growth_warnings(state, before, before, PACK, 1, 25)
        self.assertIn('dashboard_no_growth', warnings); self.assertIn('cost_gain_ratio', warnings)
        state['gains'] = [dict(chapter=2), dict(chapter=26)]
        self.assertIn('gains_below_two', growth_warnings(state, before, before, PACK, 1, 25))
        self.assertEqual(growth_warnings(state, before, before, dict(PACK, genre_kind='悬疑'), 1, 25), [])

    def test_daily_payoff_pending_and_suspense_frequency(self):
        rows = [dict(number=n,hook_type=HOOKS[(n-1)%5],payoff=dict(type=MENU[(n-1)%5],fulfilled=True)) for n in range(1,41)]
        plan=choose_contract(PACK,rows,41)
        self.assertTrue(plan['payoff_due']);self.assertIn(plan['payoff_type'],MENU)
        self.assertLessEqual(sum(r['hook_type']==plan['hook_type'] for r in rows[-29:])+1,30*.25)
        self.assertTrue(choose_contract(PACK,rows+[plan],42)['payoff_due'])
        pending=choose_contract(PACK,rows,41,'升级')
        self.assertEqual(pending['payoff_type'],'升级')
        self.assertNotIn('pending_contract_blocked',pending['warnings'])
        repeated=[dict(number=n,payoff=dict(type='升级',fulfilled=True)) for n in (1,2)]
        blocked=choose_contract(PACK,repeated,3,'升级')
        self.assertTrue(blocked['payoff_due']);self.assertNotEqual(blocked['payoff_type'],'升级')
        self.assertIn('pending_contract_blocked',blocked['warnings'])
        self.assertEqual(choose_contract(PACK,[dict(solution_type='抢先夺物')],3)['solution_type'],'抢先夺物')
        suspense=dict(PACK,genre_kind='悬疑')
        self.assertFalse(choose_contract(suspense,[],2)['payoff_due'])
        self.assertTrue(choose_contract(suspense,[],1)['payoff_due'])
        self.assertNotEqual(choose_contract(PACK,[dict(hook_type='危机')]*3,4)['hook_type'],'危机')
        with self.assertRaises(ValueError):choose_contract(dict(PACK,payoff_menu=['升级之子型']),[],1)

    def test_jump_rejected_without_manufacturing_intermediate_upgrade(self):
        old={'strength':'入门'};before=deepcopy(old)
        result,warnings=dashboard_guard(old,{'strength':'化境'},PACK,'他自称化境。')
        self.assertEqual(result,old);self.assertEqual(old,before)
        self.assertIn('unsupported_upgrade',warnings)
        state,warnings=reduce_growth(dict(dashboard=old,power_status={'限制':'右臂不可用'}),
            dict(state={'dashboard':{'strength':'化境'},'power_status':{'境界':'化境'}}),{'canon':PACK},'他自称化境。',7)
        self.assertEqual(state['dashboard'],old);self.assertEqual(state['power_status']['境界']['值'],'入门')
        self.assertEqual(state['power_status']['限制']['值'],'右臂不可用')

    def test_realm_obligation_only_literal_true_and_body_quote_discharge_it(self):
        from bookengine.contracts import growth_obligations
        old={'dashboard':{'strength':'入门'}};text='他踏入通脉，隔空震碎石门。'
        raw={'state':{'dashboard':{'strength':'通脉'}}}
        state,_=reduce_growth(old,raw,{'canon':PACK},text,10)
        before=deepcopy(state);obligation=growth_obligations(state,11,PACK)
        self.assertEqual(state,before)
        self.assertEqual({k:obligation[k] for k in ('level','upgrade_at','display_by','display_required')},
                         dict(level='通脉',upgrade_at=10,display_by=13,display_required=True))
        plan=choose_contract(PACK,[],11,pending='升级',state=state)
        self.assertEqual(plan['growth_obligation'],obligation)
        self.assertEqual(plan['payoff_type'],'升级')
        self.assertNotIn('pending_contract_blocked',plan['warnings'])
        self.assertTrue(obligation['display_before_upgrade'])
        self.assertIn('先展示当前阶',obligation['required_action'])
        for displayed,quote in ((True,'不存在'),('true',text),(1,text),(False,text)):
            value,_=reduce_growth(state,dict(state={},realm_displayed=displayed,realm_display_quote=quote),{'canon':PACK},text,11)
            self.assertTrue(growth_obligations(value,14,PACK)['display_required'])
        shown,_=reduce_growth(state,dict(state={},realm_displayed=True,realm_display_quote='隔空震碎石门'),{'canon':PACK},text,12)
        self.assertFalse(growth_obligations(shown,13,PACK)['display_required'])
        self.assertFalse(growth_obligations(shown,13,PACK)['display_before_upgrade'])
        self.assertEqual(shown['realm_progress']['displayed_at'],12)
        again,_=reduce_growth(shown,dict(state={}),{'canon':PACK},text,13)
        self.assertEqual(again['realm_progress'],shown['realm_progress'])
        self.assertEqual(reduce_growth(old,raw,{'canon':PACK},text,10)[0],state)
        next_level,_=reduce_growth(shown,dict(state={'dashboard':{'strength':'凝元'}}),{'canon':PACK},text,13)
        self.assertEqual(growth_obligations(next_level,14,PACK)['display_by'],16)
        self.assertTrue(growth_obligations(next_level,14,PACK)['display_required'])

    def test_solution_windows_primary_history_and_group_lifetime_caps(self):
        data=yaml.safe_load((Path(__file__).resolve().parents[1]/'data/growth_payoff.yaml').read_text())
        canon=dict(data['packs']['suspense-mystery'],solution_aliases=data['solution_aliases'])
        old={'solutions':[dict(chapter=1,type='实力压制',content='正面制服'),
                          dict(chapter=1,type='信息欺骗',content='诱敌造成错误判断',primary=True),
                          dict(chapter=10,type='信息欺骗',content='再度用诱饵使守卫离开')]}
        before=deepcopy(old);text='她用假信引开了守卫。'
        raw={'state':{},'solutions':[dict(type='信息欺骗',content='以假信使守卫离开',quote=text),
                                    dict(type='实力压制',content='制服',quote=text)]}
        state,warnings=reduce_growth(old,raw,{'canon':canon},text,20)
        self.assertEqual(old,before);self.assertEqual(len(state['solutions']),3)
        self.assertEqual(state['solutions'][0]['type'],'信息欺骗')
        self.assertTrue(state['solutions'][0]['primary_basis'])
        self.assertEqual(state['solution_history'][:2],old['solutions'][:2])
        self.assertEqual(len(state['solution_history']),5)
        self.assertIn('solution_repeat_30',warnings)
        self.assertEqual(state['solutions'][-1]['type'],'信息欺骗')
        recent={'solutions':[dict(chapter=19,type='信息欺骗',content='假信') ]}
        self.assertIn('solution_repeat_5',reduce_growth(recent,raw,{'canon':canon},text,20)[1])
        def planned(chapters,idx):
            rows=[dict(number=n,solutions=[dict(chapter=n,type='信息欺骗',content='假信使对方离开')]) for n in chapters]
            return choose_contract(canon,rows,idx)
        self.assertNotIn('信息欺骗',planned([1],20)['solution_avoid'])
        self.assertIn('信息欺骗',planned([1,10],20)['solution_avoid'])
        self.assertNotIn('信息欺骗',planned([1,10],31)['solution_avoid'])
        self.assertIn('信息欺骗',planned([10],14)['solution_avoid'])
        self.assertNotIn('信息欺骗',planned([10],15)['solution_avoid'])
        groups=[dict(number=n,solutions=[dict(chapter=n,type='共同喊名',content='齐声')]) for n in (1,50)]
        contract=choose_contract(canon,groups,90,final_volume_start=76)
        self.assertFalse(contract['group_allowed']);self.assertIn('群体表态',contract['solution_avoid'])
        contract=choose_contract(canon,groups[1:],90,final_volume_start=50)
        self.assertFalse(contract['group_allowed'])
        self.assertTrue(choose_contract(canon,groups[:1],50,final_volume_start=50)['group_allowed'])
        self.assertEqual(canon['solution_aliases']['众人作证'],'群体表态')
        current=dict(state={},solutions=[dict(type='众人作证',content='共同作证解除控制',quote=text)])
        group_state={'solutions':[x for row in groups for x in row['solutions']]}
        self.assertIn('group_solution_book_cap',reduce_growth(group_state,current,{'canon':canon},text,90)[1])
        last_canon=dict(canon,final_volume_start=50)
        self.assertIn('group_solution_final_cap',reduce_growth(group_state,current,{'canon':last_canon},text,90)[1])

    def test_one_primary_new_injury_preserves_extra_body_facts_and_old_limits(self):
        text='他右臂骨折，左眼失明，付出三天寿命。'
        old={'power_status':{'限制':'旧伤不能奔跑'}}
        raw=dict(state={'power_status':{'当前状态':{'右臂':'骨折','左眼':'失明'}}},
                 injuries=[dict(content='右臂骨折',quote='右臂骨折'),dict(content='左眼失明',quote='左眼失明')],
                 costs=[dict(type='身体代价',content='付出三天寿命',quote='付出三天寿命')])
        before=deepcopy((old,raw));state,warnings=reduce_growth(old,raw,{'canon':PACK},text,3)
        self.assertEqual(state['injuries_count'],3);self.assertEqual(len(state['injuries']),1)
        self.assertEqual(state['injuries'][0]['content'],'右臂骨折')
        self.assertIn('injuries_over_one',warnings)
        self.assertEqual(state['power_status']['限制']['值'],'旧伤不能奔跑')
        self.assertEqual(state['power_status']['当前状态']['值'],{'右臂':'骨折','左眼':'失明'})
        self.assertEqual([x['content'] for x in state['injury_facts']],['右臂骨折','左眼失明','付出三天寿命'])
        self.assertEqual((old,raw),before)
        self.assertEqual(reduce_growth(state,raw,{'canon':PACK},text,3)[0],state)
        self.assertEqual(reduce_growth(old,dict(state={},injuries=[dict(content='新伤',quote='未出现')]),{'canon':PACK},text,3)[0]['injuries_count'],0)
        same=dict(state={},injuries=[dict(content='右臂骨折',quote=text)],costs=[dict(type='身体代价',content='右臂折断的代价',quote=text)])
        self.assertEqual(reduce_growth(old,same,{'canon':PACK},text,3)[0]['injuries_count'],1)
        same['injuries'].append(dict(content='左眼失明',quote=text))
        self.assertEqual(reduce_growth(old,same,{'canon':PACK},text,3)[0]['injuries_count'],2)

    def test_old_injury_markers_exclude_only_new_counts_and_preserve_raw_and_mixed_new_injuries(self):
        text='左肩旧伤裂开，旧伤未愈又添右腕骨折，旧伤旁新添刀口。'
        old={'power_status':{'限制':'左肩旧伤仍影响发力'}}
        for marker in ({'type':'旧伤复发'},{'type':'旧伤加重'},{'type':'身体伤残','new':False}):
            with self.subTest(marker=marker):
                recurring=dict(marker,content='左肩旧伤裂开',quote='左肩旧伤裂开')
                raw=dict(state={},injuries=[recurring],costs=[dict(recurring,physical=True)])
                before=deepcopy((old,raw))
                state,warnings=reduce_growth(old,raw,{'canon':PACK},text,8)
                self.assertEqual(state['injuries_count'],0);self.assertEqual(state['injuries'],[])
                self.assertNotIn('injuries_over_one',warnings)
                self.assertEqual(state['power_status']['限制']['值'],'左肩旧伤仍影响发力')
                self.assertEqual((old,raw),before)
                raw['injuries']+=[dict(type='新伤',new=True,content='旧伤未愈又添右腕骨折',quote='旧伤未愈又添右腕骨折')]
                raw['costs']+=[dict(type='身体代价',content='旧伤旁新添刀口',quote='旧伤旁新添刀口')]
                before=deepcopy(raw);mixed,warnings=reduce_growth(old,raw,{'canon':PACK},text,8)
                self.assertEqual(mixed['injuries_count'],2);self.assertEqual(len(mixed['injuries']),1)
                self.assertEqual([x['content'] for x in mixed['injury_facts']],['旧伤未愈又添右腕骨折','旧伤旁新添刀口'])
                self.assertIn('injuries_over_one',warnings);self.assertEqual(raw,before)

class SchedulingTests(unittest.TestCase):
    def canon(self, seed):
        data = yaml.safe_load((Path(__file__).resolve().parents[1]/'data/growth_payoff.yaml').read_text())
        return dict(deepcopy(PACK), seed=seed, scheduling=data.get('scheduling', {}),
                    ladder=dict(PACK['ladder'], strength=[f'第{n}阶' for n in range(61)]))

    def plan(self, canon, count=120, actual=False, start=None):
        rows = deepcopy(start or [])
        for n in range(len(rows)+1, count+1):
            row = choose_contract(canon, rows, n)
            if actual:
                row['payoff'] = dict(type=row['payoff_type'], fulfilled=True)
            row['reserved_payoff_type'] = row['payoff_type']
            rows.append(row)
        return rows

    def test_seeded_book_plans_differ_without_fixed_five_chapter_cycle(self):
        first = self.plan(self.canon('山门外的雷声'))
        second = self.plan(self.canon('斩断天命以后'))
        types = [r['payoff_type'] for r in first]
        self.assertNotEqual(types, [r['payoff_type'] for r in second])
        self.assertNotEqual([r['upgrade_due'] for r in first], [r['upgrade_due'] for r in second])
        self.assertTrue(any(a != b for a,b in zip(types, types[5:])))
        self.assertEqual(first, self.plan(self.canon('山门外的雷声')))

    def test_upgrade_gaps_setup_and_canonical_single_step_guard(self):
        canon = self.canon('浮动升级的测试书'); rows = self.plan(canon, 240)
        upgrades = [r['number'] for r in rows if r['upgrade_due']]
        gaps = [b-a for a,b in zip([0]+upgrades, upgrades)]
        self.assertGreater(len(set(gaps)), 1)
        self.assertTrue(all(4 <= gap <= 8 for gap in gaps), gaps)
        for row in rows:
            self.assertEqual(row['upgrade_due'], row['payoff_type'] == '升级')
            self.assertEqual(row['growth_setup'], row['number']+1 in upgrades)
            if row['upgrade_due']:
                self.assertEqual(rows[row['number']-2]['next_upgrade'], row['number'])
        self.assertEqual(dashboard_guard({'strength':'第0阶'}, {'strength':'第2阶'}, canon, '')[0]['strength'], '第0阶')

    def test_every_chapter_due_and_sliding_payoff_constraints(self):
        for seed in ('甲书', '乙书', '第三本', 42):
            rows = self.plan(self.canon(seed), 180)
            for i,row in enumerate(rows):
                self.assertTrue(row['payoff_due']); self.assertIn(row['payoff_type'], MENU)
                if i>=2: self.assertGreater(len({r['payoff_type'] for r in rows[i-2:i+1]}), 1)
                if i>=9: self.assertGreaterEqual(len({r['payoff_type'] for r in rows[i-9:i+1]}), 3)

    def test_future_reservations_execution_and_checkpoint_roundtrip_agree(self):
        canon=self.canon('断点续写的一本书'); planned=self.plan(canon)
        actual=self.plan(canon, actual=True)
        keys=('payoff_type','payoff_due','growth_setup','upgrade_due','next_upgrade','hook_type')
        self.assertEqual([{k:r[k] for k in keys} for r in planned], [{k:r[k] for k in keys} for r in actual])
        for cut in (1,9,10,19,25,49,99):
            resumed=self.plan(canon, start=json.loads(json.dumps(planned[:cut], ensure_ascii=False)))
            self.assertEqual(planned, resumed)
        before=deepcopy((canon,planned))
        choose_contract(canon,planned[:30],31)
        self.assertEqual((canon,planned),before)

    def test_pending_upgrade_precedes_calendar_and_blocked_debt_remains_explicit(self):
        canon=self.canon('升级债务')
        plan=choose_contract(canon,[],2,pending='升级')
        self.assertEqual(plan['payoff_type'],'升级');self.assertTrue(plan['upgrade_due'])
        self.assertEqual(plan['next_upgrade'],2);self.assertIsNone(plan['deferred_payoff'])
        self.assertNotIn('pending_contract_blocked',plan['warnings'])
        self.assertNotIn('growth_target',plan)
        for rows in ([dict(number=n,reserved_payoff_type='升级') for n in (1,2)],
                     [dict(number=n,reserved_payoff_type='升级' if n%2 else '打脸') for n in range(1,10)]):
            result=choose_contract(canon,rows,len(rows)+1,pending='升级')
            self.assertNotEqual(result['payoff_type'],'升级')
            self.assertIn('pending_contract_blocked',result['warnings'])
            self.assertEqual(result['deferred_payoff'],'升级')
        calendar=self.plan(canon,20);n=next(r['number'] for r in calendar if r['upgrade_due'])
        result=choose_contract(canon,[],n,pending='打脸')
        self.assertEqual(result['payoff_type'],'打脸');self.assertFalse(result['upgrade_due'])

    def test_schedule_configuration_is_data_and_top_grade_has_no_extra_upgrade(self):
        canon=self.canon('测试配置');self.assertEqual(canon['scheduling']['upgrade_gap'], [4,8])
        self.assertEqual(canon['scheduling']['setup_lead_chapters'],1)
        canon['ladder']['strength']=['初阶','终阶'];rows=self.plan(canon,30)
        self.assertEqual(sum(r['upgrade_due'] for r in rows),1)
        self.assertIsNone(rows[-1]['next_upgrade'])
        canon=self.canon('配置覆盖');canon['scheduling']['upgrade_gap']=[4,4]
        self.assertEqual([r['number'] for r in self.plan(canon,20) if r['upgrade_due']],[4,8,12,16,20])

if __name__ == '__main__': unittest.main()
