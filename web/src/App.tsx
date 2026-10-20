import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ArrowRight, BookOpen, Box, ChevronRight, CircleHelp, Database, FolderOpen, Gauge,
  HardDrive, KeyRound, Layers3, Menu, Plus, Radio, RefreshCw, ScanLine, Server,
  Settings2, ShieldCheck, Upload, X,
} from 'lucide-react'
import { Alert, Logo, Modal, Spinner } from './components'
import { ApiError, connect, errorMessage, formatBytes, request, storedToken } from './api'
import { DEFAULT_RECIPE } from './types'
import type { Job, Page, Plan, Recipe, Scene, SystemInfo } from './types'
import Studio from './Studio'
import { DatasetsPage, JobDialog, JobsPage } from './Jobs'
import { ScenesPage, UploadDialog } from './Scenes'
import Method from './Method'

const NAVIGATION = [
  { page: 'studio', title: '采集工作台', icon: ScanLine },
  { page: 'scenes', title: '场景资产', icon: Box },
  { page: 'jobs', title: '渲染任务', icon: Layers3 },
  { page: 'datasets', title: '数据集', icon: Database },
] satisfies { page: Page; title: string; icon: typeof Box }[]

const TITLES: Record<Page, { heading: string; subtitle: string; eyebrow: string }> = {
  studio: { heading: '让世界回到原点。', subtitle: '从真实 3D 几何，生成精确、可验证的长程记忆数据。', eyebrow: 'LOOP-CLOSURE DATA STUDIO' },
  scenes: { heading: '每一个世界，都有出处。', subtitle: '管理原创与授权场景，为可复现的采集建立几何基础。', eyebrow: 'SCENE ASSET LIBRARY' },
  jobs: { heading: '从路径，到可用数据。', subtitle: '持久化队列、逐帧进度和明确的验收状态。每一步都有记录。', eyebrow: 'RENDER OPERATIONS' },
  datasets: { heading: '不止是视频，是证据。', subtitle: '只发布完整通过验收的采集结果；预览与正式训练数据明确分开。', eyebrow: 'VERIFIED DATASETS' },
  method: { heading: '精确，是一种设计选择。', subtitle: '数据生成原理、研究依据与不可省略的部署边界。', eyebrow: 'METHOD & DATA CONTRACT' },
}

function pageFromHash(): Page {
  const value = location.hash.slice(1)
  return value === 'scenes' || value === 'jobs' || value === 'datasets' || value === 'method' ? value : 'studio'
}

export default function App() {
  const [page, setPage] = useState<Page>(pageFromHash)
  const [mobileNav, setMobileNav] = useState(false)
  const [scenes, setScenes] = useState<Scene[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [system, setSystem] = useState<SystemInfo | null>(null)
  const [recipe, setRecipe] = useState<Recipe>(structuredClone(DEFAULT_RECIPE))
  const [plan, setPlan] = useState<Plan | null>(null)
  const [planPending, setPlanPending] = useState(false)
  const [planError, setPlanError] = useState<string | null>(null)
  const [connectionError, setConnectionError] = useState<string | null>(null)
  const [connecting, setConnecting] = useState(true)
  const [connectionDialog, setConnectionDialog] = useState(false)
  const [uploadDialog, setUploadDialog] = useState(false)
  const [confirmCapture, setConfirmCapture] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [selectedJob, setSelectedJob] = useState<Job | null>(null)
  const [toast, setToast] = useState<{ message: string; error: boolean } | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [lastRefreshed, setLastRefreshed] = useState<string | null>(null)
  const recipeFile = useRef<HTMLInputElement>(null)

  const navigate = useCallback((next: Page) => {
    location.hash = next
    setPage(next)
    setMobileNav(false)
  }, [])
  useEffect(() => {
    const listener = () => setPage(pageFromHash())
    window.addEventListener('hashchange', listener)
    return () => window.removeEventListener('hashchange', listener)
  }, [])
  const notify = useCallback((message: string, error = false) => setToast({ message, error }), [])
  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), toast.error ? 10000 : 5000)
    return () => clearTimeout(timer)
  }, [toast])

  const refresh = useCallback(async () => {
    try {
      const [nextSystem, nextScenes, recent] = await Promise.all([
        request<SystemInfo>('/system'), request<Scene[]>('/scenes'), request<Job[]>('/jobs?limit=50'),
      ])
      setSystem(nextSystem)
      setScenes(nextScenes)
      setJobs((previous) => {
        const ids = new Set(recent.map((job) => job.id))
        return [...recent, ...previous.filter((job) => !ids.has(job.id))]
      })
      setConnectionError(null)
      setLastRefreshed(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
    } catch (failure) {
      setConnectionError(errorMessage(failure))
      if (failure instanceof ApiError && failure.status === 401) setConnectionDialog(true)
    } finally {
      setConnecting(false)
    }
  }, [])

  useEffect(() => {
    let disposed = false
    const boot = async () => {
      try {
        await connect(storedToken())
        if (!disposed) await refresh()
      } catch (failure) {
        if (disposed) return
        setConnectionError(errorMessage(failure))
        if (failure instanceof ApiError && failure.status === 401) setConnectionDialog(true)
        setConnecting(false)
      }
    }
    void boot()
    const timer = setInterval(() => { if (!document.hidden) void refresh() }, 5000)
    return () => { disposed = true; clearInterval(timer) }
  }, [refresh])

  const selectedSceneExists = scenes.some((scene) => scene.id === recipe.scene_id)
  useEffect(() => {
    if (!selectedSceneExists) { setPlan(null); return }
    const controller = new AbortController()
    setPlanPending(true)
    setPlanError(null)
    const timer = setTimeout(() => {
      void request<Plan>('/plan', { method: 'POST', body: JSON.stringify(recipe), signal: controller.signal })
        .then(setPlan)
        .catch((failure: unknown) => {
          if (!controller.signal.aborted) { setPlan(null); setPlanError(errorMessage(failure)) }
        })
        .finally(() => { if (!controller.signal.aborted) setPlanPending(false) })
    }, 300)
    return () => { clearTimeout(timer); controller.abort() }
  }, [recipe, selectedSceneExists])

  const chooseScene = (scene: Scene) => {
    setRecipe((previous) => ({
      ...previous, scene_id: scene.id, name: `${scene.title} / memory loop`,
      trajectory: { ...structuredClone(DEFAULT_RECIPE.trajectory), ...scene.default_trajectory },
    }))
    navigate('studio')
  }
  const createCapture = async () => {
    if (!plan?.valid || planPending || planError) {
      notify('请先修正轨迹并等待规划检查通过。', true)
      return
    }
    setSubmitting(true)
    try {
      const job = await request<Job>('/jobs', {
        method: 'POST', body: JSON.stringify(recipe),
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      })
      setConfirmCapture(false)
      await refresh()
      setSelectedJob(job)
      notify('采集任务已入队。源资产和当前配方已固定为任务快照。')
    } catch (failure) {
      notify(errorMessage(failure), true)
    } finally { setSubmitting(false) }
  }
  const totalJobs = Object.values(system?.counts ?? {}).reduce((sum, value) => sum + value, 0)
  const hasMore = jobs.length < totalJobs
  const loadMore = async () => {
    setLoadingMore(true)
    try {
      const older = await request<Job[]>(`/jobs?limit=50&offset=${jobs.length}`)
      setJobs((previous) => {
        const ids = new Set(previous.map((job) => job.id))
        return [...previous, ...older.filter((job) => !ids.has(job.id))]
      })
    } catch (failure) { notify(errorMessage(failure), true) }
    finally { setLoadingMore(false) }
  }
  const importRecipe = async (file: File | undefined) => {
    if (!file) return
    try {
      if (file.size > 1024 * 1024) throw new Error('配方文件不能超过 1 MiB。')
      const value: unknown = JSON.parse(await file.text())
      const result = await request<Plan>('/plan', { method: 'POST', body: JSON.stringify(value) })
      setRecipe(result.recipe)
      navigate('studio')
      notify(result.valid ? '配方已验证并加载。' : '配方已加载，但运动学检查未通过。', !result.valid)
    } catch (failure) { notify(`配方导入失败：${errorMessage(failure)}`, true) }
    finally { if (recipeFile.current) recipeFile.current.value = '' }
  }
  const heading = TITLES[page]
  const activeCount = system ? ['queued', 'preflight', 'rendering', 'validating', 'packaging']
    .reduce((sum, state) => sum + (system.counts[state as keyof SystemInfo['counts']] ?? 0), 0) : 0
  const certifiedPairs = system?.statistics?.certified_pairs ?? jobs.reduce((sum, job) => sum + (job.state === 'succeeded' ? job.report?.certified_pairs ?? 0 : 0), 0)
  return <div className="app">
    {mobileNav && <button className="nav-scrim" aria-label="关闭导航" onClick={() => setMobileNav(false)} />}
    <aside className={`sidebar ${mobileNav ? 'mobile-open' : ''}`}>
      <Logo />
      <div className="workspace-picker"><span className="workspace-symbol">L</span><span><strong>Local workspace</strong><small>精确世界数据工作区</small></span><ChevronRight size={14} /></div>
      <span className="nav-section-label">工作空间</span>
      <nav aria-label="主导航">{NAVIGATION.map(({ page: target, title, icon: Icon }) =>
        <button key={target} aria-label={title} className={`nav-item ${page === target ? 'active' : ''}`} aria-current={page === target ? 'page' : undefined} onClick={() => navigate(target)}>
          <Icon size={18} strokeWidth={1.7} /><span>{title}</span>{target === 'jobs' && activeCount > 0 && <small>{activeCount}</small>}
        </button>)}
      </nav>
      <div className="sidebar-bottom">
        <div className="worker-card"><div><span className={`connection-dot ${system?.worker_online && !connectionError ? 'online' : ''}`} /><strong>
          {connecting ? '正在连接节点' : connectionError ? '服务连接中断' : system?.worker_online ? '渲染节点在线' : '渲染节点离线'}</strong></div>
          <p>{system ? `${system.render_threads} 渲染线程 · 单任务隔离` : '等待真实服务状态'}</p><small>先验证几何，再消耗算力。</small>
        </div>
        <button aria-label="方法与数据合同" className={`nav-item ${page === 'method' ? 'active' : ''}`} onClick={() => navigate('method')} aria-current={page === 'method' ? 'page' : undefined}><BookOpen size={18} /><span>方法与数据合同</span><ArrowRight size={14} /></button>
        <div className="sidebar-footer"><span>LocusForge <small>v{system?.version ?? '0.2.0'}</small></span><span>SELF-HOSTED</span></div>
      </div>
    </aside>
    <div className="main-shell">
      <header className="topbar"><div className="breadcrumbs"><button className="icon-button mobile-menu" aria-label="打开导航" onClick={() => setMobileNav(true)}><Menu size={20} /></button>
        <FolderOpen size={15} /><span>工作空间</span><ChevronRight size={13} /><strong>{NAVIGATION.find((item) => item.page === page)?.title ?? '方法与数据合同'}</strong></div>
        <div className="topbar-actions"><span className="privacy-label"><ShieldCheck size={14} />{system?.authentication === 'token' ? '令牌保护' : '本地优先'}</span>
          <button className="icon-button" aria-label="刷新工作区" onClick={() => void refresh()}><RefreshCw size={16} /></button>
          <button className="icon-button" aria-label="连接设置" onClick={() => setConnectionDialog(true)}><Settings2 size={17} /></button>
          <span className="user-mark" aria-label="本地工作区">LF</span></div></header>
      <main className="main-content">
        {connectionError && <div className="connection-banner"><Alert>连接异常：{connectionError}{lastRefreshed && `。以下数据停留在 ${lastRefreshed}。`}
          <button className="text-button" onClick={() => void refresh()}>重试连接<RefreshCw size={13} /></button></Alert></div>}
        <div className="page-heading"><div><span className="eyebrow">{heading.eyebrow}</span><h1>{heading.heading}</h1><p>{heading.subtitle}</p></div>
          <div className="heading-actions">{page === 'studio' || page === 'scenes'
            ? <button className="button secondary" onClick={() => setUploadDialog(true)}><Upload size={15} />导入场景</button>
            : page !== 'method' && <button className="button primary" onClick={() => navigate('studio')}><Plus size={16} />新建采集</button>}
            {page === 'studio' && <button className="button help-button" aria-label="了解数据生成方法" onClick={() => navigate('method')}><CircleHelp size={17} /></button>}</div>
        </div>
        {page === 'studio' && <div className="stats-grid">
          <Stat icon={<Box size={18} />} title="可用场景" value={system ? String(system.scene_count).padStart(2, '0') : '—'} note="已入库 · 来源可追溯" />
          <Stat icon={<ScanLine size={18} />} title="完成采集" value={system ? String(system.counts.succeeded ?? 0).padStart(2, '0') : '—'} note="全流程验收通过" />
          <Stat icon={<Gauge size={18} />} title="认证回访" value={system ? String(certifiedPairs).padStart(2, '0') : '—'} note="双向深度重投影验证" />
          <Stat icon={<HardDrive size={18} />} title="可用存储" value={system ? formatBytes(system.free_disk_bytes) : '—'} note="提交前自动检查容量" />
        </div>}
        {connecting && !system ? <div className="boot-state panel"><Spinner label="正在连接真实工作区…" /><p>读取场景资产、渲染队列与存储状态。</p></div> :
          page === 'studio' ? <Studio scenes={scenes} recipe={recipe} setRecipe={setRecipe} plan={plan} planPending={planPending}
            planError={planError} system={system} jobs={jobs} onScene={chooseScene} onSubmit={() => setConfirmCapture(true)}
            submitting={submitting} onJob={setSelectedJob} onJobs={() => navigate('jobs')} onImportRecipe={() => recipeFile.current?.click()} /> :
          page === 'scenes' ? <ScenesPage scenes={scenes} onChoose={chooseScene} onUpload={() => setUploadDialog(true)} /> :
          page === 'jobs' ? <JobsPage jobs={jobs} onSelect={setSelectedJob} loadMore={() => void loadMore()} hasMore={hasMore} loadingMore={loadingMore} /> :
          page === 'datasets' ? <DatasetsPage jobs={jobs} onSelect={setSelectedJob} loadMore={() => void loadMore()} hasMore={hasMore} /> :
          <Method />}
        <footer className="page-footer"><span><ShieldCheck size={12} />精确几何 · 明确来源 · 可验证回环</span><span>BUILT FOR WORLD MODELS</span></footer>
      </main>
    </div>
    <input ref={recipeFile} type="file" accept=".json" hidden aria-label="导入 JSON 配方" onChange={(event) => void importRecipe(event.target.files?.[0])} />
    {uploadDialog && <UploadDialog maxMb={system?.max_asset_mb ?? 1024} onClose={() => setUploadDialog(false)} onComplete={(scene) => {
      setUploadDialog(false)
      setScenes((previous) => [...previous, scene])
      chooseScene(scene)
      void refresh()
      notify('场景校验通过并已入库。请在三维视口检查比例与路径，再开始采集。')
    }} />}
    {selectedJob && <JobDialog job={selectedJob} onClose={() => setSelectedJob(null)} onChange={() => void refresh()} onReuse={(job) => {
      void request<Plan>('/plan', { method: 'POST', body: JSON.stringify(job.recipe) }).then((result) => {
        setRecipe({ ...result.recipe, name: `${job.name.slice(0, 88)} / copy` })
        setSelectedJob(null)
        navigate('studio')
        notify('已复制为可编辑配方，原任务和数据保持不变。')
      }).catch((failure: unknown) => notify(errorMessage(failure), true))
    }} />}
    {connectionDialog && <ConnectionDialog system={system} onClose={() => setConnectionDialog(false)} onConnected={() => {
      setConnectionDialog(false); void refresh(); notify('连接已验证。令牌仅保存在当前浏览器会话。')
    }} />}
    {confirmCapture && <Modal title="开始一次可验证的采集" eyebrow="RENDER CONFIRMATION" onClose={() => setConfirmCapture(false)} busy={submitting}>
      <div className="modal-body"><p className="modal-description">任务会冻结当前配方与源资产指纹。后续修改工作台不会影响正在执行的任务。</p>
        <div className="confirmation-summary"><strong>{recipe.name}</strong><span>{scenes.find((scene) => scene.id === recipe.scene_id)?.title}</span>
          <div><span>{recipe.capture.width} × {recipe.capture.height}</span><span>{recipe.capture.duration_seconds}s / {recipe.capture.fps} fps</span><span>{recipe.capture.samples} spp / {recipe.capture.device}</span></div></div>
        <dl className="confirmation-facts"><div><dt>逐帧真实渲染</dt><dd>{recipe.capture.fps * recipe.capture.duration_seconds} 帧</dd></div>
          <div><dt>未压缩 RGB-D 量级</dt><dd>{plan ? formatBytes(plan.stats.raw_rgb_depth_bytes) : '—'}</dd></div>
          <div><dt>数据用途</dt><dd>{recipe.capture.purpose === 'preview' ? '预览验证，不标记为可训练' : '正式数据，通过验收后发布'}</dd></div></dl>
        <Alert tone="info">碰撞或可见重叠不达标时会明确拒绝。耗时依赖场景与硬件；未进行实测的配置不提供虚构 ETA。</Alert>
        {!system?.worker_online && <Alert tone="warning">渲染节点当前离线。任务可以入队，但需要启动工作进程后才会执行。</Alert>}
      </div><footer className="modal-footer"><button className="button secondary" onClick={() => setConfirmCapture(false)} disabled={submitting}>继续调整</button>
        <button className="button primary" disabled={submitting || planPending || !plan?.valid} onClick={() => void createCapture()}>{submitting ? <Spinner label="提交中…" /> : <><Radio size={15} />确认加入队列</>}</button></footer>
    </Modal>}
    {toast && <div className={`toast ${toast.error ? 'toast-error' : ''}`} role={toast.error ? 'alert' : 'status'}><ShieldCheck size={18} /><span>{toast.message}</span><button aria-label="关闭提示" onClick={() => setToast(null)}><X size={15} /></button></div>}
  </div>
}

function Stat({ icon, title, value, note }: { icon: React.ReactNode; title: string; value: string; note: string }) {
  return <article className="stat-card"><div><span>{title}</span>{icon}</div><strong>{value}</strong><small>{note}</small></article>
}

function ConnectionDialog({ system, onClose, onConnected }: {
  system: SystemInfo | null; onClose: () => void; onConnected: () => void
}) {
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return <Modal title="工作区连接" eyebrow="SELF-HOSTED, BY DESIGN" onClose={onClose} busy={busy}>
    <form onSubmit={(event) => {
      event.preventDefault(); setBusy(true); setError(null)
      void connect(token).then(onConnected).catch((failure: unknown) => setError(errorMessage(failure))).finally(() => setBusy(false))
    }}>
      <div className="modal-body"><div className="connection-server"><Server size={24} /><div><strong>{location.origin}</strong><span>{system?.authentication === 'token' ? 'API 令牌保护已开启' : '本机回环访问 · 无需令牌'}</span></div></div>
        {error && <Alert>{error}</Alert>}
        <label className="field"><span><KeyRound size={13} />API 令牌</span><input type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} placeholder="本机无需鉴权时留空" /></label>
        <p className="field-help">令牌只保存在 sessionStorage。验证成功后，HttpOnly 会话 Cookie 支持原生流式下载，不把令牌放进 URL。</p>
        <dl className="asset-facts"><div><dt>Blender 可执行文件</dt><dd>{system?.blender_available ? '已找到' : '未连接 / 未配置'}</dd></div>
          <div><dt>渲染工作进程</dt><dd>{system?.worker_online ? '在线' : '离线 / 未连接'}</dd></div><div><dt>运行边界</dt><dd>单工作区 · 受信任操作员</dd></div></dl>
      </div>
      <footer className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={busy}>关闭</button><button className="button primary" disabled={busy}>{busy ? <Spinner label="验证连接…" /> : '验证并连接'}</button></footer>
    </form>
  </Modal>
}
