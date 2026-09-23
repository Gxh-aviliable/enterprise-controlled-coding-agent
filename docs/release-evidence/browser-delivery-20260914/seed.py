import json,os,secrets,subprocess,urllib.request
from pathlib import Path
root=Path(__file__).resolve().parent
def request(path,data=None,token=None):
 headers={'Content-Type':'application/json'}
 if token:headers['Authorization']='Bearer '+token
 req=urllib.request.Request('http://127.0.0.1:18000'+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
 with urllib.request.urlopen(req,timeout=20) as res:return json.load(res)
fixture={'email':'goal-browser-'+secrets.token_hex(5)+'@example.com','password':secrets.token_urlsafe(24)}
r=request('/auth/register',{'username':'goal_browser_'+secrets.token_hex(5),**fixture})
token=request('/auth/login',fixture)['access_token']
user=request('/auth/me',token=token); uid=int(user['id'])
fixture['user_id']=uid;fixture['access_token']=token
p=root/'fixture.json';p.write_text(json.dumps(fixture));p.chmod(0o600)
script='''import asyncio\nfrom enterprise_agent.db.mysql import async_session_factory,engine\nfrom enterprise_agent.models.user import User\nfrom enterprise_agent.core.agent.tools.workspace import get_user_workspace\nengine.echo=False\nasync def main():\n async with async_session_factory() as db:\n  user=await db.get(User,UID)\n  assert user.email.startswith('goal-browser-')\n  user.is_superuser=True\n  await db.commit()\n root=get_user_workspace(UID)\n (root/'sleeper.py').write_text('import time\\ntime.sleep(30)\\n')\n (root/'calculator.py').write_text('def add(a, b): return a - b\\n')\n (root/'test_calculator.py').write_text('from calculator import add\\ndef test_add(): assert add(2, 3) == 5\\n')\n (root/'README.md').write_text('Synthetic browser test project. Run python -B -m pytest -q.\\n')\n await engine.dispose()\nasyncio.run(main())\n'''.replace('UID',str(uid))
subprocess.run(['docker','--context','desktop-linux','exec','-i','goal-agent-20260914-api-1','/app/.venv/bin/python','-'],input=script,text=True,check=True,capture_output=True)
print(json.dumps({'fixture_created':True,'user_id':uid,'isolated_project':'goal-agent-20260914'}))
