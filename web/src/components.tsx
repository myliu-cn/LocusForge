import { useEffect, useId, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { AlertCircle, Box, Check, ChevronRight, LoaderCircle, X } from 'lucide-react'
import type { JobState, Scene } from './types'
import { STATE_LABELS } from './types'
import type { Vec3 } from './types'
import { parseSpatialPoints } from './pathTools'

export function Logo({ small = false }: { small?: boolean }) {
  return <div className={`brand ${small ? 'small' : ''}`}>
    <img src="/favicon.svg" alt="" width={36} height={36} />
    <span>Locus<span className="brand-light">Forge</span><small>WORLD DATA STUDIO</small></span>
  </div>
}

export function Spinner({ label = '加载中' }: { label?: string }) {
  return <span className="loading-inline" role="status"><LoaderCircle size={16} className="spin" />{label}</span>
}

export function Badge({ state }: { state: JobState }) {
  return <span className={`status-badge state-${state}`}><i />{STATE_LABELS[state]}</span>
}

export function Alert({ children, tone = 'error' }: { children: ReactNode; tone?: 'error' | 'info' | 'warning' }) {
  return <div className={`alert ${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
    <AlertCircle size={16} /><div>{children}</div>
  </div>
}

export function Empty({ icon, title, children, action }: {
  icon?: ReactNode; title: string; children: ReactNode; action?: ReactNode
}) {
  return <div className="empty-state">
    <div className="empty-icon">{icon ?? <Box size={25} />}</div>
    <h3>{title}</h3><p>{children}</p>{action}
  </div>
}

export function Modal({ title, eyebrow, children, onClose, wide = false, busy = false, className = '' }: {
  title: string; eyebrow?: string; children: ReactNode; onClose: () => void; wide?: boolean; busy?: boolean; className?: string
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const headingId = useId()
  useEffect(() => {
    const dialog = ref.current
    dialog?.showModal()
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      dialog?.close()
      document.body.style.overflow = previous
    }
  }, [])
  return <dialog
    ref={ref}
    className={`modal ${wide ? 'modal-wide' : ''} ${className}`}
    aria-labelledby={headingId}
    onCancel={(event) => { event.preventDefault(); if (!busy) onClose() }}
    onClick={(event) => { if (event.target === event.currentTarget && !busy) onClose() }}
  >
    <div className="modal-shell">
      <header className="modal-header">
        <div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}<h2 id={headingId}>{title}</h2></div>
        <button className="icon-button" aria-label="关闭对话框" onClick={onClose} disabled={busy}><X size={19} /></button>
      </header>
      {children}
    </div>
  </dialog>
}

export function NumberField({ label, value, onChange, min, max, step = 1, unit }: {
  label: string; value: number; onChange: (value: number) => void;
  min: number; max: number; step?: number; unit?: string
}) {
  const [draft, setDraft] = useState(String(value))
  const id = useId()
  useEffect(() => setDraft(String(value)), [value])
  return <label className="field" htmlFor={id}><span>{label}</span>
    <span className="number-field">
      <input id={id} type="number" required min={min} max={max} step={Number.isInteger(step) ? step : 'any'} value={draft}
        onChange={(event) => {
          setDraft(event.target.value)
          const number = event.target.valueAsNumber
          if (event.target.validity.valid && Number.isFinite(number)) onChange(number)
        }}
      />
      {unit && <small>{unit}</small>}
    </span>
  </label>
}

export function SpatialPointsField({ value, closed, onChange }: {
  value: Vec3[]; closed: boolean; onChange: (points: Vec3[]) => void
}) {
  const [text, setText] = useState(value.map((point) => point.join(', ')).join('\n'))
  const [error, setError] = useState('')
  const input = useRef<HTMLTextAreaElement>(null)
  const lastSent = useRef(JSON.stringify(value))
  useEffect(() => {
    const next = JSON.stringify(value)
    if (next !== lastSent.current) {
      setText(value.map((point) => point.join(', ')).join('\n'))
      setError('')
      input.current?.setCustomValidity('')
      lastSent.current = next
    }
  }, [value])
  return <label className="field"><span>空间控制点 · 每行 x, y, z（米）</span>
    <textarea ref={input} rows={6} value={text} aria-label="空间控制点" onChange={(event) => {
      setText(event.target.value)
      try {
        const points = parseSpatialPoints(event.target.value, closed)
        event.target.setCustomValidity('')
        setError('')
        lastSent.current = JSON.stringify(points)
        onChange(points)
      } catch (failure) {
        const message = failure instanceof Error ? failure.message : String(failure)
        event.target.setCustomValidity(message)
        setError(message)
      }
    }} />
    {error && <small className="text-danger">{error}</small>}
    <small className="field-help">闭合曲线不重复填写首点；样条会圆滑经过这些点，仍须真实碰撞检查。</small>
  </label>
}

export function SceneThumbnail({ scene, className = '' }: { scene: Scene; className?: string }) {
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [scene.id])
  if (!scene.builtin || failed) {
    return <div className={`scene-placeholder ${className}`}><Box size={38} strokeWidth={1.1} />
      <span>{scene.title}</span><small>{failed ? '缩略图加载失败，请切换三维视口' : '在三维视口查看真实几何'}</small></div>
  }
  return <img className={className} src={`/api/scenes/${scene.id}/thumbnail`}
    alt={`${scene.title}的真实 Cycles 渲染预览`} onError={() => setFailed(true)} />
}

export function SectionTitle({ title, label, action }: { title: string; label?: string; action?: ReactNode }) {
  return <div className="section-title"><div><h2>{title}</h2>{label && <span>{label}</span>}</div>{action}</div>
}

export function MethodSteps() {
  return <div className="method-steps">
    {['授权 3D 场景', '确定性相机路径', 'RGB-D 真值渲染', '双向几何验收'].map((text, index) => <div key={text}>
      <span className="step-number">{String(index + 1).padStart(2, '0')}</span><span>{text}</span>
      {index < 3 ? <ChevronRight size={15} /> : <Check size={15} />}
    </div>)}
  </div>
}
