import { chromium } from 'playwright';
import { pathToFileURL } from 'url';
const DEMO = pathToFileURL('C:/Users/RUMAN/Downloads/Movie Final Plan implementation/Movie demo design index.html').href;
const SITE = process.argv[2] || 'http://127.0.0.1:4180/';
const OUT = process.argv[3] || 'C:/Users/RUMAN/AppData/Local/Temp/claude/c--Users-RUMAN-Downloads-Movie-Final-Plan-implementation/eaf652cd-ab0d-48c3-97d6-4792dda86a71/scratchpad/shots';
import fs from 'fs';
fs.mkdirSync(OUT, { recursive: true });
const b = await chromium.launch();
for (const vp of [{n:'d',w:1440,h:950},{n:'m',w:390,h:844}]) {
  // demo
  {
    const c = await b.newContext({ viewport:{width:vp.w,height:vp.h}, isMobile: vp.w<800, hasTouch: vp.w<800 });
    const p = await c.newPage();
    await p.goto(DEMO,{waitUntil:'load'}); await p.waitForTimeout(1500);
    await p.screenshot({path:`${OUT}/demo-home-${vp.n}.png`, fullPage:false});
    await p.locator('.media-card').first().click(); await p.waitForTimeout(800);
    await p.screenshot({path:`${OUT}/demo-detail-${vp.n}.png`, fullPage:false});
    await p.locator('#detailPlayAction').click(); await p.waitForTimeout(1000);
    await p.screenshot({path:`${OUT}/demo-player-${vp.n}.png`, fullPage:false});
    await c.close();
  }
  // site
  {
    const c = await b.newContext({ viewport:{width:vp.w,height:vp.h}, isMobile: vp.w<800, hasTouch: vp.w<800 });
    const p = await c.newPage();
    await p.goto(SITE,{waitUntil:'domcontentloaded',timeout:60000}); await p.waitForTimeout(3000);
    await p.locator('.final-main-button:visible',{hasText:'Movies'}).first().click(); await p.waitForTimeout(7000);
    await p.screenshot({path:`${OUT}/site-home-${vp.n}.png`});
    await p.locator('.movie-card:visible').first().click(); await p.waitForTimeout(3000);
    await p.screenshot({path:`${OUT}/site-detail-${vp.n}.png`});
    const play = p.locator('.movie-detail-play:visible').first();
    if (await play.count()) { await play.click(); await p.waitForTimeout(9000); await p.screenshot({path:`${OUT}/site-player-${vp.n}.png`}); }
    await c.close();
  }
}
await b.close();
console.log('shots in', OUT);
