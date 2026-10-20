import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import type { Dispatch, SetStateAction } from 'react'
import {
  ArrowDownToLine, ArrowRight, BadgeCheck, Camera, Check, ChevronDown, CircleDot,
  Cpu, Focus, Info, Layers3, Maximize2, Pause, Play, RotateCcw, Route, Settings2, Sparkles,
} from 'lucide-react'
import { Alert, NumberField, SceneThumbnail, SectionTitle, SpatialPointsField, Spinner } from './components'
import { errorMessage, formatBytes, formatTime, saveJson } from './api'
import type { Job, Plan, Recipe, Scene, SystemInfo, Trajectory, Vec3 } from './types'
import { CHECK_LABELS } from './types'
import { JobTable } from './Jobs'
import { recenterTrajectory } from './pathTools'

const Viewport = lazy(() => import('./Viewport'))
const PathDesigner = lazy(() => import('./PathDesigner'))

export function RouteMap({ plan, frameIndex = 0, large = false }: {
  plan: Plan | null; frameIndex?: number; large?: boolean
}) {
  if (!plan?.frames.length) return null
  const positions = plan.frames.map((frame) => [frame.c2w[0][3], frame.c2w[1][3]])
  const minX = Math.min(...positions.map((p) => p[0]))
  const minY = Math.min(...positions.map((p) => p[1]))
  const maxX = Math.max(...positions.map((p) => p[0]))
  const maxY = Math.max(...positions.map((p) => p[1]))
  const extent = Math.max(maxX - minX, maxY - minY, 1)
  const centerX = (minX + maxX) / 2
  const centerY = (minY + maxY) / 2
  const map = ([x, y]: number[]) => [70 + (x - centerX) / extent * 100, 70 - (y - centerY) / extent * 100]
  const points = positions.map(map)
  const current = points[Math.min(frameIndex, points.length - 1)]
  const first = points[0]
  return <div className={`route-map ${large ? 'large' : ''}`}>
    <span>XY · 世界坐标</span>
    <svg viewBox="0 0 140 140" role="img" aria-label="真实规划路径的俯视图">
      {[30, 50, 70, 90, 110].map((offset) => <g key={offset}>
        <line x1={offset} x2={offset} y1="12" y2="128" className="map-grid" />
        <line x1="12" x2="128" y1={offset} y2={offset} className="map-grid" />
      </g>)}
      <polyline points={points.map((p) => p.join(',')).join(' ')} fill="none" stroke="currentColor" strokeWidth="2.5" />
      <circle cx={first[0]} cy={first[1]} r="5.2" fill="#eff8e8" stroke="#4c865a" strokeWidth="2" />
      <circle cx={current[0]} cy={current[1]} r="4" fill="#d5fa9b" stroke="#254d39" strokeWidth="1.5" />
    </svg>
  </div>
}

function CaptureSettings({ recipe, setRecipe, plan, pending, submitting, onSubmit, system, onDesign }: {
  recipe: Recipe; setRecipe: Dispatch<SetStateAction<Recipe>>; plan: Plan | null;
  pending: boolean; submitting: boolean; onSubmit: () => void; system: SystemInfo | null; onDesign: () => void
}) {
  const trajectory = (patch: Partial<Trajectory>) => setRecipe((value) => ({
    ...value, trajectory: { ...value.trajectory, ...patch },
  }))
  const capture = (patch: Partial<Recipe['capture']>) => setRecipe((value) => ({
    ...value, capture: { ...value.capture, ...patch },
  }))
  const quality = (patch: Partial<Recipe['quality']>) => setRecipe((value) => ({
    ...value, quality: { ...value.quality, ...patch },
  }))
  const setPreset = (value: string) => {
    if (value === 'preview') capture({ width: 320, height: 180, fps: 4, duration_seconds: 12, samples: 8, purpose: 'preview' })
    else if (value === 'balanced') capture({ width: 640, height: 360, fps: 8, duration_seconds: 20, samples: 32, purpose: 'dataset' })
    else if (value === 'standard') capture({ width: 1280, height: 720, fps: 24, duration_seconds: 30, samples: 64, purpose: 'dataset' })
    else if (value === 'high') capture({ width: 1920, height: 1080, fps: 24, duration_seconds: 60, samples: 128, purpose: 'dataset' })
  }
  const changeKind = (kind: Trajectory['kind']) => {
    const r = recipe.trajectory.radius
    const h = recipe.trajectory.height
    const [x, y] = recipe.trajectory.center
    trajectory({
      kind,
      heading: 'forward', closed: true, segments: [],
      waypoints: kind === 'waypoints' ? [[x - r, y - r, h], [x + r, y - r, h], [x + r, y + r, h], [x - r, y + r, h]] : [],
    })
  }
  return <form className="settings-panel panel" id="capture-settings" onInvalid={(event) => {
    if (event.target instanceof HTMLElement) event.target.closest('details')?.setAttribute('open', '')
  }} onSubmit={(event) => {
    event.preventDefault()
    onSubmit()
  }}>
    <header className="panel-header"><h2><Settings2 size={17} />采集配方</h2><span className="small-label">RECIPE</span></header>
    <div className="settings-body">
      <label className="field"><span>任务名称</span><input required maxLength={100} value={recipe.name}
        onChange={(event) => setRecipe((value) => ({ ...value, name: event.target.value }))} /></label>
      <div className="form-section-title"><span>01</span>相机轨迹<small>DETERMINISTIC</small></div>
      <button type="button" className="path-designer-launch" onClick={onDesign}><Route size={19} /><span><strong>路径设计器</strong><small>运动路径 → 镜头航点 · 保存与复用</small></span><ArrowRight size={16} /></button>
      <div className="path-orientation-summary">{recipe.trajectory.heading === 'forward' ? '默认朝前' : '旧配方 · 看向目标'} · 零翻滚 · {recipe.trajectory.orientation_keyframes.length} 个镜头航点</div>
      <div className="choice-group" aria-label="轨迹类型">
        {([['orbit', '闭合环游'], ['figure_eight', '8 字回访'], ['waypoints', '控制点']] as const).map(([key, text]) =>
          <button type="button" key={key} className={recipe.trajectory.kind === key ? 'selected' : ''} onClick={() => changeKind(key)}>{text}</button>)}
      </div>
      {recipe.trajectory.kind === 'program' ? <p className="field-help">{recipe.trajectory.segments.length} 个组合动作 · 在路径设计器中编辑、调整时间权重和查看转向。</p> : recipe.trajectory.kind === 'waypoints'
        ? <SpatialPointsField value={recipe.trajectory.waypoints} closed={recipe.trajectory.closed} onChange={(waypoints) => trajectory({ waypoints })} />
        : <div className="field-grid">
          <NumberField label="路径半径" value={recipe.trajectory.radius} min={0.25} max={500} step={0.25} unit="m" onChange={(radius) => trajectory({ radius })} />
          <NumberField label="相机高度" value={recipe.trajectory.height} min={-500} max={500} step={0.1} unit="m" onChange={(height) => trajectory({ height })} />
        </div>}
      <div className="field-grid">
        <NumberField label="完整回环" value={recipe.trajectory.laps} min={1} max={recipe.trajectory.closed ? 4 : 1} unit="圈" onChange={(laps) => trajectory({ laps })} />
        <NumberField label="水平视场" value={recipe.capture.fov_deg} min={25} max={110} step={0.1} unit="°" onChange={(fov_deg) => capture({ fov_deg })} />
      </div>
      <div className="form-section-title"><span>02</span>输出规格<small>RGB + METRIC DEPTH</small></div>
      <label className="field"><span>快速应用预设</span><select value="" onChange={(event) => setPreset(event.target.value)}>
        <option value="" disabled>选择预设（下方可独立调整）</option>
        <option value="preview">轻量验证 · 180p / 4 fps / 8 spp</option>
        <option value="balanced">均衡采集 · 360p / 8 fps / 32 spp</option>
        <option value="standard">标准数据 · 720p / 24 fps / 64 spp</option>
        <option value="high">高质量 · 1080p / 24 fps / 128 spp</option>
      </select></label>
      <div className="field-grid">
        <NumberField label="视频宽度" value={recipe.capture.width} min={64} max={3840} step={2} unit="px" onChange={(width) => capture({ width })} />
        <NumberField label="视频高度" value={recipe.capture.height} min={64} max={2160} step={2} unit="px" onChange={(height) => capture({ height })} />
      </div>
      <div className="field-grid">
        <NumberField label="视频时长" value={recipe.capture.duration_seconds} min={2} max={180} unit="s" onChange={(duration_seconds) => capture({ duration_seconds })} />
        <NumberField label="采样帧率" value={recipe.capture.fps} min={1} max={60} unit="fps" onChange={(fps) => capture({ fps })} />
      </div>
      <label className="field"><span>数据用途</span><select value={recipe.capture.purpose}
        onChange={(event) => capture({ purpose: event.target.value === 'dataset' ? 'dataset' : 'preview' })}>
        <option value="preview">预览验证 · 不标记为可训练数据</option>
        <option value="dataset">正式数据 · 通过验收后可导出训练集</option>
      </select></label>
      <div className="form-section-title"><span>03</span>渲染与预算<small>EXPLICIT COMPUTE</small></div>
      <label className="field"><span>固定光照</span><select value={recipe.capture.lighting} onChange={(event) => capture({ lighting: event.target.value === 'asset' ? 'asset' : 'daylight' })}>
        <option value="daylight">固定日光 · 室外 / 开放天窗场景</option>
        <option value="asset">资产原始灯光 · 封闭室内场景</option>
      </select></label>
      <label className="field"><span>渲染引擎</span><select value={recipe.capture.engine} onChange={(event) => {
        const eevee = event.target.value === 'BLENDER_EEVEE_NEXT'
        capture({ engine: eevee ? 'BLENDER_EEVEE_NEXT' : 'CYCLES', device: eevee ? 'GRAPHICS' : 'CPU' })
      }}>
        <option value="CYCLES">Cycles · 物理路径追踪</option>
        <option value="BLENDER_EEVEE_NEXT">EEVEE · 实验通道（需图形上下文）</option>
      </select></label>
      <div className="field-grid">
        <label className="field"><span>计算设备</span><select value={recipe.capture.device}
          disabled={recipe.capture.engine === 'BLENDER_EEVEE_NEXT'} onChange={(event) => {
            const value = event.target.value
            if (value === 'CPU' || value === 'CUDA' || value === 'OPTIX' || value === 'HIP' || value === 'ONEAPI' || value === 'METAL') capture({ device: value })
          }}>
          {recipe.capture.engine === 'BLENDER_EEVEE_NEXT' ? <option value="GRAPHICS">图形 GPU</option> :
            ['CPU', 'OPTIX', 'CUDA', 'HIP', 'ONEAPI', 'METAL'].map((device) => <option key={device}>{device}</option>)}
        </select></label>
        <NumberField label="每像素采样" value={recipe.capture.samples} min={1} max={512} unit="spp" onChange={(samples) => capture({ samples })} />
      </div>
      <div className="compute-note"><Cpu size={15} /><span>{recipe.capture.device === 'CPU'
        ? `${system?.render_threads ?? '—'} 个渲染线程。建议先用预览档测量成本。`
        : '设备会在实际渲染器中检查；不可用时失败，不静默回退。'}</span></div>
      <details className="advanced-settings">
        <summary><Settings2 size={14} />坐标、复现与验收阈值<ChevronDown size={14} /></summary>
        {(recipe.trajectory.kind === 'orbit' || recipe.trajectory.kind === 'figure_eight') && <div className="field-grid">
          <NumberField label="路径中心 X" value={recipe.trajectory.center[0]} min={-10000} max={10000} step={0.1} unit="m" onChange={(x) => trajectory({ center: [x, recipe.trajectory.center[1]] })} />
          <NumberField label="路径中心 Y" value={recipe.trajectory.center[1]} min={-10000} max={10000} step={0.1} unit="m" onChange={(y) => trajectory({ center: [recipe.trajectory.center[0], y] })} />
          <NumberField label="路径长宽比" value={recipe.trajectory.aspect} min={0.2} max={3} step={0.1} onChange={(aspect) => trajectory({ aspect })} />
          <NumberField label="起始相位" value={recipe.trajectory.phase_deg} min={0} max={360} step={0.1} unit="°" onChange={(phase_deg) => trajectory({ phase_deg })} />
        </div>}
        <label className="field"><span>相机朝向</span><select aria-label="相机基准朝向" disabled={recipe.trajectory.orientation_keyframes.length > 0} value={recipe.trajectory.heading} onChange={(event) => trajectory({ heading: event.target.value === 'target' ? 'target' : 'forward' })}>
          <option value="target">始终看向目标点</option><option value="forward">沿路径前进方向</option>
        </select></label>
        {recipe.trajectory.heading === 'target' && <div className="field-grid triple">
          {(['X', 'Y', 'Z'] as const).map((axis, index) => <NumberField key={axis} label={`目标 ${axis}`}
            value={recipe.trajectory.target[index]} min={-10000} max={10000} step={0.1}
            onChange={(value) => {
              const target: Vec3 = [...recipe.trajectory.target]
              target[index] = value
              trajectory({ target })
            }} />)}
        </div>}
        <div className="field-grid">
          <NumberField label="固定随机种子" value={recipe.capture.seed} min={0} max={2147483647} onChange={(seed) => capture({ seed })} />
          <NumberField label="最短回访间隔" value={recipe.quality.min_gap_seconds} min={0.5} max={179} step={0.5} unit="s" onChange={(min_gap_seconds) => quality({ min_gap_seconds })} />
          <NumberField label="最小离场距离" value={recipe.quality.min_excursion_m} min={0.1} max={500} step={0.1} unit="m" onChange={(min_excursion_m) => quality({ min_excursion_m })} />
          <NumberField label="最低双向重叠" value={recipe.quality.min_overlap} min={0.5} max={1} step={0.01} onChange={(min_overlap) => quality({ min_overlap })} />
          <NumberField label="离场重叠上限" value={recipe.quality.max_departure_overlap} min={0} max={0.8} step={0.01} onChange={(max_departure_overlap) => quality({ max_departure_overlap })} />
          <NumberField label="相机碰撞半径" value={recipe.quality.camera_radius_m} min={0.05} max={1} step={0.05} unit="m" onChange={(camera_radius_m) => quality({ camera_radius_m })} />
          <NumberField label="最高移动速度" value={recipe.quality.max_speed_mps} min={0.1} max={20} step={0.1} unit="m/s" onChange={(max_speed_mps) => quality({ max_speed_mps })} />
          <NumberField label="最低有效深度" value={recipe.quality.min_valid_depth} min={0.1} max={1} step={0.05} onChange={(min_valid_depth) => quality({ min_valid_depth })} />
        </div>
        <p className="field-help">世界 Z 向上，单位为米。手动轨迹仍需通过实际三角网格碰撞检查；包围盒内不等于可通行。</p>
      </details>
    </div>
    <footer className="settings-footer">
      <div className="budget-row"><span>待渲染帧数</span><strong>{(recipe.capture.fps * recipe.capture.duration_seconds).toLocaleString()} <small>帧</small></strong></div>
      <div className="budget-row"><span>未压缩 RGB + 深度</span><span>{plan ? formatBytes(plan.stats.raw_rgb_depth_bytes) : '—'}</span></div>
      <button type="submit" className="button primary full" disabled={pending || submitting || !plan?.valid || !system?.blender_available}>
        {submitting ? <Spinner label="提交中…" /> : <><Play size={15} fill="currentColor" />加入渲染队列<ArrowRight size={16} /></>}
      </button>
      <span className="submit-caption"><BadgeCheck size={12} />先几何预检，后渲染；未通过不发布</span>
    </footer>
  </form>
}

function SceneStage({ scene, scenes, recipe, setRecipe, plan, pending, error, onScene }: {
  scene: Scene; scenes: Scene[]; recipe: Recipe; setRecipe: Dispatch<SetStateAction<Recipe>>;
  plan: Plan | null; pending: boolean; error: string | null; onScene: (scene: Scene) => void
}) {
  const [mode, setMode] = useState<'render' | 'geometry'>(scene.builtin ? 'render' : 'geometry')
  const [frameIndex, setFrameIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [viewportError, setViewportError] = useState<string | null>(null)
  const viewport = useRef<HTMLDivElement>(null)
  useEffect(() => { setFrameIndex(0); setPlaying(false) }, [plan])
  useEffect(() => setMode(scene.builtin ? 'render' : 'geometry'), [scene.id, scene.builtin])
  useEffect(() => {
    if (!playing || !plan) return
    const timer = setInterval(() => setFrameIndex((value) => (value + 1) % plan.frames.length), 1000 / recipe.capture.fps)
    return () => clearInterval(timer)
  }, [playing, plan, recipe.capture.fps])
  return <div className="scene-panel panel">
    <header className="panel-header">
      <h2><Layers3 size={17} />场景与轨迹</h2>
      <select className="scene-select" aria-label="当前场景" value={scene.id} onChange={(event) => {
        const chosen = scenes.find((item) => item.id === event.target.value)
        if (chosen) onScene(chosen)
      }}>{scenes.map((item) => <option value={item.id} key={item.id}>{item.title}</option>)}</select>
    </header>
    <div className="scene-toolbar">
      <div className="view-toggle"><button className={mode === 'render' ? 'active' : ''} onClick={() => setMode('render')}><Camera size={14} />渲染预览</button>
        <button className={mode === 'geometry' ? 'active' : ''} onClick={() => setMode('geometry')}><Route size={14} />三维轨迹</button></div>
      <div className="scene-tools"><span><i />静态场景</span>
        <button className="icon-button" aria-label="全屏查看场景" onClick={() => {
          if (viewport.current?.requestFullscreen) {
            void viewport.current.requestFullscreen().catch((failure: unknown) => setViewportError(errorMessage(failure)))
          } else setViewportError('当前浏览器不支持全屏查看。三维视口仍可正常操作。')
        }}><Maximize2 size={15} /></button>
      </div>
    </div>
    <div className="scene-viewport" ref={viewport}>
      {mode === 'render' ? <>
        <SceneThumbnail scene={scene} className="hero-scene-image" />
        <div className="scene-overlay-top"><span className="glass-label"><CircleDot size={12} />{scene.builtin ? '原创场景渲染' : '导入场景'}</span>
          <span className="glass-label">{scene.license}</span></div>
        <div className="scene-overlay-bottom"><div><span className="scene-overline">{scene.subtitle ?? 'YOUR SCENE, EXACTLY.'}</span>
          <h3>{scene.title}</h3><p>固定示意视角 · 在「三维轨迹」中检查相机路径</p></div>
          <RouteMap plan={plan} frameIndex={frameIndex} />
        </div>
      </> : <Suspense fallback={<div className="viewport-loading"><Spinner label="加载三维视口…" /></div>}>
        <Viewport scene={scene} plan={plan} frameIndex={frameIndex} onCenter={(x, y, z) => setRecipe((value) => ({
          ...value, trajectory: recenterTrajectory(value.trajectory, Number(x.toFixed(2)), Number(y.toFixed(2)), Number(z.toFixed(2))),
        }))} />
      </Suspense>}
    </div>
    {viewportError && <div className="plan-alert"><Alert>{viewportError}</Alert></div>}
    <div className="timeline">
      <button className="timeline-play" aria-label={playing ? '暂停轨迹预演' : '播放轨迹预演'}
        disabled={!plan} onClick={() => setPlaying(!playing)}>{playing ? <Pause size={15} fill="currentColor" /> : <Play size={15} fill="currentColor" />}</button>
      <button className="icon-button" aria-label="返回起始帧" onClick={() => { setFrameIndex(0); setPlaying(false) }}><RotateCcw size={14} /></button>
      <span className="time-label">{formatTime((plan?.frames[frameIndex]?.timestamp_seconds) ?? 0)}</span>
      <div className="timeline-track"><input type="range" aria-label="相机时间轴" min={0} max={Math.max(0, (plan?.frames.length ?? 1) - 1)}
        value={Math.min(frameIndex, (plan?.frames.length ?? 1) - 1)} disabled={!plan}
        onChange={(event) => { setFrameIndex(Number(event.target.value)); setPlaying(false) }} />
        <div className="timeline-endpoints"><span>起点</span><span>{recipe.trajectory.closed ? '闭合设计端点' : '开放路径末端'}</span></div></div>
      <span className="time-label">{formatTime(plan?.stats.last_sample_seconds ?? 0)}</span>
      <span className="frame-counter">{String(frameIndex + 1).padStart(3, '0')}<small> / {plan?.frames.length ?? '—'}</small></span>
    </div>
    <div className="plan-strip">
      <span><Route size={14} />{plan ? plan.stats.path_length_m.toFixed(1) : '—'} m <small>路径长度</small></span>
      <span><Focus size={14} />{plan ? plan.stats.max_speed_mps.toFixed(2) : '—'} m/s <small>最大速度</small></span>
      <span className={pending || !plan?.valid ? 'text-muted' : 'text-green'}>{pending ? <Spinner label="更新规划…" /> :
        <><Check size={14} />{plan?.valid ? '运动学通过' : '等待有效规划'}</>}</span>
    </div>
    {(error || (plan && !plan.valid)) && <div className="plan-alert"><Alert>{error ??
      plan?.checks.filter((check) => !check.passed).map((check) => `${CHECK_LABELS[check.name] ?? check.name}：${check.value.toFixed(2)} / 阈值 ${check.threshold}`).join('；')}</Alert></div>}
    <div className="geometry-disclaimer"><Info size={13} />规划阶段仅检查运动学。碰撞、可见性与回环证书将在真实渲染任务中验证。</div>
  </div>
}

export default function Studio({ scenes, recipe, setRecipe, plan, planPending, planError, system,
  jobs, onScene, onSubmit, submitting, onJob, onJobs, onImportRecipe }: {
  scenes: Scene[]; recipe: Recipe; setRecipe: Dispatch<SetStateAction<Recipe>>; plan: Plan | null;
  planPending: boolean; planError: string | null; system: SystemInfo | null; jobs: Job[];
  onScene: (scene: Scene) => void; onSubmit: () => void; submitting: boolean;
  onJob: (job: Job) => void; onJobs: () => void; onImportRecipe: () => void
}) {
  const scene = scenes.find((item) => item.id === recipe.scene_id)
  const [designing, setDesigning] = useState(false)
  return <>
    <div className="studio-grid">
      <div className="studio-main">
        {scene ? <SceneStage scene={scene} scenes={scenes} recipe={recipe} setRecipe={setRecipe} plan={plan}
          pending={planPending} error={planError} onScene={onScene} /> : <div className="panel empty-state">
          <Spinner label="等待场景资产…" /><p>首次启动请运行 <code>locusforge init</code> 注册内置场景。</p></div>}
        <div className="contract-banner"><div className="contract-icon"><BadgeCheck size={22} /></div>
          <div><strong>不估计世界，记录世界。</strong><p>内外参来自渲染器；回环由深度遮挡验证，而不是画面相似度。</p></div>
          <span className="contract-tag">GEOMETRY FIRST</span>
        </div>
        <div className="panel recent-panel">
          <SectionTitle title="最近采集" label="所有状态均来自真实任务队列" action={<button className="text-button" onClick={onJobs}>查看全部<ArrowRight size={14} /></button>} />
          <JobTable jobs={jobs.slice(0, 4)} onSelect={onJob} compact />
        </div>
        <div className="recipe-actions"><button className="text-button" onClick={() => saveJson(recipe, 'locusforge-recipe.json')}><ArrowDownToLine size={14} />导出当前配方</button>
          <button className="text-button" onClick={onImportRecipe}><Sparkles size={14} />从 JSON 配方恢复</button>
          <span>固定种子 · 不可变任务快照</span></div>
      </div>
      <CaptureSettings recipe={recipe} setRecipe={setRecipe} plan={plan} pending={planPending || !!planError}
        submitting={submitting} onSubmit={onSubmit} system={system} onDesign={() => setDesigning(true)} />
    </div>
    {designing && scene && <Suspense fallback={<div className="designer-loading"><Spinner label="加载路径设计器…" /></div>}>
      <PathDesigner recipe={recipe} scene={scene} onClose={() => setDesigning(false)} onApply={(value) => { setRecipe(value); setDesigning(false) }} />
    </Suspense>}
  </>
}
