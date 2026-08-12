import Link from "next/link";

export const metadata = {
  title: "邀请测试说明与隐私告知｜原始股雷达",
  description: "原始股雷达香港邀请测试环境的服务范围、数据处理和退出说明。",
};

export default function TrialNoticePage() {
  return (
    <main className="shell page-stack notice-page">
      <section className="hero notice-hero">
        <p className="eyebrow">Invitation-only validation</p>
        <h1>邀请测试说明与隐私告知</h1>
        <p>
          本说明适用于原始股雷达的中国香港邀请测试环境。当前版本仅用于小范围产品验证，不是公开注册、正式收费或投资建议服务。
        </p>
        <p className="notice-effective-date">生效日期：2026 年 8 月 12 日</p>
      </section>

      <section className="panel notice-section">
        <h2>1. 服务部署和测试范围</h2>
        <ul>
          <li>应用和业务数据库部署在腾讯云中国香港服务器。</li>
          <li>腾讯云 CloudBase 仅用于邮箱身份认证；公司、基金和业务权限仍由本平台服务端判断。</li>
          <li>加密数据库备份存放在中国香港私有对象存储中，生产服务器不保存解密私钥。</li>
          <li>仅受邀账户可以登录；请勿上传真实投资协议、持股比例、内部估值、未公开财务资料或其他敏感文件。</li>
        </ul>
      </section>

      <section className="panel notice-section">
        <h2>2. 测试期间处理的数据</h2>
        <p>为提供和验证当前功能，平台会处理以下最小必要数据：</p>
        <ul>
          <li>受邀邮箱、本地用户标识、CloudBase 身份映射及登录、刷新和退出审计记录；</li>
          <li>公司查询次数、个人关注、公司收录或更新申请、查看回执和固定模板报告；</li>
          <li>为安全、可用性和故障排查产生的必要技术日志，例如访问时间、请求路径、响应状态、来源网络地址和浏览器信息；</li>
          <li>测试者主动反馈的问题和复现所需信息。</li>
        </ul>
        <p>
          平台共享的公司档案与个人或机构私有数据分层处理。个人关注和报告仅本人可见；基金投资信息只向获得相应基金授权的用户展示。
        </p>
      </section>

      <section className="panel notice-section">
        <h2>3. 使用目的和共享边界</h2>
        <ul>
          <li>完成身份认证、权限判断、公司查询、关注、报告和测试期额度管理；</li>
          <li>发现故障、安全异常和权限问题，并改进邀请测试产品；</li>
          <li>不会因为用户查询同一家公司而向其展示其他个人或机构的私有线索、文件或投资信息；</li>
          <li>不会把测试账户数据出售给第三方，也不会将私有材料自动晋升为平台共享事实。</li>
        </ul>
      </section>

      <section className="panel notice-section">
        <h2>4. 保存期限和退出</h2>
        <ul>
          <li>测试账户及其个人功能数据在受邀测试期间保存；收到退出请求后，原则上在 30 天内删除或匿名化。</li>
          <li>身份认证和安全审计记录最长保留 180 天，用于追查登录、越权和安全事件。</li>
          <li>加密数据库备份按生命周期规则保留，最长 56 天后自动到期；因此已从主库删除的数据可能在受控加密备份中短期残留。</li>
          <li>发生安全事件、争议或依法需要保留时，只在必要范围和期限内延长保存。</li>
        </ul>
      </section>

      <section className="panel notice-section">
        <h2>5. 查询、更正和停止测试</h2>
        <p>
          如需查询、更正或删除个人测试数据，或停止参加测试，请直接联系邀请人或回复邀请邮件。收到请求后会先核验账户归属，再按上述期限处理。
        </p>
        <p>
          如果你不同意本说明，请不要继续登录或使用测试环境。当前页面不是正式商业隐私政策；进入公开注册或收费阶段前将另行完成法律和产品审查。
        </p>
      </section>

      <Link className="back-link" href="/login">
        返回登录
      </Link>
    </main>
  );
}
