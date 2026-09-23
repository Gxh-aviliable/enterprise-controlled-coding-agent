from pathlib import Path
import json
root=Path(__file__).resolve().parents[2];out=root/'docs/release-evidence';reports=root/'benchmarks/results'
raw=[]
for name in ['20260827T181517Z-agent-single.json','20260914T052049Z-agent-single.json','20260914T062224Z-agent-single.json','20260914T063137Z-agent-single.json']:
 d=json.loads((reports/name).read_text());metrics=[r.get('trace',{}).get('metrics',{}) for r in d['results']]
 raw.append({'report':name,'source':d.get('run_metadata',{}).get('source_snapshot',d.get('run_metadata',{}).get('source')),'summary':{k:v for k,v in d['summary'].items() if not k.startswith('by_')},'tokens':{k:sum(m.get(k,0) for m in metrics) for k in ['input_tokens','output_tokens','total_tokens','confirmation_count']},'failures':[{'id':r['id'],'status':r['status'],'infra':r.get('infrastructure_error'),'system':r.get('system_error'),'failed_assertions':[e for e in r.get('evaluations',[]) if not e['passed']]} for r in d['results'] if r['status']!='passed']})
(out/'goal-c-baseline-analysis-20260914.json').write_text(json.dumps(raw,ensure_ascii=False,indent=2))
local=json.loads((reports/'20260914T062349Z-memory-governance.json').read_text());model=json.loads((reports/'20260914T060624Z-memory-governance.json').read_text());suite=json.loads((root/'benchmarks/memory-v2/cases.json').read_text())
expected={c['id']:c['expected'] for c in suite['admission']};ad=[c for c in local['cases'] if c['layer']=='admission'];tp=fp=fn=tn=0
for c in ad:
 actual=c['decision']['accepted'];want=expected[c['id']]
 tp+=bool(actual and want);fp+=bool(actual and not want);fn+=bool(not actual and want);tn+=bool(not actual and not want)
rec=[c['detail'] for c in local['cases'] if c['layer']=='retrieval'];beh=[c for c in model['cases'] if c['layer']=='behavior']
result={'local_report':'20260914T062349Z-memory-governance.json','model_report':'20260914T060624Z-memory-governance.json','local_source':local['source_sha256'],'model_source':model['source_sha256'],'admission':{'tp':tp,'fp':fp,'fn':fn,'tn':tn,'precision':tp/(tp+fp),'recall':tp/(tp+fn)},'retrieval':{'cases':len(rec),'negative_cases':sum(not c['expected'] for c in rec),'irrelevant_injection_cases':sum(bool(c['unexpected']) for c in rec),'forbidden_injections':sum(len(c['forbidden_injected']) for c in rec),'injected_tokens':[c['injected_tokens'] for c in rec]},'lifecycle':[c for c in local['cases'] if c['layer']=='lifecycle'],'behavior':[{'id':c['id'],'passed':c['passed'],'seed_recalled':c['seed_recalled'],'metrics':c['trace']['metrics']} for c in beh],'behavior_conformity':sum(c['passed'] for c in beh)/len(beh),'limitations':['Small synthetic suite; not real-user generalization','Memory injection attack was not recalled; no evidence of resistance after exposure','No no-memory control arm; injected context cost is measured, causal benefit/cost difference is not','Current-instruction case used pip but strict exact/forbidden wording assertion failed; retained']}
(out/'goal-j-memory-analysis-20260914.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({'admission':result['admission'],'behavior':result['behavior_conformity'],'token_totals':[r['tokens'] for r in raw]},ensure_ascii=False))
