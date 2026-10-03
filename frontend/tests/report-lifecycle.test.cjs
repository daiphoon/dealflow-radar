const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
function load(relative,mocks={}) {
  const code=ts.transpileModule(fs.readFileSync(path.join(__dirname,'..',relative),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2017,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText;
  const module={exports:{}};
  new Function('require','module','exports',code)((id)=>Object.hasOwn(mocks,id)?mocks[id]:require(id),module,module.exports);
  return module.exports;
}
const report=(status='historical_snapshot')=>({id:'report',company_id:'company',title:'虚构报告',company_legal_name:'虚构公司',as_of:'2026-09-26T00:00:00Z',source_event_count:2,history_status:status,markdown:status==='restricted'?'报告来源权限或状态已变化，历史正文停止在线提供':'> 资料性质：虚构演示\n\n## 已审核的重要信息\n\n允许的正文',reused:false});
async function render(status,result,overrides={}) {
  const Page=load('app/reports/[id]/page.tsx',{
    'next/link':{default:({children,prefetch,...props})=>React.createElement('a',props,children)},
    '@/components/report-content':load('components/report-content.tsx'),
    '@/lib/api':{getPersonalCompanyReport:async()=>({...report(status),...overrides})},
    '@/lib/auth-navigation':{redirectIfAuthenticationRequired:async()=>{}},
  }).default;
  return renderToStaticMarkup(await Page({params:Promise.resolve({id:'report'}),searchParams:Promise.resolve({result})}));
}
test('真实报告页面展示正文与虚构性质；URL 只提示合法成功或复用',async()=>{
  for(const result of ['report_generated','report_reused']){
    const html=await render('historical_snapshot',result);
    assert.match(html,/允许的正文/);assert.match(html,/虚构演示/);
    assert.match(html,result==='report_reused'?/不会重复占用/:/报告已生成/);
  }
  assert.doesNotMatch(await render('historical_snapshot','forged'),/报告已生成/);
});
test('正式报告渲染器将恶意 HTML 和脚本链接当作文本',()=>{
  const {ReportContent}=load('components/report-content.tsx');
  const markdown='## 正常融资线索\n\n<img src=x onerror="alert(1)">\n\n[危险链接](javascript:alert(1))\n\n- 合法来源：<https://example.com/evidence>';
  const html=renderToStaticMarkup(React.createElement(ReportContent,{markdown}));
  assert.doesNotMatch(html,/<img|href="javascript:|<script/);
  assert.match(html,/&lt;img/);
  assert.match(html,/href="https:\/\/example.com\/evidence"/);
  assert.match(html,/rel="noreferrer"/);
});
test('撤权和过时状态优先于伪造成功参数，也在无 query 时显示',async()=>{
  for(const result of ['report_generated','report_reused',undefined]){
    const restricted=await render('restricted',result);
    assert.match(restricted,/正文停止/);assert.doesNotMatch(restricted,/来源许可已变化/);assert.doesNotMatch(restricted,/报告已生成|已直接为你打开|允许的正文/);
    const stale=await render('stale',result);
    assert.match(stale,/已过时/);assert.match(stale,/允许的正文/);assert.doesNotMatch(stale,/报告已生成|已直接为你打开/);
  }
});
for (const [name,count] of [['只有待核线索',1],['历史资料和待核混合',2]]) {
  test(`${name}的报告页和列表不把全部引用宣称为已审核`,async()=>{
    const data={...report(),created_at:'2026-09-26T00:00:00Z',source_event_count:count,report_version:'personal-company-v5',
      markdown:'## 待核线索\n\n虚构待核事项，candidate / unconfirmed，缺少独立确认。'+(count===2?'\n\n## 历史资料（窗口外）\n\n虚构历史事项。':'')};
    const detail=await render('historical_snapshot',undefined,data);
    assert.match(detail,/虚构待核事项/);assert.match(detail,/candidate \/ unconfirmed/);
    assert.doesNotMatch(detail,/条已审核事件|内容来自已审核资料|生成时已经审核的信息/);
    const Page=load('app/reports/page.tsx',{
      'next/link':{default:({children,prefetch,...props})=>React.createElement('a',props,children)},
      '@/lib/api':{getPersonalCompanyReports:async()=>[data],getPersonalUsage:async()=>({reports:{used:1,limit:10,remaining:9}})},
      '@/lib/auth-navigation':{redirectIfAuthenticationRequired:async()=>{}},
    }).default;
    const list=renderToStaticMarkup(await Page());
    assert.match(list,/虚构报告/);assert.doesNotMatch(list,/条已审核事件|机构资料或未确认线索/);
    assert.match(detail,/待核线索不代表事实已确认/);assert.match(list,/待核线索/);
  });
}
test('真实 Server Action 跳转采用服务端状态，许可拒绝不提示成功',async()=>{
  class ApiError extends Error{constructor(status,detail){super(detail);this.status=status;this.detail=detail;}}
  for(const status of ['historical_snapshot','restricted','stale','denied']){
    const Actions=load('app/personal-actions.ts',{
      'next/cache':{revalidatePath:()=>{}},
      'next/navigation':{redirect:(url)=>{throw new Error('REDIRECT:'+url);}},
      '@/lib/api':{ApiError,createPersonalCompanyReport:async()=>{if(status==='denied')throw new ApiError(403,'report_content_restricted');return report(status);}},
    });
    const form=new FormData();form.set('company_id','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');form.set('idempotency_key','a'.repeat(64));
    const result=status==='denied'?'error=report_restricted':'result='+(status==='historical_snapshot'?'report_generated':'report_'+status);
    await assert.rejects(Actions.generateCompanyReport(form),new RegExp(result));
  }
});
