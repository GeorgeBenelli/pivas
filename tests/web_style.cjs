// Visual/navigation checks on preview_web.py; all state is a temporary fixture.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH || undefined});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1080},colorScheme:'dark'}),out=path.resolve(__dirname,'../docs');
  const errors=[],remote=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!r.url().startsWith('http://127.0.0.1:8879/'))remote.push(r.url());});
  await page.goto('http://127.0.0.1:8879');await page.getByRole('heading',{name:'Медиатека',exact:true}).waitFor();
  await page.locator('#navMore > summary').click();
  assert.equal(await page.locator('#navMore').evaluate(e=>e.open),true);
  await page.locator('#navMore > summary').press('Escape');
  assert.equal(await page.locator('#navMore').evaluate(e=>e.open),false);
  assert.equal(await page.locator('#navMore > summary').evaluate(e=>e===document.activeElement),true);
  await page.locator('#navMore > summary').click();
  await page.locator('#navMore a[href="#backups"]').click();
  assert.equal(await page.locator('#navMore').evaluate(e=>e.open),false);
  await page.waitForFunction(()=>{const top=document.querySelector('#backups').getBoundingClientRect().top;const padding=parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop);const target=Math.min(document.documentElement.scrollHeight-innerHeight,scrollY+top-padding);return Math.abs(scrollY-target)<2;});
  const position=await page.locator('#backups').boundingBox();const header=await page.locator('.topbar').boundingBox();
  assert.ok(position.y>=header.y+header.height,'anchor section stays below fixed header');
  await page.locator('.brand').click();await page.waitForFunction(()=>scrollY===0);
  await page.screenshot({path:out+'/ui-eliza-desktop-dark.png',animations:'disabled'});
  await page.emulateMedia({colorScheme:'light'});await page.screenshot({path:out+'/ui-eliza-desktop-light.png',animations:'disabled'});
  for(const width of [320,360,390,760,820,1024,1051,1280,1440,1704,1920,2560,3408]){
   await page.setViewportSize({width,height:900});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'no horizontal overflow '+width);
   if(width<=850){assert.equal(await page.locator('input:not([type=file]),textarea,select').evaluateAll(es=>es.some(e=>parseFloat(getComputedStyle(e).fontSize)<16)),false,'no iOS input zoom '+width);}
   if(width>1050){
    const desktop=await page.evaluate(()=>{
     const m=document.querySelector('main'),s=getComputedStyle(m),h=document.querySelector('.topbar').getBoundingClientRect();
     const left=parseFloat(s.paddingLeft),right=parseFloat(s.paddingRight);
     return {workspace:(m.clientWidth-left-right)/innerWidth,aligned:Math.abs(h.left-left)<2,
      smallText:parseFloat(getComputedStyle(document.querySelector('.chip')).fontSize),
      button:parseFloat(getComputedStyle(document.querySelector('.group-more .text-button')).fontSize)};
    });
    assert.ok(desktop.workspace>=.90,'desktop uses screen width '+width);
    assert.ok(desktop.aligned,'header aligns with workspace '+width);
    assert.ok(desktop.smallText>=13 && desktop.button>=14,'desktop text is readable '+width);
   }
  }
  await page.setViewportSize({width:1920,height:1080});await page.screenshot({path:out+'/ui-eliza-desktop-wide.png',animations:'disabled'});
  await page.emulateMedia({colorScheme:'dark'});await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:out+'/ui-eliza-mobile-dark.png',animations:'disabled'});
  await page.locator('#navMore > summary').click();await page.screenshot({path:out+'/ui-eliza-mobile-menu.png',animations:'disabled'});
  await page.locator('#navMore a[href="#telegram"]').click();assert.equal(await page.locator('#navMore').evaluate(e=>e.open),false);
  await page.goto('http://127.0.0.1:8879/login');await page.getByRole('button',{name:'Войти',exact:true}).waitFor();
  await page.screenshot({path:out+'/ui-eliza-login-mobile.png',animations:'disabled'});
  assert.equal(await page.locator('#p').evaluate(e=>parseFloat(getComputedStyle(e).fontSize)),16);
  await page.setViewportSize({width:1440,height:960});await page.screenshot({path:out+'/ui-login-dark.png',animations:'disabled'});
  await page.emulateMedia({colorScheme:'light'});await page.screenshot({path:out+'/ui-login-light.png',animations:'disabled'});
  await page.emulateMedia({reducedMotion:'reduce'});await page.goto('http://127.0.0.1:8879');
  assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).scrollBehavior),'auto');
  assert.deepEqual(errors,[]);assert.deepEqual(remote,[]);
  console.log('Style PASS: menu/keyboard/anchors; widths 320–3408; desktop workspace/text; mobile inputs; dark/light/login; reduced motion; no remote resources.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
