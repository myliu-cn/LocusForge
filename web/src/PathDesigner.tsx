import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import {
  ArrowDown, ArrowRight, ArrowUp, BookOpen, Check, ChevronLeft, ChevronRight, CirclePlus,
  Compass, Copy, Crosshair, Eye, FolderOpen, LockKeyhole, Pause, Play, Plus, RotateCcw,
  Route, Save, Sparkles, Trash2,
} from 'lucide-react'
import { Alert, Modal, NumberField, SpatialPointsField, Spinner } from './components'
import { errorMessage, formatTime, request, saveJson } from './api'
import { CHECK_LABELS } from './types'
import type { MotionProposal, MotionSegment, OrientationKeyframe, Plan, PlannerCapabilities, Recipe, SavedPath, Scene, Trajectory, Vec3 } from './types'
import { insertOrientationKey, newPathKind, PATH_NAMES, recenterTrajectory } from './pathTools'

const Viewport = lazy(() => import('./Viewport'))
const DIRECTIONS = { forward: '前进', backward: '后退', left: '左移', right: '右移', up: '上升', down: '下降' } as const

function MotionBlocks({ trajectory, onChange }: { trajectory: Trajectory; onChange: (patch: Partial<Trajectory>) => void }) {
  const segments = trajectory.segments
  const update = (index: number, segment: MotionSegment) => onChange({ segments: segments.map((item, i) => i === index ? segment : item) })
  const move = (index: number, offset: number) => {
    const result = [...segments]
    const next = index + offset
    if (next < 0 || next >= result.length) return
    ;[result[index], result[next]] = [result[next], result[index]]
    onChange({ segments: result })
  }
  return <>
    <div className="field-grid triple">{(['X', 'Y', 'Z'] as const).map((axis, index) => <NumberField key={axis}
      label={`起点 ${axis}`} value={trajectory.start[index]} min={-10000} max={10000} step={0.1} unit="m"
      onChange={(value) => { const start: Vec3 = [...trajectory.start]; start[index] = value; onChange({ start }) }} />)}</div>
    <NumberField label="初始导航朝向 · 0° = +X，右转为正" value={trajectory.start_yaw_deg} min={-180} max={180} step={0.1} unit="°" onChange={(start_yaw_deg) => onChange({ start_yaw_deg })} />
    <div className="motion-block-list">{segments.map((segment, index) => <article className="motion-block" key={index}>
      <header><span>{String(index + 1).padStart(2, '0')}</span><strong>{segment.kind === 'move' ? '平移' : segment.kind === 'arc' ? '圆弧' : segment.kind === 'turn' ? '原地转向' : '停留'}</strong>
        <button type="button" className="icon-button" aria-label={`动作 ${index + 1} 上移`} disabled={index === 0} onClick={() => move(index, -1)}><ArrowUp size={13} /></button>
        <button type="button" className="icon-button" aria-label={`动作 ${index + 1} 下移`} disabled={index === segments.length - 1} onClick={() => move(index, 1)}><ArrowDown size={13} /></button>
        <button type="button" className="icon-button" aria-label={`删除动作 ${index + 1}`} disabled={segments.length === 1} onClick={() => onChange({ segments: segments.filter((_, i) => i !== index) })}><Trash2 size={13} /></button>
      </header>
      <div className="field-grid">
        {segment.kind === 'move' && <>
          <label className="field"><span>移动方向</span><select aria-label={`动作 ${index + 1} 方向`} value={segment.direction} onChange={(event) => {
            const value = event.target.value
            if (value === 'forward' || value === 'backward' || value === 'left' || value === 'right' || value === 'up' || value === 'down') update(index, { ...segment, direction: value })
          }}>{Object.entries(DIRECTIONS).map(([key, title]) => <option key={key} value={key}>{title}</option>)}</select></label>
          <NumberField label={`动作 ${index + 1} 距离`} value={segment.distance_m} min={0.01} max={500} step={0.1} unit="m" onChange={(distance_m) => update(index, { ...segment, distance_m })} />
        </>}
        {segment.kind === 'arc' && <>
          <NumberField label={`动作 ${index + 1} 半径`} value={segment.radius_m} min={0.25} max={500} step={0.25} unit="m" onChange={(radius_m) => update(index, { ...segment, radius_m })} />
          <NumberField label={`动作 ${index + 1} 圆弧角`} value={segment.angle_deg} min={-360} max={360} step={0.1} unit="°" onChange={(angle_deg) => update(index, { ...segment, angle_deg })} />
        </>}
        {segment.kind === 'turn' && <NumberField label={`动作 ${index + 1} 转向角`} value={segment.yaw_deg} min={-360} max={360} step={0.1} unit="°" onChange={(yaw_deg) => update(index, { ...segment, yaw_deg })} />}
        <NumberField label={`动作 ${index + 1} 时间权重`} value={segment.weight} min={0.1} max={100} step={0.1} onChange={(weight) => update(index, { ...segment, weight })} />
      </div>
    </article>)}</div>
    <div className="block-add-actions">
      <button type="button" className="button secondary small" disabled={segments.length >= 64} onClick={() => onChange({ segments: [...segments, { kind: 'move', direction: 'forward', distance_m: 3, weight: 1 }] })}><Plus size={12} />平移</button>
      <button type="button" className="button secondary small" disabled={segments.length >= 64} onClick={() => onChange({ segments: [...segments, { kind: 'arc', radius_m: 3, angle_deg: 90, weight: 2 }] })}><Plus size={12} />圆弧</button>
      <button type="button" className="button secondary small" disabled={segments.length >= 64} onClick={() => onChange({ segments: [...segments, { kind: 'turn', yaw_deg: 90, weight: 1 }] })}><Plus size={12} />转向</button>
      <button type="button" className="button secondary small" disabled={segments.length >= 64} onClick={() => onChange({ segments: [...segments, { kind: 'hold', weight: 1 }] })}><Plus size={12} />停留</button>
    </div>
    <p className="field-help">平移相对导航朝向；左/右侧移不改变导航轴。镜头默认朝向实际运动方向，拐角处插入可见的原地平滑转向。上下升降保留最近水平朝向。圆弧／转向：右为正，左为负。</p>
  </>
}

function LanguageMotion({ trajectory, onAdopt, onBusy }: {
  trajectory: Trajectory; onAdopt: (result: MotionProposal) => void; onBusy: (value: boolean) => void
}) {
  const [text, setText] = useState('前进4米；右移3米；后退4米；左移3米；闭合；时长40秒')
  const [provider, setProvider] = useState<'local' | 'llm'>('local')
  const [consent, setConsent] = useState(false)
  const [capabilities, setCapabilities] = useState<PlannerCapabilities | null>(null)
  const [result, setResult] = useState<MotionProposal | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    void request<PlannerCapabilities>('/planner/capabilities', { signal: controller.signal }).then(setCapabilities).catch((failure: unknown) => {
      if (!controller.signal.aborted) setError(errorMessage(failure))
    })
    return () => controller.abort()
  }, [])
  const parse = async () => {
    setBusy(true); onBusy(true); setError(null); setResult(null)
    try {
      setResult(await request<MotionProposal>('/planner/parse', {
        method: 'POST', body: JSON.stringify({ text, provider, allow_external: consent,
          start: trajectory.start, start_yaw_deg: trajectory.start_yaw_deg }),
      }))
    } catch (failure) { setError(errorMessage(failure)) }
    finally { setBusy(false); onBusy(false) }
  }
  return <div className="language-motion">
    <div className="choice-group"><button type="button" disabled={busy} className={provider === 'local' ? 'selected' : ''} onClick={() => { setProvider('local'); setResult(null) }}>本地语法 · 无费用</button>
      <button type="button" disabled={busy} className={provider === 'llm' ? 'selected' : ''} onClick={() => { setProvider('llm'); setResult(null) }}>LLM · 自然语言</button></div>
    <label className="field"><span>描述你要经过的路径，不填写密钥或私密信息</span><textarea aria-label="运动路径描述" rows={5} value={text} maxLength={4000} disabled={busy} onChange={(event) => { setText(event.target.value); setResult(null) }} /></label>
    <div className="language-examples">
      <button type="button" disabled={busy} onClick={() => { setText('圆形；半径5.5米；高度1.7米；时长30秒'); setResult(null) }}>圆形示例</button>
      <button type="button" disabled={busy} onClick={() => { setText('前进4米；右移3米；后退4米；左移3米；闭合；时长40秒'); setResult(null) }}>动作组合</button>
      <button type="button" disabled={busy} onClick={() => { setText('右弧3米360度；闭合；时长30秒'); setResult(null) }}>圆弧积木</button>
    </div>
    {provider === 'local' ? <p className="field-help">使用阿拉伯数字，分号分隔。支持圆形、8字形、半径、高度、时长、圈数、前进／后退／左移／右移／上升／下降、左转／右转、左弧／右弧和停留。未识别内容会拒绝，不会被丢弃。</p> :
      <div className="llm-consent">
        <p><LockKeyhole size={14} />{capabilities?.llm_configured ? `${capabilities.llm_provider} · ${capabilities.llm_model}` : 'LLM 未配置：请在服务器设置 API 密钥和模型。'}</p>
        <span>只发送输入文字和起始坐标，不发送 GLB、图像、历史任务或源代码。每日上限 {capabilities?.daily_limit ?? '—'} 次，失败不自动付费重试。</span>
        <label><input type="checkbox" checked={consent} disabled={busy || !capabilities?.llm_configured} onChange={(event) => setConsent(event.target.checked)} />我允许将本次运动描述发送给上述服务。</label>
      </div>}
    {error && <Alert>{error}</Alert>}
    <button type="button" className="button primary full" disabled={busy || !text.trim() || (provider === 'llm' && (!consent || !capabilities?.llm_configured))} onClick={() => void parse()}>
      {busy ? <Spinner label="解析运动草稿…" /> : <><Sparkles size={14} />解析运动路径</>}</button>
    {result && <div className="proposal-card"><strong>{result.title}</strong><p>{result.summary}</p>
      <span>{PATH_NAMES[result.trajectory.kind]} · {result.duration_seconds}s · {result.trajectory.closed ? '闭合设计' : '开放设计'}{result.cached ? ' · 已验证缓存' : ''}</span>
      <ul>{result.assumptions.map((assumption) => <li key={assumption}>{assumption}</li>)}</ul>
      {!result.preview.valid && <Alert tone="warning">运动学门限尚未通过，采用后需要继续调整。</Alert>}
      <button type="button" className="button secondary full" onClick={() => onAdopt(result)}><Check size={14} />采用草稿并进入镜头编辑</button>
      <small>采用会重设镜头航点；尚未保存或提交渲染。</small>
    </div>}
  </div>
}

export default function PathDesigner({ recipe, scene, onApply, onClose }: {
  recipe: Recipe; scene: Scene; onApply: (recipe: Recipe) => void; onClose: () => void
}) {
  const [draft, setDraft] = useState<Recipe>(structuredClone(recipe))
  const [name, setName] = useState(`${scene.title} / 自定义路径`)
  const [description, setDescription] = useState('')
  const [step, setStep] = useState<1 | 2>(1)
  const [entry, setEntry] = useState<'geometry' | 'language'>('geometry')
  const [plan, setPlan] = useState<Plan | null>(null)
  const [pending, setPending] = useState(true)
  const [planError, setPlanError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [paths, setPaths] = useState<SavedPath[]>([])
  const [pathTotal, setPathTotal] = useState(0)
  const [library, setLibrary] = useState(false)
  const [loadingPaths, setLoadingPaths] = useState(false)
  const [saved, setSaved] = useState<SavedPath | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<SavedPath | null>(null)
  const [frameIndex, setFrameIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [cameraView, setCameraView] = useState(false)
  const form = useRef<HTMLFormElement>(null)
  const path = draft.trajectory
  const patchPath = (patch: Partial<Trajectory>) => setDraft((value) => ({ ...value, trajectory: { ...value.trajectory, ...patch } }))
  useEffect(() => {
    const controller = new AbortController()
    setPending(true); setPlanError(null)
    const timer = setTimeout(() => {
      void request<Plan>('/plan', { method: 'POST', body: JSON.stringify(draft), signal: controller.signal })
        .then((value) => { setPlan(value); setFrameIndex((current) => Math.min(current, value.frames.length - 1)) })
        .catch((failure: unknown) => { if (!controller.signal.aborted) { setPlan(null); setPlanError(errorMessage(failure)) } })
        .finally(() => { if (!controller.signal.aborted) setPending(false) })
    }, 250)
    return () => { controller.abort(); clearTimeout(timer) }
  }, [draft])
  useEffect(() => {
    if (!playing || !plan) return
    const timer = setInterval(() => setFrameIndex((index) => (index + 1) % plan.frames.length), 1000 / draft.capture.fps)
    return () => clearInterval(timer)
  }, [playing, plan, draft.capture.fps])

  const refreshPaths = async (offset = 0) => {
    setLoadingPaths(true)
    try {
      const result = await request<{ items: SavedPath[]; total: number }>(`/paths?scene_id=${encodeURIComponent(scene.id)}&limit=50&offset=${offset}`)
      setPaths((previous) => offset ? [...previous, ...result.items.filter((item) => !previous.some((old) => old.id === item.id))] : result.items)
      setPathTotal(result.total)
    } catch (failure) { setError(errorMessage(failure)) }
    finally { setLoadingPaths(false) }
  }
  const save = async (overwrite: boolean) => {
    if (!form.current?.reportValidity() || !plan || pending) return
    setBusy(true); setError(null)
    const payload = { name, description, scene_id: scene.id, duration_seconds: draft.capture.duration_seconds, trajectory: path }
    try {
      const result = await request<SavedPath>(overwrite && saved ? `/paths/${saved.id}` : '/paths', {
        method: overwrite && saved ? 'PUT' : 'POST',
        body: JSON.stringify(overwrite && saved ? { ...payload, revision: saved.revision } : payload),
      })
      setSaved(result)
      setNotice(`已持久化保存「${result.name}」· v${result.revision}。保存不代表通过渲染验收。`)
      if (library) await refreshPaths()
    } catch (failure) { setError(errorMessage(failure)) }
    finally { setBusy(false) }
  }
  const load = async (item: SavedPath) => {
    setBusy(true); setError(null)
    try {
      const current = await request<SavedPath>(`/paths/${item.id}`)
      if (current.scene_id !== scene.id || current.asset_sha256 !== scene.asset_sha256) throw new Error('该路径的源场景指纹与当前资产不一致，不能直接复用。')
      setDraft((value) => ({ ...value, trajectory: current.trajectory, capture: { ...value.capture, duration_seconds: current.duration_seconds } }))
      setSaved(current); setName(current.name); setDescription(current.description)
      setLibrary(false); setStep(2); setEntry('geometry'); setFrameIndex(0); setPlaying(false)
      setNotice(`已加载 v${current.revision}。修改不会影响已提交的采集任务。`)
    } catch (failure) { setError(errorMessage(failure)) }
    finally { setBusy(false) }
  }
  const remove = async () => {
    if (!deleteTarget) return
    setBusy(true); setError(null)
    try {
      await request(`/paths/${deleteTarget.id}?revision=${deleteTarget.revision}`, { method: 'DELETE' })
      if (saved?.id === deleteTarget.id) setSaved(null)
      setDeleteTarget(null); await refreshPaths()
      setNotice('已删除库中的路径记录，原有采集任务和当前草稿不受影响。')
    } catch (failure) { setError(errorMessage(failure)) }
    finally { setBusy(false) }
  }
  const current = plan?.frames[Math.min(frameIndex, plan.frames.length - 1)]
  const progress = current?.progress ?? 0
  const keys = path.orientation_keyframes
  const addKey = () => {
    if (!current) return
    setError(null)
    try {
      patchPath({ heading: 'forward', orientation_keyframes: insertOrientationKey(keys, {
        at: current.progress, yaw_deg: ((current.orientation.yaw_offset_deg + 180) % 360 + 360) % 360 - 180,
        pitch_deg: current.orientation.pitch_offset_deg,
      }, path.closed) })
      setPlaying(false)
    } catch (failure) { setError(errorMessage(failure)) }
  }
  const updateKey = (index: number, patch: Partial<OrientationKeyframe>) => patchPath({
    orientation_keyframes: keys.map((key, i) => i === index ? { ...key, ...patch } : key),
  })
  const dirty = saved && (name !== saved.name || description !== saved.description ||
    JSON.stringify(path) !== JSON.stringify(saved.trajectory) || draft.capture.duration_seconds !== saved.duration_seconds)

  return <Modal title="路径设计器" eyebrow="MOTION FIRST. LOOK SECOND." wide className="path-designer" busy={busy} onClose={onClose}>
    <form ref={form} onSubmit={(event) => {
      event.preventDefault()
      if (plan && !pending && !planError && !busy && name.trim()) onApply({ ...draft, name: name.trim() })
    }}>
      <div className="designer-topline">
        <div className="designer-steps">
          <button type="button" disabled={busy} className={step === 1 ? 'active' : ''} onClick={() => setStep(1)}><span>01</span>运动路径</button>
          <ChevronRight size={14} />
          <button type="button" disabled={busy} className={step === 2 ? 'active' : ''} onClick={() => { setStep(2); setEntry('geometry') }}><span>02</span>镜头航点</button>
        </div>
        <button type="button" className="button secondary small" disabled={busy} onClick={() => { setLibrary(!library); if (!library) void refreshPaths() }}><FolderOpen size={14} />{library ? '收起路径库' : '已保存路径'}</button>
      </div>
      {error && <div className="designer-message"><Alert>{error}</Alert></div>}
      {notice && <div className="designer-message"><Alert tone="info">{notice}</Alert></div>}
      {library && <section className="path-library"><header><strong>当前场景 · {scene.title}</strong><span>{pathTotal} 条设计 · 仅在该源资产上复用</span></header>
        {loadingPaths && <Spinner label="读取路径库…" />}
        {!loadingPaths && paths.length === 0 && <p>还没有保存的路径。编辑后点击下方「保存路径」，即可跨刷新和重启复用。</p>}
        {paths.map((item) => <div className="path-library-row" key={item.id}><Route size={16} /><div><strong>{item.name}</strong>
          <small>{PATH_NAMES[item.trajectory.kind]} · {item.duration_seconds}s · {item.trajectory.orientation_keyframes.length} 个镜头航点 · v{item.revision}</small></div>
          <button type="button" className="text-button" disabled={busy} onClick={() => void load(item)}>加载<ArrowRight size={13} /></button>
          <button type="button" className="icon-button" disabled={busy} aria-label={`删除路径 ${item.name}`} onClick={() => setDeleteTarget(item)}><Trash2 size={14} /></button></div>)}
        {paths.length < pathTotal && <button type="button" className="text-button" disabled={loadingPaths} onClick={() => void refreshPaths(paths.length)}>加载更多路径</button>}
        {deleteTarget && <div className="delete-path-confirm"><span>删除「{deleteTarget.name}」的库记录？不会删除采集数据。</span>
          <button type="button" className="button small secondary" disabled={busy} onClick={() => setDeleteTarget(null)}>保留</button>
          <button type="button" className="button small danger" disabled={busy} onClick={() => void remove()}>确认删除路径</button></div>}
      </section>}
      <fieldset className="designer-grid" disabled={busy}>
        <section className="designer-controls">
          <label className="field"><span>路径名称 {saved && <small>v{saved.revision}{dirty ? ' · 有未保存更改' : ' · 已保存'}</small>}</span>
            <input aria-label="路径名称" required maxLength={100} value={name} onChange={(event) => {
              setName(event.target.value)
              event.target.setCustomValidity(event.target.value.trim() ? '' : '请输入非空的路径名称。')
            }} /></label>
          <label className="field"><span>设计备注</span><input aria-label="设计备注" maxLength={1000} value={description} placeholder="用途、空间约束或回访意图" onChange={(event) => setDescription(event.target.value)} /></label>
          <div className="field-grid">
            <NumberField label="全片时长" value={draft.capture.duration_seconds} min={2} max={180} unit="s" onChange={(duration_seconds) => setDraft((value) => ({ ...value, capture: { ...value.capture, duration_seconds } }))} />
            <NumberField label="重复圈数" value={path.laps} min={1} max={path.closed ? 4 : 1} unit="圈" onChange={(laps) => patchPath({ laps })} />
          </div>
          {step === 1 ? <>
            <div className="designer-section-heading"><Route size={16} /><h3>先确定相机经过哪里</h3></div>
            <div className="choice-group"><button type="button" disabled={busy} className={entry === 'geometry' ? 'selected' : ''} onClick={() => setEntry('geometry')}>模板 / 动作积木</button>
              <button type="button" disabled={busy} className={entry === 'language' ? 'selected' : ''} onClick={() => setEntry('language')}><Sparkles size={12} />自然语言</button></div>
            {entry === 'language' ? <LanguageMotion trajectory={path} onBusy={setBusy} onAdopt={(proposal) => {
              setDraft((value) => ({ ...value, trajectory: proposal.trajectory, capture: { ...value.capture, duration_seconds: proposal.duration_seconds } }))
              setStep(2); setEntry('geometry'); setNotice('运动草稿已采用，镜头航点已重设。请预览并编辑朝向；尚未保存或渲染。')
            }} /> : <>
              <label className="field"><span>运动模型</span><select aria-label="运动模型" value={path.kind} onChange={(event) => {
                const kind = event.target.value
                if (kind === 'orbit' || kind === 'figure_eight' || kind === 'waypoints' || kind === 'program') {
                  patchPath(newPathKind(path, kind))
                  setNotice('镜头航点继续绑定全片归一化进度；改变运动几何后请重新检查朝向。')
                }
              }}>{Object.entries(PATH_NAMES).map(([key, title]) => <option value={key} key={key}>{title}</option>)}</select></label>
              {path.kind === 'program' ? <MotionBlocks trajectory={path} onChange={patchPath} /> : path.kind === 'waypoints' ?
                <SpatialPointsField value={path.waypoints} closed={path.closed} onChange={(waypoints) => patchPath({ waypoints })} /> :
                <div className="field-grid">
                  <NumberField label="路径半径" value={path.radius} min={0.25} max={500} step={0.25} unit="m" onChange={(radius) => patchPath({ radius })} />
                  <NumberField label="相机高度" value={path.height} min={-500} max={500} step={0.1} unit="m" onChange={(height) => patchPath({ height })} />
                  <NumberField label="中心 X" value={path.center[0]} min={-10000} max={10000} step={0.1} onChange={(x) => patchPath({ center: [x, path.center[1]] })} />
                  <NumberField label="中心 Y" value={path.center[1]} min={-10000} max={10000} step={0.1} onChange={(y) => patchPath({ center: [path.center[0], y] })} />
                  <NumberField label="起始相位" value={path.phase_deg} min={0} max={359.999} step={0.1} unit="°" onChange={(phase_deg) => patchPath({ phase_deg })} />
                  <NumberField label="长宽比" value={path.aspect} min={0.2} max={3} step={0.1} onChange={(aspect) => patchPath({ aspect })} />
                </div>}
              {(path.kind === 'program' || path.kind === 'waypoints') && <label className="designer-checkbox"><input type="checkbox" checked={path.closed}
                onChange={(event) => patchPath({ closed: event.target.checked, laps: event.target.checked ? path.laps : 1 })} />
                闭合设计：要求首尾位置和朝向一致</label>}
              <p className="field-help">动作路径不会自动补回程，也不会把末帧瞬移回起点。开放路径可保存为草稿；正式采集仍需通过回访验收。</p>
            </>}
          </> : <>
            <div className="designer-section-heading"><Eye size={16} /><h3>再设计相机看向哪里</h3><span>ROLL 0°</span></div>
            {path.heading === 'target' ? <Alert tone="warning">此旧配方固定看向目标点。镜头航点需以运动方向为基准。
              <button type="button" className="text-button" onClick={() => patchPath({ heading: 'forward' })}>切换到前进方向</button></Alert> :
              <div className="orientation-contract"><Compass size={16} /><span>默认沿运动方向。右转为正偏航，抬头为正俯仰；始终锁定水平线。</span></div>}
            <div className="selected-frame-card"><Crosshair size={16} /><div><strong>选中 F{current?.index ?? 0} · {(progress * 100).toFixed(2)}%</strong>
              <small>{formatTime(current?.timestamp_seconds ?? 0)} / {formatTime(plan?.stats.last_sample_seconds ?? 0)}</small></div>
              <button type="button" className="button secondary small" disabled={!current || pending || path.heading !== 'forward' || (path.closed && progress >= 1)} onClick={addKey}><CirclePlus size={13} />添加航点</button></div>
            {!keys.length && <p className="orientation-empty">尚无镜头偏转。拖动时间轴或点击三维路径，添加航点后设置左右／上下角度。</p>}
            <div className="orientation-key-list">{keys.map((key, index) => <article key={index} className="orientation-key">
              <header><span>镜头航点 {index + 1}</span><button type="button" className="text-button" onClick={() => {
                setFrameIndex(Math.round(key.at * ((plan?.frames.length ?? 1) - 1))); setPlaying(false)
              }}>定位<ChevronRight size={12} /></button>
                <button type="button" className="icon-button" aria-label={`删除镜头航点 ${index + 1}`} onClick={() => patchPath({ orientation_keyframes: keys.filter((_, i) => i !== index) })}><Trash2 size={13} /></button></header>
              <NumberField label={`航点 ${index + 1} 进度`} value={key.at * 100} min={0} max={100} step={0.1} unit="%" onChange={(at) => updateKey(index, { at: at / 100 })} />
              <div className="field-grid">
                <NumberField label={`航点 ${index + 1} 左右偏航`} value={key.yaw_deg} min={-180} max={180} step={0.1} unit="°" onChange={(yaw_deg) => updateKey(index, { yaw_deg })} />
                <NumberField label={`航点 ${index + 1} 上下俯仰`} value={key.pitch_deg} min={-85} max={85} step={0.1} unit="°" onChange={(pitch_deg) => updateKey(index, { pitch_deg })} />
              </div></article>)}</div>
            <p className="field-help">航点绑定整个视频的时间进度，不是每圈重复。采用最短偏航 + 五次缓动插值，角度不超调。{path.closed ? '末帧自动联动起点；若未设置起点，偏航和俯仰均为 0°。' : '未指定末端时保持最后一个偏转。'}至少间隔 2 帧；过快旋转会明确报错。</p>
          </>}
        </section>
        <section className="designer-preview">
          <header><span><Route size={15} />{scene.title}</span><div className="view-toggle">
            <button type="button" className={!cameraView ? 'active' : ''} onClick={() => setCameraView(false)}>路径总览</button>
            <button type="button" className={cameraView ? 'active' : ''} onClick={() => setCameraView(true)}>相机视角</button>
          </div></header>
          <div className="designer-viewport"><Suspense fallback={<div className="viewport-loading"><Spinner label="载入几何…" /></div>}>
            <Viewport scene={scene} plan={plan} frameIndex={frameIndex} cameraView={cameraView}
              onPickFrame={(index) => { setFrameIndex(index); setPlaying(false); if (step === 1) setStep(2) }}
              onCenter={(x, y, z) => {
                patchPath(recenterTrajectory(path, x, y, z))
              }} />
          </Suspense></div>
          <div className="designer-timeline">
            <button type="button" className="timeline-play" aria-label={playing ? '暂停路径预演' : '播放路径预演'} disabled={!plan || pending} onClick={() => setPlaying(!playing)}>{playing ? <Pause size={14} /> : <Play size={14} />}</button>
            <button type="button" className="icon-button" aria-label="回到路径起点" onClick={() => { setFrameIndex(0); setPlaying(false) }}><RotateCcw size={14} /></button>
            <input type="range" aria-label="路径设计时间轴" min={0} max={Math.max(0, (plan?.frames.length ?? 1) - 1)} value={Math.min(frameIndex, Math.max(0, (plan?.frames.length ?? 1) - 1))}
              disabled={!plan || pending} onChange={(event) => { setFrameIndex(Number(event.target.value)); setPlaying(false) }} />
            <span>{formatTime(current?.timestamp_seconds ?? 0)}</span>
          </div>
          <div className="orientation-readout"><div><span>相对左右</span><strong>{current?.orientation.yaw_offset_deg.toFixed(1) ?? '—'}°</strong></div>
            <div><span>相对上下</span><strong>{current?.orientation.pitch_offset_deg.toFixed(1) ?? '—'}°</strong></div>
            <div><span>绝对俯仰</span><strong>{current?.orientation.pitch_deg.toFixed(1) ?? '—'}°</strong></div><div><span>翻滚</span><strong>0.0° <LockKeyhole size={12} /></strong></div></div>
          <div className="designer-plan-status">{pending ? <Spinner label="校验完整位姿轨迹…" /> : planError ? <Alert>{planError}</Alert> :
            plan && <><div className={plan.valid ? 'text-green' : 'text-danger'}><Check size={15} />{plan.valid ? '运动学与闭合检查通过' : '尚未达到采集门限，可继续编辑或保存草稿'}</div>
              <div className="designer-metrics"><span>{plan.stats.path_length_m.toFixed(2)} m 路径</span><span>{plan.stats.max_speed_mps.toFixed(2)} m/s</span><span>{plan.stats.max_angular_speed_dps.toFixed(2)} °/s</span></div>
              {plan.checks.filter((check) => !check.passed).map((check) => <p className="text-danger" key={check.name}>{CHECK_LABELS[check.name] ?? check.name}：{check.value.toFixed(3)}，阈值 {check.threshold}</p>)}</>}
          </div>
          {plan?.motion_schedule.length ? <div className="compiled-motion"><strong>编译后的单圈时间线 · 重复 {path.laps} 圈</strong>
            <div>{plan.motion_schedule.map((piece, index) => <span key={index} className={piece.automatic ? 'automatic' : ''} title={`${piece.kind} · ${(piece.start_at * 100).toFixed(1)}%–${(piece.end_at * 100).toFixed(1)}%`}>
              {piece.automatic ? '平滑转向' : `动作 ${(piece.authored_index ?? 0) + 1}`}</span>)}</div>
            <small>权重分配每圈的曝光时间窗，包含自动插入的原地转向；镜头航点仍绑定整段视频。</small></div> : null}
          <div className="designer-footnote"><BookOpen size={14} /><span>这里是几何预演，不是最终画质。运动学通过仍不代表无碰撞、无遮挡或具备训练资格。保存路径会保存完整空间参数、朝向航点和时长，不保存 API 密钥。</span></div>
        </section>
      </fieldset>
      <footer className="modal-footer designer-footer"><div>
        <button type="button" className="button secondary" disabled={!plan || pending || busy} onClick={() => void save(false)}><Save size={14} />{saved ? '另存为新路径' : '保存路径'}</button>
        {saved && <button type="button" className="button secondary" disabled={!plan || pending || busy || !dirty} onClick={() => void save(true)}><Save size={14} />更新 v{saved.revision}</button>}
        <button type="button" className="icon-button" aria-label="导出当前路径配方" disabled={!plan || pending || busy} onClick={() => {
          if (form.current?.reportValidity()) saveJson({ ...draft, name: name.trim() }, 'locusforge-path-recipe.json')
        }}><Copy size={15} /></button>
      </div><div>{step === 1 ? <button type="button" className="button secondary" disabled={busy} onClick={() => { setStep(2); setEntry('geometry') }}>下一步：镜头航点<ArrowRight size={14} /></button> :
        <button type="button" className="button secondary" disabled={busy} onClick={() => setStep(1)}><ChevronLeft size={14} />运动路径</button>}
        <button type="submit" className="button primary" disabled={!plan || pending || busy || !!planError}><Check size={14} />应用到工作台</button></div></footer>
    </form>
  </Modal>
}
