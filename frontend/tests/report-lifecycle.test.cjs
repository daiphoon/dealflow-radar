const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
function load(relative,mocks={}) {
  const code=ts.transpileModule(fs.readFileSync(path.join(__dirname,'..',relative),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText;
  const module={exports:{}};
  new Function('require','module','exports',code)((id)=>Object.hasOwn(mocks,id)?mocks[id]:require(id),module,module.exports);
  return module.exports;
}
const report=(status='historical_snapshot')=>({id:'report',company_id:'company',title:'虚构报告',company_legal_name:'虚构公司',as_of:'2026-09-26T00:00:00Z',source_event_count:2,history_status:status,markdown:status==='restricted'?'报告来源权限或状态已变化，历史正文停止在线提供':'> 资料性质：虚构演示\n\n## 已审核的重要信息\n\n允许的正文',reused:false});
async function render(status,result) {
  const Page=load('app/reports/[id]/page.tsx',{
    'next/link':{default:({children,prefetch,...props})=>React.createElement('a',props,children)},
    '@/components/report-content':load('components/report-content.tsx'),
    '@/lib/api':{getPersonalCompanyReport:async()=>report(status)},
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
test('撤权和过时状态优先于伪造成功参数，也在无 query 时显示',async()=>{
  for(const result of ['report_generated','report_reused',undefined]){
    const restricted=await render('restricted',result);
    assert.match(restricted,/正文停止/);assert.doesNotMatch(restricted,/报告已生成|已直接为你打开|允许的正文/);
    const stale=await render('stale',result);
    assert.match(stale,/已过时/);assert.match(stale,/允许的正文/);assert.doesNotMatch(stale,/报告已生成|已直接为你打开/);
  }
});
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
