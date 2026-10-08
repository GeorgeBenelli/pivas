// Run only against the loopback preview with temporary router state.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH || undefined});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1080},colorScheme:'dark'}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const url='http://127.0.0.1:8879';
  const names=()=>page.request.get(url+'/api/slots').then(r=>r.json());
  await page.goto(url);await page.locator('#slotTitle1').getByText('Слот 1',{exact:true}).waitFor();
  const before=await page.request.get(url+'/api/catalog').then(r=>r.json());
  async function rename(n,value){
   await page.locator(`[data-name="${n}"]`).click();await page.locator('#slotNameInput').fill(value);
   await page.locator('#slotNameForm').getByRole('button',{name:'Сохранить',exact:true}).click();
   await page.locator('#slotTitle'+n).getByText(value,{exact:true}).waitFor();
  }
  await rename(1,'Германия 🇩🇪');await rename(2,'Резерв <img src=x>');
  assert.equal(await page.locator('#slotTitle2 img').count(),0,'name is plain text');
  await page.reload();await page.locator('#slotTitle1').getByText('Германия 🇩🇪',{exact:true}).waitFor();
  assert.equal(await page.locator('.group-route select').first().locator('option[value="1"]').textContent(),'1 · Германия 🇩🇪');
  assert.equal(await page.locator('[data-filter="2"]').textContent(),'2 · Резерв <img src=x>');
  await page.locator('#swapSlots').click();await page.waitForFunction(()=>!document.querySelector('#swapSlots').disabled);
  assert.equal(await page.locator('#slotTitle1').textContent(),'Германия 🇩🇪');
  assert.equal(await page.locator('#slotTitle2').textContent(),'Резерв <img src=x>');
  assert.deepEqual(await page.request.get(url+'/api/catalog').then(r=>r.json()),before);
  await page.locator('#swapSlots').click();await page.waitForFunction(()=>!document.querySelector('#swapSlots').disabled);
  await rename(1,'Ш'.repeat(32));await rename(2,'Щ'.repeat(32));
  for(const width of [320,390,760,1051,1440,1920,3408]){
   await page.setViewportSize({width,height:1080});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'long names fit '+width);
  }
  await page.setViewportSize({width:390,height:844});
  await page.locator('[data-name="1"]').click();
  assert.equal(await page.locator('#slotNameInput').evaluate(e=>parseFloat(getComputedStyle(e).fontSize)),16);
  const saved=await names();
  await page.request.post(url+'/api/slots',{data:{action:'rename',slot:1,name:'Из Telegram',revision:saved.revision}});
  await page.locator('#slotNameInput').fill('Из старой вкладки');
  await page.locator('#slotNameForm').getByRole('button',{name:'Сохранить',exact:true}).click();
  await page.locator('#slotNameError').getByText(/изменились/).waitFor();
  assert.equal((await names()).names['1'],'Из Telegram','conflict does not overwrite another client');
  await page.locator('#slotNameDialog [data-close]').click();await page.locator('#refresh').click();
  await page.locator('#slotTitle1').getByText('Из Telegram',{exact:true}).waitFor();
  await page.locator('[data-name="1"]').click();await page.locator('#resetSlotName').click();
  await page.locator('#slotTitle1').getByText('Слот 1',{exact:true}).waitFor();
  await page.locator('[data-name="2"]').click();await page.locator('#resetSlotName').click();
  await page.locator('#slotTitle2').getByText('Слот 2',{exact:true}).waitFor();
  assert.deepEqual(errors,[]);
  console.log('Slot names PASS: rename/reload; shared route labels; plain text; swap keeps names/groups; long names 320–3408; mobile input; stale-write conflict; reset.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
