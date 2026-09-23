import asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright
base='http://127.0.0.1:18877'; backend='http://127.0.0.1:18878'
tokens=json.loads(Path('/tmp/skill-ownership-tokens.json').read_text())
dest=Path('docs/release-evidence/skill-ownership-20260923');dest.mkdir(parents=True,exist_ok=True)
async def main():
 errors=[]
 async with async_playwright() as p:
  browser=await p.chromium.launch(channel='chrome',headless=True)
  context=await browser.new_context(viewport={'width':1440,'height':1100})
  async def proxy(route):
   response=await route.fetch(url=backend+'/api/'+route.request.url.split('/api/',1)[1]);await route.fulfill(response=response)
  await context.route(base+'/api/**',proxy)
  page=await context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
  await page.goto(base,wait_until='networkidle')
  await page.evaluate('''async token => { localStorage.setItem('access_token',token);const main=await (await fetch('/src/main.js')).text();const {createApp}=await import(main.match(/from "([^"]+)"/)[1]);const {default:Admin}=await import('/src/components/admin/AdminConsole.vue');document.querySelector('#app').__vue_app__.unmount();createApp(Admin).mount('#app'); }''',tokens['1'])
  await page.get_by_role('button',name='公共 Skills',exact=False).click()
  await page.wait_for_load_state('networkidle')
  await page.locator('.skill-list button').filter(has_text='python').click()
  editor=page.locator('.skill-editor textarea');await page.wait_for_function('document.querySelector(".skill-editor h3")?.textContent === "python"')
  original=await editor.input_value(); assert len(original)>100 and await editor.is_enabled()
  assert 'BUILTIN' not in await page.locator('.skill-list').inner_text()
  await page.screenshot(path=str(dest/'local-admin-editable.png'))
  user_headers={'Authorization':'Bearer '+tokens['2']}
  async def public():
   r=await context.request.get(backend+'/api/skills',headers=user_headers);assert r.status==200;return (await r.json())['items']
  before=await public();assert len(before)==4 and {i['source'] for i in before}=={'managed'}
  denied=await context.request.get(backend+'/api/admin/skills',headers=user_headers);assert denied.status==403
  await editor.fill(original+'\n\nAdministrator browser regression update.\n')
  async with page.expect_response(lambda r:'/api/admin/skills' in r.url and r.request.method=='POST') as save:
   await page.get_by_role('button',name='保存草稿',exact=True).click()
  assert (await save.value).status==201
  await page.wait_for_load_state('networkidle')
  assert next(i for i in await public() if i['name']=='python')['version']==1
  await page.get_by_placeholder('说明本次修改').fill('Browser ownership regression')
  async with page.expect_response(lambda r:r.url.endswith('/python/publish')) as published:
   await page.get_by_role('button',name='发布版本',exact=True).click()
  assert (await published.value).status==200,await (await published.value).text()
  await page.wait_for_load_state('networkidle')
  assert next(i for i in await public() if i['name']=='python')['version']==2
  await page.get_by_placeholder('说明本次修改').fill('Retire browser test version')
  async with page.expect_response(lambda r:'/python/retire' in r.url) as retired:
   await page.get_by_role('button',name='下架',exact=True).click()
  assert (await retired.value).status==200
  await page.get_by_role('button',name='下架',exact=True).wait_for(state='hidden')
  assert 'python' not in {i['name'] for i in await public()}
  await page.get_by_placeholder('说明本次修改').fill('Restore original version')
  async with page.expect_response(lambda r:r.url.endswith('/python/rollback')) as rolled:
   await page.locator('.version-list > span').filter(has=page.locator('code',has_text='v1')).get_by_role('button',name='回滚').click()
  assert (await rolled.value).status==200
  await page.wait_for_load_state('networkidle')
  assert next(i for i in await public() if i['name']=='python')['version']==3
  await page.screenshot(path=str(dest/'local-admin-restored.png'))
  assert not errors,errors
  report={'database':'disposable MySQL 8','real_auth':True,'real_admin_and_catalog_api':True,'api_mocked':False,'component_mounted_in_isolation':True,'fresh_install_empty':True,'upgrade_migrated_public_skills':4,'existing_body_editable':True,'save_does_not_publish':True,'publish_visible_to_user':True,'retire_no_fallback':True,'rollback_restores':True,'normal_user_admin_status':403,'page_errors':errors}
  (dest/'local-browser.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
  await browser.close()
asyncio.run(main())
