import subprocess,time,json
from pathlib import Path
root=Path('/private/tmp/goal-publish-fault-20260914')
with (root/'driver.log').open('w') as log:
 p=subprocess.Popen(['node',str(root/'fault.cjs')],stdout=log,stderr=subprocess.STDOUT)
 for n in range(600):
  if (root/'restart-ready.json').exists():break
  if p.poll() is not None:raise RuntimeError('Browser ended before publish fault window')
  time.sleep(.2)
 else:raise RuntimeError('No ready signal')
 fixture=json.loads((root/'restart-ready.json').read_text());assert fixture['project']=='goal-agent-20260914'
 d=['docker','--context','desktop-linux'];name='goal-agent-20260914-api-1'
 info=json.loads(subprocess.check_output(d+['inspect',name]))[0]
 assert info['Config']['Labels']['com.docker.compose.project']=='goal-agent-20260914'
 try:subprocess.run(d+['kill','--signal','KILL',name],check=True,stdout=log,stderr=log)
 finally:subprocess.run(d+['start',name],check=True,stdout=log,stderr=log)
 code=p.wait(timeout=170)
 assert code==0
 print(json.dumps({'passed':True,'trace_id':fixture['trace_id']}))
