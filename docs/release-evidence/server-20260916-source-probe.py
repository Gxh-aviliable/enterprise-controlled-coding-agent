from pathlib import Path
import hashlib,json,sys
root=Path('/app');m=json.loads((root/'enterprise_agent/_build_manifest.json').read_text());checked=[]
for n,h in m['files'].items():
 if n.startswith(('enterprise_agent/','migrations/','shared_skills/')) or n in ('pyproject.toml','alembic.ini'):
  assert hashlib.sha256((root/n).read_bytes()).hexdigest()==h,n
  checked.append(n)
# The existing server Dockerfile changes only package download URLs to its mirror.
lock=(root/'uv.lock').read_bytes().replace(b'https://pypi.tuna.tsinghua.edu.cn/packages',b'https://files.pythonhosted.org/packages')
assert hashlib.sha256(lock).hexdigest()==m['files']['uv.lock']
print(json.dumps({'source_sha256':m['source_sha256'],'runtime_files_verified':len(checked),'uv_lock_original_hash_verified_after_reversing_server_mirror_urls':True}))
