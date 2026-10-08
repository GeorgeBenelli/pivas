// Shared web form/API on a temporary preview, never on a router.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH || undefined});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1080}}),url=process.env.PIVAS_PREVIEW_URL||'http://127.0.0.1:8879';
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);await page.locator('#groups .group-card').first().waitFor();
  const before=await page.request.get(url+'/api/catalog').then(r=>r.json());
  const base='vless://00000000-0000-0000-0000-000000000001@xhttp.example:443?type=xhttp&path=%2Ftransport%2F&mode=stream-up';
  for(const [security,params] of [['tls','&pcs='+'ab'.repeat(32)],['reality','&pbk=fixture-key&sid=ab']]){
   await page.locator('[data-config="2"]').click();
   assert.match(await page.locator('#config').textContent(),/XHTTP/);
   assert.equal(await page.locator('#vlessUrl').inputValue(),'');
   await page.locator('#vlessUrl').fill(base+'&security='+security+params);
   await page.locator('#configForm').getByRole('button',{name:'Сохранить',exact:true}).click();
   await page.locator('#server2').getByText(new RegExp('xhttp.example:443.*XHTTP / '+security.toUpperCase())).waitFor();
   assert.deepEqual(await page.request.get(url+'/api/catalog').then(r=>r.json()),before);
   await page.reload();await page.locator('#server2').getByText(/XHTTP/).waitFor();
  }
  await page.locator('[data-config="2"]').click();
  await page.locator('#vlessUrl').fill(base+'&security=tls&flow=xtls-rprx-vision');
  await page.locator('#configForm').getByRole('button',{name:'Сохранить',exact:true}).click();
  await page.locator('#configError').getByText(/Команда не выполнена/).waitFor();
  await page.locator('#config [data-close]').click();
  await page.locator('#swapSlots').click();await page.locator('#server1').getByText(/XHTTP/).waitFor();
  assert.deepEqual(await page.request.get(url+'/api/catalog').then(r=>r.json()),before);
  await page.setViewportSize({width:390,height:844});await page.locator('[data-config="1"]').click();
  assert.equal(await page.locator('#vlessUrl').evaluate(e=>parseFloat(getComputedStyle(e).fontSize)),16);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);
  console.log('XHTTP web PASS: TLS and Reality import; state/reload; hidden secret; invalid flow; mixed swap; groups unchanged; mobile input.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
