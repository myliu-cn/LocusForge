import { ArrowUpRight, BookOpen, Box, Camera, Fingerprint, Gauge, GitBranch, Route, ShieldCheck } from 'lucide-react'
import { MethodSteps } from './components'

export default function Method() {
  return <div className="method-page">
    <div className="method-hero panel"><span className="eyebrow">DESIGNED FOR SPATIAL MEMORY</span><h2>更有价值的回访，<br />而不只是更多视频。</h2>
      <p>长程记忆需要经历「观察 → 离开 → 回访」。LocusForge 把这一过程变成可以控制、度量和复现的数据生产合同。</p>
      <MethodSteps /></div>
    <div className="principle-grid">
      {[
        { icon: <Box />, title: '真实几何，而非重建猜测', body: '自包含 GLB、明确的场景比例和固定的光照。相机内外参直接读取 Blender；不运行 SLAM、VGGT、Pi3X 或单目尺度恢复。' },
        { icon: <Route />, title: '把离场作为回环的一部分', body: '不仅要求回到近似位姿，也要求足够的时间间隔、空间离场和原视图可见支持下降。避免用原地停留制造大量伪回访。' },
        { icon: <Camera />, title: '双向遮挡感知的回环证书', body: '用米制深度反投影到世界，再投影到目标帧，并用目标深度排除遮挡。两个方向都达到重叠阈值，才计入认证回访。' },
        { icon: <Gauge />, title: '先便宜地拒绝，再认真渲染', body: '先检查速度、加速度与连续相机净空；再在同一个 Blender 进程中复用静态场景。用实际秒 / 帧度量成本，不承诺未知硬件的吞吐量。' },
        { icon: <GitBranch />, title: '隔离场景，而不是隔离文件', body: '根据来源场景族稳定划分 train / validation / test。相同 GLB 指纹不允许被归入不同来源族；同一场景改色也不能跨划分。' },
        { icon: <Fingerprint />, title: '每个结果都有可追溯证据', body: '不可变配方、源资产 SHA-256、Blender 版本、逐帧时钟、真实位姿、回环帧对与逐文件校验和一起导出。失败任务不发布训练数据。' },
      ].map((item) => <article className="panel principle" key={item.title}><span>{item.icon}</span><h3>{item.title}</h3><p>{item.body}</p></article>)}
    </div>
    <div className="method-two-column">
      <section className="panel method-section"><h3><BookOpen size={19} />数据合同速查</h3>
        <dl><div><dt>世界坐标</dt><dd>右手系，Z 向上，米制。资产真实比例需由导入者确认。</dd></div>
          <div><dt>相机坐标</dt><dd>OpenCV：右 / 下 / 前；c2w 将相机坐标映射到世界坐标。</dd></div>
          <div><dt>两步路径设计</dt><dd>先用几何模板、动作积木或显式授权的 LLM 解析运动；再按时间进度设置左右偏航和上下俯仰航点。</dd></div>
          <div><dt>零翻滚插值</dt><dd>默认朝向运动方向。偏航走最短角，五次缓动连接航点；每帧锁定世界竖直方向，不用可能带来翻滚的任意四元数插值。</dd></div>
          <div><dt>保存与复用</dt><dd>路径库绑定源场景指纹，保存完整轨迹、镜头航点和时长；版本冲突会拒绝覆盖，不改写已提交任务。</dd></div>
          <div><dt>像素与深度</dt><dd>像素中心为 (u + 0.5, v + 0.5)。深度是相机轴向 Z，float32 米，0 表示无效。</dd></div>
          <div><dt>精确视频时钟</dt><dd>N = 时长 × FPS；第 i 帧时刻 i / FPS。闭合设计在 (N−1) / FPS 闭合，开放设计不补回程；视频时长为 N / FPS。</dd></div>
          <div><dt>动作标签</dt><dd>相邻真实相机的 SE(3) 变换与本地平移速度。不会把视觉估计伪装成实际按键输入。</dd></div>
          <div><dt>训练数据</dt><dd>无损 RGB PNG + 米制 NPY 深度；H.264 MP4 只用于方便审阅。</dd></div></dl>
      </section>
      <section className="panel method-section"><h3><ShieldCheck size={19} />明确边界，才能可靠使用</h3>
        <p>当前交付针对单工作区、受信任团队的静态场景采集。几何精确不等于自动获得真实世界分布，也不能直接保证模型训练收益。</p>
        <ul><li>没有自动生成可行走导航网格；轨迹碰撞会明确拒绝。</li>
          <li>透明、透射和 alpha 裁切材质不在严格深度合同内。</li>
          <li>冻结场景时间，不承诺动态物体、反射虚像的几何对应。</li>
          <li>EEVEE 需要可用图形上下文；Cycles GPU 后端必须明确可用。</li>
          <li>非本机部署必须启用令牌、TLS 和运行资源隔离。</li></ul>
        <p>完整算法、实测结果、复现命令和生产部署说明均在项目 README 中。</p>
        <a className="button secondary small" href="/api/openapi.json" target="_blank" rel="noreferrer"><BookOpen size={14} />查看 API 合同<ArrowUpRight size={14} /></a>
      </section>
    </div>
    <section className="panel source-panel"><h3>研究依据与官方资料</h3><p>「加载 3D 场景并渲染回环」不是本项目首创。这里的重点是可验证的数据工程与质量约束，不宣称未经实验验证的学术首创。</p>
      <div>{[
        ['Infinite-World', '30 分钟高回访密度微调；强调时间跨度与轨迹拓扑。', 'https://arxiv.org/abs/2602.02393'],
        ['Infinite-World 官方项目', '公开方法说明与论文，区别于同名的仿真基准。', 'https://rq-wu.github.io/projects/infinite-world/'],
        ['Blender 4.5 Render Passes', '位置、深度及数据通道的定义与限制。', 'https://docs.blender.org/manual/en/4.5/render/layers/passes.html'],
        ['Unreal Movie Render Queue', '对比方案：离线电影渲染、输出与时间采样设置。', 'https://dev.epicgames.com/documentation/en-us/unreal-engine/cinematic-render-settings-and-formats-in-unreal-engine'],
        ['Infinigen', '程序化场景资产生态；导出后可接入本系统。', 'https://github.com/princeton-vl/infinigen'],
        ['Poly Haven', 'CC0 资产与独立的 API / 网站使用条款。', 'https://polyhaven.com/license'],
      ].map(([title, description, href]) => <a href={href} key={title} target="_blank" rel="noreferrer"><span><strong>{title}</strong><small>{description}</small></span><ArrowUpRight size={16} /></a>)}</div>
    </section>
  </div>
}
