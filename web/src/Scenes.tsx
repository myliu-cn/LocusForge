import { useEffect, useRef, useState } from 'react'
import { ArrowRight, Box, Check, FileBox, Fingerprint, Info, Search, ShieldCheck, Upload, X } from 'lucide-react'
import { Alert, Empty, Modal, SceneThumbnail, Spinner } from './components'
import { errorMessage, formatBytes, uploadScene } from './api'
import type { Scene } from './types'
import { SOURCE_GROUP_PATTERN } from './types'

export function ScenesPage({ scenes, onChoose, onUpload }: {
  scenes: Scene[]; onChoose: (scene: Scene) => void; onUpload: () => void
}) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [detail, setDetail] = useState<Scene | null>(null)
  const visible = scenes.filter((scene) => `${scene.title} ${scene.tags.join(' ')} ${scene.source_group}`.toLowerCase().includes(query.toLowerCase()) &&
    (filter === 'all' || (filter === 'builtin' ? scene.builtin : !scene.builtin)))
  return <div className="assets-page">
    <div className="list-toolbar"><div className="filter-tabs">
      {[['all', '全部场景'], ['builtin', '原创资产'], ['imported', '已导入']].map(([key, label]) =>
        <button className={filter === key ? 'active' : ''} key={key} onClick={() => setFilter(key)}>{label}</button>)}
    </div><label className="search-field"><Search size={16} /><input placeholder="搜索场景或来源族" aria-label="搜索场景" value={query} onChange={(event) => setQuery(event.target.value)} /></label></div>
    <div className="scene-library">
      {visible.map((scene) => <article className="asset-card panel" key={scene.id}>
        <button className="asset-image" onClick={() => onChoose(scene)} aria-label={`使用${scene.title}`}>
          <SceneThumbnail scene={scene} /><span className="asset-license"><ShieldCheck size={12} />{scene.license}</span>
          <span className="asset-hover">设计相机路径<ArrowRight size={18} /></span>
        </button>
        <div className="asset-card-body"><div><span className="eyebrow">{scene.subtitle ?? 'IMPORTED SCENE'}</span>
          <button className="icon-button" aria-label={`${scene.title}资产详情`} onClick={() => setDetail(scene)}><Info size={16} /></button></div>
          <h3>{scene.title}</h3><p>{scene.description || '自包含 GLB · 静态场景 · 由操作员确认授权'}</p>
          <div className="tags">{scene.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>
          <footer><span><Box size={13} />{formatBytes(scene.asset_info.bytes)}<small>·</small>{scene.asset_info.material_count} 材质</span>
            <button className="text-button" onClick={() => onChoose(scene)}>使用场景<ArrowRight size={14} /></button></footer>
        </div>
      </article>)}
      <button className="upload-tile" onClick={onUpload}><span><Upload size={25} strokeWidth={1.5} /></span><strong>带入你的世界</strong>
        <p>上传自包含 GLB 大场景<br />保留原始几何与米制比例</p><span className="button secondary small">导入场景<ArrowRight size={14} /></span></button>
    </div>
    {!visible.length && scenes.length > 0 && <Empty title="没有匹配的场景">调整搜索条件，或导入一个新场景。</Empty>}
    <div className="asset-license-note"><ShieldCheck size={20} /><div><strong>可商用，先从来源可追溯开始。</strong>
      <p>内置资产为原创 CC0。上传场景必须明确来源、版权、训练授权和场景族；同一资产不能跨训练 / 验证 / 测试划分。</p></div></div>
    {detail && <Modal title={detail.title} eyebrow="ASSET PROVENANCE" onClose={() => setDetail(null)}>
      <div className="modal-body"><SceneThumbnail scene={detail} className="asset-detail-image" /><p>{detail.description}</p>
        <dl className="asset-facts"><div><dt>许可</dt><dd>{detail.license}</dd></div><div><dt>来源场景族</dt><dd>{detail.source_group}</dd></div>
          <div><dt>固定划分</dt><dd>{detail.split}</dd></div><div><dt>每资产单位</dt><dd>{detail.meters_per_unit} 米</dd></div>
          <div><dt>署名</dt><dd>{detail.attribution}</dd></div><div><dt>来源</dt><dd>{detail.source_url.startsWith('http') ?
            <a href={detail.source_url} target="_blank" rel="noreferrer">{detail.source_url}</a> : detail.source_url}</dd></div></dl>
        <div className="fingerprint"><Fingerprint size={16} /><code>{detail.asset_sha256}</code></div>
        <Alert tone="info">授权是操作员声明，不是平台自动完成的法律审查。导入动画冻结在场景第 1 帧，透明和透射表面不纳入此版本的精确深度合同。</Alert>
      </div>
      <footer className="modal-footer"><span /><button className="button primary" onClick={() => { onChoose(detail); setDetail(null) }}>进入采集工作台<ArrowRight size={15} /></button></footer>
    </Modal>}
  </div>
}

export function UploadDialog({ onClose, onComplete, maxMb }: {
  onClose: () => void; onComplete: (scene: Scene) => void; maxMb: number
}) {
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [commercial, setCommercial] = useState(false)
  const [training, setTraining] = useState(false)
  const abort = useRef<(() => void) | null>(null)
  useEffect(() => () => abort.current?.(), [])
  const submit = async (form: HTMLFormElement) => {
    if (!file) { setError('请选择一个 .glb 文件。'); return }
    if (file.size > maxMb * 1024 ** 2) { setError(`文件超过 ${maxMb} MiB 上传上限。`); return }
    const data = new FormData(form)
    setBusy(true); setError(null)
    const upload = uploadScene<Scene>(file, {
      title: data.get('title'), description: data.get('description'),
      source_url: data.get('source_url'), source_group: data.get('source_group'),
      license: data.get('license'), attribution: data.get('attribution'),
      meters_per_unit: Number(data.get('scale')),
      commercial_use_confirmed: commercial, training_use_confirmed: training,
    }, setProgress)
    abort.current = upload.abort
    try {
      const scene = await upload.result
      abort.current = null
      onComplete(scene)
    } catch (failure) {
      setError(errorMessage(failure))
    } finally { setBusy(false) }
  }
  return <Modal title="带入你的世界" eyebrow="IMPORT A SCENE" onClose={onClose} busy={busy}>
    <form onSubmit={(event) => { event.preventDefault(); void submit(event.currentTarget) }}>
      <div className="modal-body upload-form">
        <p className="modal-description">加载完整场景，设计相机路径。我们记录引擎真值，不重新估计相机或深度。</p>
        {error && <Alert>{error}</Alert>}
        <label className={`file-drop ${file ? 'has-file' : ''}`}><input type="file" accept=".glb" required disabled={busy}
          onChange={(event) => { setFile(event.target.files?.[0] ?? null); setError(null) }} />
          <FileBox size={29} strokeWidth={1.5} /><strong>{file?.name ?? '点击选择自包含 GLB 文件'}</strong>
          <span>{file ? formatBytes(file.size) : `glTF 2.0 binary · 最大 ${maxMb} MiB · 嵌入全部纹理`}</span>
        </label>
        <div className="field-grid"><label className="field"><span>场景名称</span><input name="title" required maxLength={100} placeholder="例如：湖畔美术馆" disabled={busy} /></label>
          <label className="field"><span>米 / 资产单位</span><input type="number" name="scale" min={0.001} max={1000} step="any" defaultValue={1} required disabled={busy} /></label></div>
        <label className="field"><span>场景说明</span><input name="description" maxLength={1000} placeholder="空间类型、拍摄区域及需要注意的几何结构" disabled={busy} /></label>
        <div className="field-grid"><label className="field"><span>资产许可</span><select name="license" defaultValue="CUSTOM" disabled={busy}>
          <option value="CC0-1.0">CC0-1.0</option><option value="CC-BY-4.0">CC-BY-4.0</option>
          <option value="OWNED">完全自有版权</option><option value="CUSTOM">商业授权 / 自定义</option>
        </select></label><label className="field"><span>来源场景族 ID</span><input name="source_group" required pattern={SOURCE_GROUP_PATTERN} placeholder="lakeside-gallery-v1" disabled={busy} /></label></div>
        <label className="field"><span>来源链接 / 原创标识</span><input name="source_url" required maxLength={1000} placeholder="https://... 或 original:your-studio/scene-id" disabled={busy} /></label>
        <label className="field"><span>作者署名与授权记录</span><input name="attribution" required maxLength={1000} placeholder="作者、许可持有人与必要的授权说明" disabled={busy} /></label>
        <div className="license-checks"><label><input type="checkbox" required checked={commercial} onChange={(event) => setCommercial(event.target.checked)} disabled={busy} />
          <span>我确认拥有该资产及其渲染衍生数据的商业使用权。</span></label>
          <label><input type="checkbox" required checked={training} onChange={(event) => setTraining(event.target.checked)} disabled={busy} />
            <span>我确认该授权允许生成机器学习训练数据。</span></label></div>
        <p className="field-help"><Info size={13} />同一原始场景的改色、裁剪与重导出必须使用同一个场景族 ID，避免数据泄漏。当前版本只支持不透明三角网格。</p>
        {busy && <div className="upload-progress"><div><Spinner label={progress < 100 ? `上传中 · ${progress}%` : '上传完成，正在校验并入库…'} />
          <button type="button" className="text-button" onClick={() => abort.current?.()}><X size={13} />取消上传</button></div>
          <div className="progress-track"><span style={{ width: `${progress}%` }} /></div></div>}
      </div>
      <footer className="modal-footer"><span className="text-muted"><ShieldCheck size={14} />文件仅保存到当前部署</span>
        <button type="submit" className="button primary" disabled={busy || !file || !commercial || !training}>{busy ? <Spinner label="处理中…" /> : <><Check size={15} />校验并导入</>}</button></footer>
    </form>
  </Modal>
}
