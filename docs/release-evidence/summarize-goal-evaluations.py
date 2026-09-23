"""Read-only report aggregation; takes a completed paired index, emits JSON + Markdown."""
import argparse
import collections
import json
import statistics
from datetime import datetime
from pathlib import Path


def seconds(value):
    return datetime.fromisoformat(value).timestamp()


def analyze(index_path):
    index=json.loads(index_path.read_text())
    if not index['complete'] or len(index['runs'])!=6:raise ValueError('Paired run is incomplete')
    rows=[]
    for run in index['runs']:
        report=json.loads((index_path.parent/Path(run['artifacts']['json']).name).read_text())
        assert report['run_metadata']['source_snapshot']['unchanged']
        assert report['run_metadata']['source_snapshot']['before_sha256']==index['source_sha256']
        for case in report['results']:
            events=case['trace']['events'];tools=[e for e in events if e['type']=='tool']
            parent=[];child=[];scopes=[]
            for e in tools:
                data=e['data'];args=data.get('args_summary',data.get('input_summary',{}))
                if e['name']=='read_file':
                    (child if data.get('child_id') else parent).append(args.get('path'))
                if e['name']=='delegate_task':scopes.append(args)
            starts={e['data']['child_id']:seconds(e['timestamp']) for e in events
                    if e['type']=='child_task' and e['status']=='running'}
            intervals=[]
            for e in events:
                if e['type']=='child_task' and e['status'] not in {'running','queued'}:
                    cid=e['data']['child_id'];end=seconds(e['timestamp'])
                    intervals.append({'child_id':cid,'start':starts.get(cid),'end':end,'status':e['status'],
                                      'duration_ms':e['duration_ms'],
                                      'first_parent_model_result_after_child':next((m['timestamp'] for m in events
                                       if m['type']=='model' and m['name']=='llm_call' and seconds(m['timestamp'])>end),None)})
            concurrent=any(a['start'] is not None and b['start'] is not None and
                           max(a['start'],b['start'])<min(a['end'],b['end'])
                           for i,a in enumerate(intervals) for b in intervals[i+1:])
            writes=[e for e in tools if e['name'] in {'write_file','edit_file','delete_paths'} and e['status']=='success']
            successful=[v['end'] for v in intervals if v['status']=='succeeded']
            after_child=all(any(end<=seconds(e['timestamp']) for end in successful) for e in writes) if writes else None
            metrics=case['trace']['metrics'];budget=case.get('task_budget',{})
            rows.append({'repeat':run['repeat'],'mode':run['mode'],'case':case['id'],'status':case['status'],
                         'duration_ms':case['duration_ms'],'tokens':metrics['total_tokens'],'model_calls':metrics['model_calls'],
                         'parent_reads':len(parent),'child_reads':len(child),
                         'repeated_paths':len(parent+child)-len(set(parent+child)),
                         'parent_child_overlap_paths':sorted(set(parent)&set(child)-{None}),
                         'parent_remaining_tokens':budget.get('remaining_tokens'),
                         'child_outcomes':dict(collections.Counter(v['status'] for v in budget.get('child_tasks',{}).values())),
                         'scopes':scopes,'child_intervals':intervals,'observed_parallel_children':concurrent,
                         'file_mutations_after_successful_child':after_child})
    summary={}
    for mode in ('single','multi'):
        values=[r for r in rows if r['mode']==mode]
        summary[mode]={'passed':sum(r['status']=='passed' for r in values),'executions':len(values),
                       'tokens_total':sum(r['tokens'] for r in values),'tokens_median':statistics.median(r['tokens'] for r in values),
                       'duration_median_ms':statistics.median(r['duration_ms'] for r in values),
                       'model_calls':sum(r['model_calls'] for r in values),
                       'repeated_paths':sum(r['repeated_paths'] for r in values),
                       'parent_remaining_tokens_min':min(r['parent_remaining_tokens'] for r in values),
                       'parent_remaining_tokens_median':statistics.median(r['parent_remaining_tokens'] for r in values)}
    return {'source_sha256':index['source_sha256'],'summary':summary,'runs':rows,
            'limits':['Three paired repeats per case are exploratory, not general statistical proof.',
                      'Raw trace supplies child scope, start/end, delivery and following parent model/write events; delivery does not prove semantic use.',
                      'Repeated reads can be legitimate after changes; counts are not automatic waste.',
                      'Local diagnostic tests/builds also ran on this host; latency is not a dedicated-host benchmark.',
                      'All failures and original budgets retained; child permissions remain read-only.']}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('index',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=analyze(args.index)
    args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    lines=['# Single/Multi 配对诊断','',f"冻结源码 `{result['source_sha256']}`；18对任务、36次执行。",'',
           '|模式|通过|token中位数|耗时中位数ms|重复路径读取|父任务剩余token中位数|','|---|---:|---:|---:|---:|---:|']
    for mode,s in result['summary'].items():
        lines.append(f"|{mode}|{s['passed']}/{s['executions']}|{s['tokens_median']}|{s['duration_median_ms']}|{s['repeated_paths']}|{s['parent_remaining_tokens_median']}|")
    lines+=['','所有逐例原始轨迹、范围、子执行时间和父任务预算见同名JSON及配对index。','',*['- '+v for v in result['limits']]]
    args.output.with_suffix('.md').write_text('\n'.join(lines)+'\n')
