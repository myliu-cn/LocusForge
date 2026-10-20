import { useEffect, useRef, useState } from 'react'
import {
  ArrowDownToLine, ArrowRight, BadgeCheck, Check, ChevronRight, Clock3, Copy,
  FileJson, Film, Layers3, Play, RefreshCw, Search, Square, Terminal, X,
} from 'lucide-react'
import { Badge, Alert, Empty, Modal, Spinner } from './components'
import { errorMessage, formatBytes, formatTime, request } from './api'
import type { Job, Loops, LoopPair } from './types'
import { CHECK_LABELS, STATE_LABELS, TERMINAL as TERMINAL_STATES } from './types'

function dateLabel(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value))
}

export function JobTable({ jobs, onSelect, compact = false }: {
  jobs: Job[]; onSelect: (job: Job) => void; compact?: boolean
}) {
  if (!jobs.length) return <Empty icon={<Film size={24} />} title="第一段回环，从这里开始">
    设置场景与轨迹后加入队列。系统不会填充模拟任务或虚构采集统计。
  </Empty>
  return <div className="table-scroll"><table className={`job-table ${compact ? 'compact' : ''}`}>
    <thead><tr><th>任务 / 场景</th><th>输出规格</th><th>状态</th>{!compact && <th>创建时间</th>}<th><span className="sr-only">详情</span></th></tr></thead>
    <tbody>{jobs.map((job) => <tr key={job.id} onClick={() => onSelect(job)}>
      <td><button className="job-name" onClick={(event) => { event.stopPropagation(); onSelect(job) }}>
        <span className={`job-icon ${job.state === 'succeeded' ? 'done' : ''}`}><Film size={17} /></span>
        <span><strong>{job.name}</strong><small>{job.scene.title}<span>·</span>{job.id.slice(0, 8)}</small></span>
      </button></td>
      <td><span className="mono">{job.recipe.capture.width} × {job.recipe.capture.height}</span>
        <small>{job.recipe.capture.duration_seconds}s <span>·</span> {job.recipe.capture.fps} fps <span>·</span> {job.recipe.capture.purpose === 'preview' ? '预览' : '数据集'}</small></td>
      <td><Badge state={job.state} />{!TERMINAL_STATES.includes(job.state) && <div className="mini-progress">
        <span style={{ width: `${Math.max(0, Math.min(100, job.progress * 100))}%` }} />
      </div>}</td>
      {!compact && <td className="date-cell">{dateLabel(job.created_at)}</td>}
      <td><button className="icon-button" aria-label={`查看 ${job.name}`} onClick={(event) => { event.stopPropagation(); onSelect(job) }}><ChevronRight size={16} /></button></td>
    </tr>)}</tbody>
  </table></div>
}

export function JobsPage({ jobs, onSelect, loadMore, hasMore, loadingMore }: {
  jobs: Job[]; onSelect: (job: Job) => void; loadMore: () => void; hasMore: boolean; loadingMore: boolean
}) {
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const visible = jobs.filter((job) => {
    const matches = `${job.name} ${job.scene.title} ${job.id}`.toLowerCase().includes(query.toLowerCase())
    if (filter === 'active') return matches && !TERMINAL_STATES.includes(job.state)
    if (filter === 'succeeded') return matches && job.state === 'succeeded'
    if (filter === 'issues') return matches && ['failed', 'rejected', 'cancelled'].includes(job.state)
    return matches
  })
  return <div className="panel list-panel">
    <div className="list-toolbar"><div className="filter-tabs">
      {[['all', '全部任务'], ['active', '进行中'], ['succeeded', '已完成'], ['issues', '待处理']].map(([key, title]) =>
        <button key={key} className={filter === key ? 'active' : ''} onClick={() => setFilter(key)}>{title}</button>)}
    </div><label className="search-field"><Search size={16} /><input placeholder="搜索任务、场景或 ID" aria-label="搜索任务" value={query} onChange={(event) => setQuery(event.target.value)} /></label></div>
    <JobTable jobs={visible} onSelect={onSelect} />
    <div className="list-footer"><span>已加载 {jobs.length} 条 · 单工作区串行渲染，避免显存抢占</span>
      {hasMore && <button className="button small secondary" disabled={loadingMore} onClick={loadMore}>{loadingMore ? <Spinner /> : '加载更早任务'}</button>}
    </div>
  </div>
}

export function DatasetsPage({ jobs, onSelect, loadMore, hasMore }: {
  jobs: Job[]; onSelect: (job: Job) => void; loadMore: () => void; hasMore: boolean
}) {
  const [filter, setFilter] = useState('all')
  const datasets = jobs.filter((job) => job.state === 'succeeded' && (filter === 'all' ||
    (filter === 'training' ? job.report?.training_eligible : !job.report?.training_eligible)))
  return <div className="datasets-page">
    <div className="dataset-intro">
      <div><BadgeCheck size={23} /><span><strong>每次导出，都有证据。</strong><small>原始 RGB · 米制深度 · 相机矩阵 · SE(3) 动作 · 回环证书 · SHA-256</small></span></div>
      <span className="outline-tag">DATA CONTRACT v1.0</span>
    </div>
    <div className="list-toolbar"><div className="filter-tabs">
      {[['all', '全部导出'], ['training', '可训练数据'], ['preview', '仅预览验证']].map(([key, title]) =>
        <button className={filter === key ? 'active' : ''} key={key} onClick={() => setFilter(key)}>{title}</button>)}
    </div><span className="text-muted">{datasets.length} 个已加载的验收结果</span></div>
    {!datasets.length ? <div className="panel"><Empty icon={<Layers3 size={25} />} title="还没有符合筛选条件的数据">
      只有完整通过几何、时钟、深度和视频解码验收的任务，才会在这里发布。
    </Empty></div> : <div className="dataset-grid">{datasets.map((job) => <article className="dataset-card panel" key={job.id}>
      <button className="dataset-image" onClick={() => onSelect(job)} aria-label={`预览 ${job.name}`}>
        <img src={`/api/jobs/${job.id}/files/rgb/000000.png`} alt={`${job.scene.title}的实际采集首帧`} loading="lazy" />
        <span className="play-circle"><Play size={21} fill="currentColor" /></span>
        <span className="duration-tag">{formatTime(job.recipe.capture.duration_seconds)}</span>
        <span className={`dataset-type ${job.report?.training_eligible ? 'training' : ''}`}>{job.report?.training_eligible ? 'TRAINING READY' : 'PREVIEW ONLY'}</span>
      </button>
      <div className="dataset-body"><span className="dataset-scene">{job.scene.title} <span>·</span> {job.scene.split.toUpperCase()}</span>
        <h3>{job.name}</h3>
        <div className="dataset-spec"><span>{job.recipe.capture.width} × {job.recipe.capture.height}</span><span>{job.recipe.capture.fps} fps</span><span>{job.completed_frames} 帧</span></div>
        <div className="dataset-proof"><BadgeCheck size={15} /><strong>{job.report?.certified_pairs ?? 0} 个认证回访</strong>
          <span>{job.report?.archive ? formatBytes(job.report.archive.bytes) : '—'}</span></div>
        <footer><button className="text-button" onClick={() => onSelect(job)}>查看证书<ArrowRight size={13} /></button>
          <a className="button small secondary" href={`/api/jobs/${job.id}/download`}><ArrowDownToLine size={14} />下载数据</a></footer>
      </div>
    </article>)}</div>}
    {hasMore && <div className="center-actions"><button className="button secondary" onClick={loadMore}>加载更早采集记录</button></div>}
  </div>
}

function PairInspector({ job, pair }: { job: Job; pair: LoopPair }) {
  const source = String(pair.source_frame).padStart(6, '0')
  const target = String(pair.target_frame).padStart(6, '0')
  const departure = String(pair.departure_frame).padStart(6, '0')
  return <div className="pair-inspector">
    <div className="pair-images">
      {[[source, '首次观察', pair.source_frame], [departure, '最远离场', pair.departure_frame], [target, '重新回访', pair.target_frame]].map(([frame, title, index]) =>
        <figure key={String(title)}><img src={`/api/jobs/${job.id}/files/rgb/${frame}.png`} alt={String(title)} />
          <figcaption><strong>{title}</strong><span>F{index} · {formatTime(Number(index) / job.recipe.capture.fps)}</span></figcaption></figure>)}
    </div>
    <div className="pair-metrics">
      <div><span>回访间隔</span><strong>{pair.gap_seconds.toFixed(2)} <small>s</small></strong></div>
      <div><span>双向重叠下界</span><strong>{(pair.overlap_min * 100).toFixed(1)} <small>%</small></strong></div>
      <div><span>最大离场距离</span><strong>{pair.excursion_m.toFixed(2)} <small>m</small></strong></div>
      <div><span>离场时重叠</span><strong>{(pair.departure_overlap * 100).toFixed(1)} <small>%</small></strong></div>
    </div>
    <div className="pair-pose-proof"><Check size={16} /><span>{pair.kind === 'exact' ? '精确 SE(3) 回环' : '近邻几何回环'}</span>
      <code>Δp {pair.position_error_m.toExponential(2)} m</code><code>ΔR {pair.rotation_error_deg.toExponential(2)}°</code></div>
    <p className="field-help">可见重叠来自米制深度的双向重投影，并检查目标深度遮挡。它不是 CLIP 相似度，也不是只按位置距离召回。</p>
  </div>
}

export function JobDialog({ job: initial, onClose, onChange, onReuse }: {
  job: Job; onClose: () => void; onChange: () => void; onReuse: (job: Job) => void
}) {
  const [job, setJob] = useState(initial)
  const [tab, setTab] = useState<'overview' | 'loops' | 'checks'>('overview')
  const [loops, setLoops] = useState<Loops | null>(null)
  const [pairIndex, setPairIndex] = useState(0)
  const [log, setLog] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const video = useRef<HTMLVideoElement>(null)
  const hasRenderer = Boolean(job.report?.renderer)
  useEffect(() => {
    const controller = new AbortController()
    const refresh = () => {
      void request<Job>(`/jobs/${initial.id}`, { signal: controller.signal }).then(setJob).catch((failure: unknown) => {
        if (!(failure instanceof DOMException && failure.name === 'AbortError')) setError(errorMessage(failure))
      })
    }
    refresh()
    const timer = setInterval(refresh, 3000)
    return () => { controller.abort(); clearInterval(timer) }
  }, [initial.id])
  useEffect(() => {
    if (!hasRenderer) return
    const controller = new AbortController()
    void request<Loops>(`/jobs/${job.id}/loops`, { signal: controller.signal }).then(setLoops).catch((failure: unknown) => {
      if (!(failure instanceof DOMException && failure.name === 'AbortError')) setError(errorMessage(failure))
    })
    return () => controller.abort()
  }, [job.id, job.state, hasRenderer])
  const action = async (kind: 'cancel' | 'retry') => {
    setBusy(true); setError(null)
    try {
      await request<Job>(`/jobs/${job.id}/${kind}`, { method: 'POST' })
      onChange()
      if (kind === 'retry') onClose()
      else setJob(await request<Job>(`/jobs/${job.id}`))
    } catch (failure) {
      setError(errorMessage(failure))
    } finally { setBusy(false) }
  }
  return <Modal title={job.name} eyebrow={`CAPTURE ${job.id.slice(0, 12).toUpperCase()}`} wide onClose={onClose} busy={busy}>
    <div className="job-dialog-body">
      <div className="job-detail-header"><Badge state={job.state} /><span>{job.scene.title}</span>
        <span>{job.recipe.capture.width} × {job.recipe.capture.height} / {job.recipe.capture.fps} fps</span>
        <span>{job.recipe.capture.device}</span></div>
      {error && <Alert>{error}</Alert>}
      {job.error && <Alert tone={job.state === 'cancelled' ? 'info' : 'error'}>{job.error}</Alert>}
      <div className="filter-tabs dialog-tabs">
        <button className={tab === 'overview' ? 'active' : ''} onClick={() => setTab('overview')}><Film size={14} />采集预览</button>
        <button className={tab === 'loops' ? 'active' : ''} onClick={() => setTab('loops')}><BadgeCheck size={14} />回环证书</button>
        <button className={tab === 'checks' ? 'active' : ''} onClick={() => setTab('checks')}><Terminal size={14} />验收与日志</button>
      </div>
      {tab === 'overview' && <>
        {job.state === 'succeeded' ? <video ref={video} className="capture-video" controls preload="metadata" playsInline
          src={`/api/jobs/${job.id}/files/video.mp4`} poster={`/api/jobs/${job.id}/files/rgb/000000.png`}>
          当前浏览器无法播放 H.264，请下载数据后查看。
        </video> : job.completed_frames > 0 ? <img className="capture-video" alt="任务已经完成的最新真实帧"
          src={`/api/jobs/${job.id}/files/rgb/${String(job.completed_frames - 1).padStart(6, '0')}.png`} /> :
          <Empty icon={<Clock3 size={25} />} title={STATE_LABELS[job.state]}>尚无已完成的 RGB 帧。预检阶段不会伪造视频预览。</Empty>}
        <div className="capture-progress"><span>{job.completed_frames} / {job.recipe.capture.fps * job.recipe.capture.duration_seconds} 帧</span>
          <span>{job.report?.renderer ? `实测 ${job.report.renderer.median_frame_seconds.toFixed(2)} 秒 / 帧（中位数）` : '耗时将在真实渲染后统计'}</span></div>
        <div className="progress-track"><span style={{ width: `${job.progress * 100}%` }} /></div>
        <div className="detail-facts"><div><span>场景数据划分</span><strong>{job.scene.split} · 场景族隔离</strong></div>
          <div><span>训练资格</span><strong>{job.report?.training_eligible ? '全部验收通过 · 可训练' : job.recipe.capture.purpose === 'preview' ? '仅预览 · 不用于正式训练' : '等待验收 / 未通过'}</strong></div>
          <div><span>源资产指纹</span><code title={job.scene.asset_sha256}>{job.scene.asset_sha256.slice(0, 24)}…</code></div>
          <div><span>创建时间</span><strong>{dateLabel(job.created_at)}</strong></div></div>
        {job.state === 'succeeded' && <p className="field-help">MP4 是有损审阅代理。训练使用压缩包中的无损 PNG 和 float32 深度，不从 MP4 反推标签。</p>}
      </>}
      {tab === 'loops' && <>
        {loops?.pairs.length ? <>
          <div className="pair-select"><span>{loops.pairs.length} 个已认证回访</span><select aria-label="选择回环帧对" value={pairIndex} onChange={(event) => setPairIndex(Number(event.target.value))}>
            {loops.pairs.map((pair, index) => <option value={index} key={`${pair.source_frame}-${pair.target_frame}`}>F{pair.source_frame} → F{pair.target_frame} · {pair.gap_seconds.toFixed(2)}s</option>)}
          </select></div><PairInspector job={job} pair={loops.pairs[pairIndex] ?? loops.pairs[0]} />
        </> : <Empty icon={<BadgeCheck size={25} />} title="尚无通过认证的回访">
          {loops ? `${loops.rejected_candidates.length} 个候选未达到离场、位姿或可见重叠门槛。请检查验收日志并调整配方。` : '任务完成真实几何与深度验收后，此处显示可复核的帧对证据。'}
        </Empty>}
        {loops && <a className="text-button" href={`/api/jobs/${job.id}/files/loops.json`} download><FileJson size={15} />下载完整回环标签与拒绝原因</a>}
      </>}
      {tab === 'checks' && <>
        {job.report ? <div className="check-list">{job.report.checks.map((check) => <div key={check.name}>
          <span className={check.passed ? 'check-pass' : 'check-fail'}>{check.passed ? <Check size={15} /> : <X size={15} />}</span>
          <span>{CHECK_LABELS[check.name] ?? check.name}</span><code title={JSON.stringify(check.measured)}>{typeof check.measured === 'number'
            ? Number(check.measured.toPrecision(6)).toString() : JSON.stringify(check.measured)}</code>
          <small>{check.passed ? '通过' : '未通过'}</small>
        </div>)}</div> : <Alert tone="info">尚未到达验收阶段。正在执行的任务不会提前获得合格证书。</Alert>}
        <div className="event-list">{job.events?.map((event, index) => <div key={index}><i /><time>{dateLabel(event.timestamp)}</time><span>{event.message}</span></div>)}</div>
        <button className="button secondary small" onClick={() => {
          void request<{ log: string }>(`/jobs/${job.id}/log`).then((value) => setLog(value.log)).catch((failure: unknown) => setError(errorMessage(failure)))
        }}><Terminal size={14} />{log ? '刷新渲染日志' : '查看渲染日志'}</button>
        {log && <pre className="log-output">{log}</pre>}
      </>}
    </div>
    <footer className="modal-footer">
      <button className="button secondary" onClick={() => onReuse(job)}><Copy size={15} />复制为新配方</button>
      <div>{!TERMINAL_STATES.includes(job.state) && <button className="button danger" disabled={busy || job.cancel_requested} onClick={() => void action('cancel')}><Square size={14} />{job.cancel_requested ? '正在取消…' : '取消任务'}</button>}
        {['failed', 'cancelled', 'rejected'].includes(job.state) && <button className="button secondary" disabled={busy} onClick={() => void action('retry')}><RefreshCw size={15} />新建重试</button>}
        {job.state === 'succeeded' && <>
          <a className="button secondary" href={`/api/jobs/${job.id}/files/manifest.json`} download><FileJson size={15} />Manifest</a>
          <a className="button primary" href={`/api/jobs/${job.id}/download`}><ArrowDownToLine size={15} />下载数据包</a>
        </>}</div>
    </footer>
  </Modal>
}
