import { useEffect, useRef, useState } from 'react'
import { Focus, Mouse, Move3D } from 'lucide-react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import type { CameraFrame, Plan, Scene } from './types'
import { Alert, Spinner } from './components'

function disposeObject(root: THREE.Object3D) {
  root.traverse((object) => {
    if (object instanceof THREE.Mesh || object instanceof THREE.Line || object instanceof THREE.Points) {
      object.geometry.dispose()
      const materials = Array.isArray(object.material) ? object.material : [object.material]
      for (const material of materials) {
        for (const value of Object.values(material)) {
          if (value instanceof THREE.Texture) value.dispose()
        }
        material.dispose()
      }
    }
  })
}

function updateMarker(marker: THREE.Group, frame: CameraFrame | undefined) {
  if (!frame) { marker.visible = false; return }
  marker.visible = true
  const m = frame.c2w
  marker.position.set(m[0][3], m[1][3], m[2][3])
  const rotation = new THREE.Matrix4().set(
    m[0][0], -m[0][1], -m[0][2], 0,
    m[1][0], -m[1][1], -m[1][2], 0,
    m[2][0], -m[2][1], -m[2][2], 0,
    0, 0, 0, 1,
  )
  marker.quaternion.setFromRotationMatrix(rotation)
}

export default function Viewport({ scene: asset, plan, frameIndex, onCenter, onPickFrame, cameraView = false }: {
  scene: Scene; plan: Plan | null; frameIndex: number; onCenter: (x: number, y: number, z: number) => void;
  onPickFrame?: (index: number) => void; cameraView?: boolean
}) {
  const container = useRef<HTMLDivElement>(null)
  const world = useRef<THREE.Scene | null>(null)
  const path = useRef<THREE.Line | null>(null)
  const marker = useRef<THREE.Group | null>(null)
  const keyMarkers = useRef<THREE.Points | null>(null)
  const fit = useRef<(() => void) | null>(null)
  const bounds = useRef<THREE.Box3 | null>(null)
  const currentFrame = useRef<CameraFrame | undefined>(undefined)
  const currentPlan = useRef<Plan | null>(plan)
  const currentView = useRef(cameraView)
  const pickCallback = useRef(onPickFrame)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  currentFrame.current = plan?.frames[frameIndex]
  currentPlan.current = plan
  currentView.current = cameraView
  pickCallback.current = onPickFrame

  useEffect(() => {
    if (!container.current) return
    const host = container.current
    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
    } catch (failure) {
      setError(`WebGL 不可用：${failure instanceof Error ? failure.message : String(failure)}。请切换渲染预览。`)
      setLoading(false)
      return
    }
    let disposed = false
    let animation = 0
    setLoading(true)
    setError(null)
    const scene = new THREE.Scene()
    scene.background = new THREE.Color('#e6e9e5')
    world.current = scene
    const camera = new THREE.PerspectiveCamera(48, 1, 0.05, 3000)
    const lens = new THREE.PerspectiveCamera(45, 16 / 9, 0.02, 1000)
    camera.up.set(0, 0, 1)
    camera.position.set(-22, -25, 23)
    renderer.setPixelRatio(Math.min(devicePixelRatio, 1.75))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 1.1
    renderer.autoClear = false
    renderer.domElement.setAttribute('aria-label', `${asset.title}的三维几何及真实相机轨迹`)
    renderer.domElement.setAttribute('role', 'img')
    host.appendChild(renderer.domElement)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    controls.target.set(0, 0, 1.5)
    controls.maxPolarAngle = Math.PI * 0.48
    scene.add(new THREE.HemisphereLight(0xe9f2ff, 0xc1b6a1, 2.2))
    const sunlight = new THREE.DirectionalLight(0xfff1d8, 3.2)
    sunlight.position.set(-6, -9, 18)
    scene.add(sunlight)
    const axes = new THREE.AxesHelper(1.5)
    axes.position.set(0, 0, 0.05)
    scene.add(axes)
    const rig = new THREE.Group()
    const geometry = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(0, 0, 0), new THREE.Vector3(-0.4, -0.24, -0.7),
      new THREE.Vector3(0.4, -0.24, -0.7), new THREE.Vector3(0, 0, 0),
      new THREE.Vector3(-0.4, 0.24, -0.7), new THREE.Vector3(0.4, 0.24, -0.7),
      new THREE.Vector3(0, 0, 0), new THREE.Vector3(0.4, -0.24, -0.7),
      new THREE.Vector3(0.4, 0.24, -0.7), new THREE.Vector3(-0.4, 0.24, -0.7),
      new THREE.Vector3(-0.4, -0.24, -0.7),
    ])
    const frustum = new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: '#d8fa8a', depthTest: false }))
    frustum.renderOrder = 12
    rig.add(frustum)
    const dot = new THREE.Mesh(new THREE.SphereGeometry(0.15, 12, 8),
      new THREE.MeshBasicMaterial({ color: '#215b43', depthTest: false }))
    dot.renderOrder = 12
    rig.add(dot)
    marker.current = rig
    scene.add(rig)
    let pointerStart: { x: number; y: number } | null = null
    const onPointerDown = (event: PointerEvent) => { pointerStart = { x: event.clientX, y: event.clientY } }
    const onPointerUp = (event: PointerEvent) => {
      if (!pointerStart || currentView.current || !pickCallback.current || !path.current ||
          Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) > 5) return
      const rect = renderer.domElement.getBoundingClientRect()
      const ray = new THREE.Raycaster()
      ray.params.Line.threshold = Math.max(0.08, (bounds.current?.getSize(new THREE.Vector3()).length() ?? 20) * 0.008)
      ray.setFromCamera(new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1,
        -(event.clientY - rect.top) / rect.height * 2 + 1), camera)
      const hit = ray.intersectObject(path.current, false)[0]
      if (hit?.index !== undefined) pickCallback.current(Math.min(hit.index, (currentPlan.current?.frames.length ?? 1) - 1))
      pointerStart = null
    }
    renderer.domElement.addEventListener('pointerdown', onPointerDown)
    renderer.domElement.addEventListener('pointerup', onPointerUp)
    const resize = new ResizeObserver(() => {
      const width = host.clientWidth
      const height = host.clientHeight
      if (!width || !height) return
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      renderer.setSize(width, height)
    })
    resize.observe(host)
    const loader = new GLTFLoader()
    loader.setWithCredentials(true)
    loader.load(`/api/scenes/${asset.id}/model`, (gltf) => {
      if (disposed) { disposeObject(gltf.scene); return }
      gltf.scene.rotation.x = Math.PI / 2
      gltf.scene.scale.setScalar(asset.meters_per_unit)
      scene.add(gltf.scene)
      const box = new THREE.Box3().setFromObject(gltf.scene)
      bounds.current = box
      const center = box.getCenter(new THREE.Vector3())
      const size = box.getSize(new THREE.Vector3())
      const distance = Math.max(size.x, size.y, size.z, 2) * 1.05
      fit.current = () => {
        controls.target.copy(center)
        camera.position.copy(center).add(new THREE.Vector3(-distance * 0.7, -distance * 0.9, distance * 0.85))
        camera.near = Math.max(0.01, distance / 1000)
        camera.far = Math.max(1000, distance * 20)
        camera.updateProjectionMatrix()
        controls.update()
      }
      fit.current()
      setLoading(false)
    }, undefined, (failure) => {
      if (!disposed) {
        setError(`场景加载失败：${failure instanceof Error ? failure.message : '请检查登录状态或 GLB 格式。'}`)
        setLoading(false)
      }
    })
    const draw = () => {
      animation = requestAnimationFrame(draw)
      updateMarker(rig, currentFrame.current)
      if (currentPlan.current) {
        const capture = currentPlan.current.recipe.capture
        const halfWidth = 0.7 * Math.tan(THREE.MathUtils.degToRad(capture.fov_deg) / 2)
        frustum.scale.set(halfWidth / 0.4, halfWidth * capture.height / capture.width / 0.24, 1)
      }
      const looking = currentView.current && currentFrame.current && currentPlan.current
      controls.enabled = !looking
      controls.update()
      const width = host.clientWidth
      const height = host.clientHeight
      renderer.setScissorTest(false)
      renderer.setViewport(0, 0, width, height)
      renderer.setClearColor('#1c3025')
      renderer.clear()
      rig.visible = Boolean(currentFrame.current) && !looking
      if (path.current) path.current.visible = !looking
      if (keyMarkers.current) keyMarkers.current.visible = !looking
      axes.visible = !looking
      if (looking && currentFrame.current && currentPlan.current) {
        const { width: outputWidth, height: outputHeight, fov_deg: fov } = currentPlan.current.recipe.capture
        const aspect = outputWidth / outputHeight
        lens.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(fov) / 2) / aspect))
        lens.aspect = aspect
        lens.updateProjectionMatrix()
        lens.position.copy(rig.position)
        lens.quaternion.copy(rig.quaternion)
        const renderWidth = Math.min(width, height * aspect)
        const renderHeight = renderWidth / aspect
        const left = (width - renderWidth) / 2
        const bottom = (height - renderHeight) / 2
        renderer.setViewport(left, bottom, renderWidth, renderHeight)
        renderer.setScissor(left, bottom, renderWidth, renderHeight)
        renderer.setScissorTest(true)
        renderer.render(scene, lens)
        renderer.setScissorTest(false)
      } else renderer.render(scene, camera)
    }
    draw()
    return () => {
      disposed = true
      cancelAnimationFrame(animation)
      resize.disconnect()
      controls.dispose()
      renderer.domElement.removeEventListener('pointerdown', onPointerDown)
      renderer.domElement.removeEventListener('pointerup', onPointerUp)
      disposeObject(scene)
      renderer.dispose()
      renderer.forceContextLoss()
      renderer.domElement.remove()
      world.current = null
      marker.current = null
      path.current = null
      keyMarkers.current = null
      bounds.current = null
      fit.current = null
    }
  }, [asset.id, asset.title, asset.meters_per_unit])

  useEffect(() => {
    const scene = world.current
    if (!scene) return
    if (path.current) {
      scene.remove(path.current)
      disposeObject(path.current)
      path.current = null
    }
    if (keyMarkers.current) {
      scene.remove(keyMarkers.current)
      disposeObject(keyMarkers.current)
      keyMarkers.current = null
    }
    if (plan) {
      const points = plan.frames.map(({ c2w: m }) => new THREE.Vector3(m[0][3], m[1][3], m[2][3]))
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),
        new THREE.LineBasicMaterial({ color: '#29835b', depthTest: false, transparent: true, opacity: 0.9 }))
      line.renderOrder = 10
      scene.add(line)
      path.current = line
      const knots = plan.recipe.trajectory.orientation_keyframes.map((key) => {
        const frame = plan.frames[Math.round(key.at * (plan.frames.length - 1))]
        return new THREE.Vector3(frame.c2w[0][3], frame.c2w[1][3], frame.c2w[2][3])
      })
      if (knots.length) {
        const dots = new THREE.Points(new THREE.BufferGeometry().setFromPoints(knots),
          new THREE.PointsMaterial({ color: '#d8a43f', size: 0.32, depthTest: false }))
        dots.renderOrder = 11
        scene.add(dots)
        keyMarkers.current = dots
      }
    }
  }, [plan, asset.id])

  return <div className="geometry-viewport">
    <div ref={container} className="webgl-host" />
    {loading && <div className="viewport-loading"><Spinner label="加载真实场景几何…" /></div>}
    {error && <div className="viewport-error"><Alert>{error}</Alert></div>}
    {!cameraView && <div className="viewport-tools">
      <button type="button" className="viewport-tool" onClick={() => fit.current?.()} aria-label="适应整个场景"><Focus size={17} /></button>
      <button type="button" className="viewport-tool" onClick={() => {
        if (!bounds.current) return
        const center = bounds.current.getCenter(new THREE.Vector3())
        onCenter(center.x, center.y, bounds.current.min.z + 1.7)
      }} disabled={loading || !!error} title="将轨迹中心对齐到场景包围盒，不代表自动避障"><Move3D size={16} />对齐路径</button>
    </div>}
    <div className="viewport-hint"><Mouse size={13} />{cameraView ? '真实 K / c2w 视角 · 不支持鼠标翻滚' : onPickFrame ? '拖动旋转 · 点击绿色路径选帧 · 金色为镜头航点' : '拖动旋转 · 滚轮缩放 · 绿色为相机路径'}<span>几何预览 ≠ 最终渲染</span></div>
  </div>
}
