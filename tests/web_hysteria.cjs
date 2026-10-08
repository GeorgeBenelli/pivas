// Real web form/API with temporary fixture; never changes a real router.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH || undefined});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1080}}),url='http://127.0.0.1:8879';
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);await page.locator('#groups .group-card').first().waitFor();
  const before=await page.request.get(url+'/api/catalog').then(r=>r.json());
  await page.locator('[data-config="2"]').click();
  assert.match(await page.locator('#config').textContent(),/Hysteria 2/);
  await page.locator('#vlessUrl').fill('hy2://fixture-password@hysteria.example:443/?sni=tls.example&pinSHA256='+'ab'.repeat(32)+'&obfs=salamander&obfs-password=maskpass');
  await page.locator('#configForm').getByRole('button',{name:'Сохранить',exact:true}).click();
  await page.locator('#server2').getByText(/hysteria.example:443.*HYSTERIA 2/).waitFor();
  assert.deepEqual(await page.request.get(url+'/api/catalog').then(r=>r.json()),before);
  await page.reload();await page.locator('#server2').getByText(/HYSTERIA 2/).waitFor();
  await page.locator('[data-config="2"]').click();
  assert.equal(await page.locator('#vlessUrl').inputValue(),'');
  await page.locator('#vlessUrl').fill('hysteria://unsupported');
  await page.locator('#configForm').getByRole('button',{name:'Сохранить',exact:true}).click();
  await page.locator('#configError').getByText(/hysteria2/).waitFor();
  await page.locator('#config [data-close]').click();
  await page.locator('#swapSlots').click();await page.locator('#server1').getByText(/HYSTERIA 2/).waitFor();
  assert.deepEqual(await page.request.get(url+'/api/catalog').then(r=>r.json()),before);
  await page.locator('#swapSlots').click();await page.locator('#server2').getByText(/HYSTERIA 2/).waitFor();
  await page.setViewportSize({width:390,height:844});
  await page.locator('[data-config="2"]').click();
  assert.equal(await page.locator('#vlessUrl').evaluate(e=>parseFloat(getComputedStyle(e).fontSize)),16);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);
  console.log('Hysteria web PASS: form import; transport display; reload; hidden secret; scheme error; mixed swap; groups unchanged; mobile input.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
