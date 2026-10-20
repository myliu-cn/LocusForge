export type Page = 'studio' | 'scenes' | 'jobs' | 'datasets' | 'method'
export type Vec3 = [number, number, number]
export type Matrix = number[][]
export const SOURCE_GROUP_PATTERN = '[a-z0-9][a-z0-9_\\-]{0,63}'

export interface OrientationKeyframe {
  at: number
  yaw_deg: number
  pitch_deg: number
}

export type MotionSegment =
  | { kind: 'move'; direction: 'forward' | 'backward' | 'left' | 'right' | 'up' | 'down'; distance_m: number; weight: number }
  | { kind: 'arc'; radius_m: number; angle_deg: number; weight: number }
  | { kind: 'turn'; yaw_deg: number; weight: number }
  | { kind: 'hold'; weight: number }

export interface Trajectory {
  kind: 'orbit' | 'figure_eight' | 'waypoints' | 'program'
  center: [number, number]
  radius: number
  aspect: number
  height: number
  phase_deg: number
  laps: number
  heading: 'target' | 'forward'
  target: Vec3
  waypoints: Vec3[]
  closed: boolean
  start: Vec3
  start_yaw_deg: number
  segments: MotionSegment[]
  orientation_keyframes: OrientationKeyframe[]
}

export interface Capture {
  width: number
  height: number
  fps: number
  duration_seconds: number
  fov_deg: number
  engine: 'CYCLES' | 'BLENDER_EEVEE_NEXT'
  device: 'CPU' | 'CUDA' | 'OPTIX' | 'HIP' | 'ONEAPI' | 'METAL' | 'GRAPHICS'
  samples: number
  seed: number
  purpose: 'preview' | 'dataset'
  lighting: 'daylight' | 'asset'
}

export interface Quality {
  min_gap_seconds: number
  min_excursion_m: number
  max_position_error_m: number
  max_rotation_error_deg: number
  min_overlap: number
  max_departure_overlap: number
  min_valid_depth: number
  camera_radius_m: number
  max_speed_mps: number
  max_angular_speed_dps: number
  max_acceleration_mps2: number
  min_pairs: number
}

export interface Recipe {
  name: string
  scene_id: string
  trajectory: Trajectory
  capture: Capture
  quality: Quality
}

export interface Scene {
  id: string
  title: string
  subtitle?: string
  description: string
  asset_sha256: string
  asset_info: { mesh_count: number; material_count: number; bytes: number; animation_count: number }
  source_url: string
  source_group: string
  license: string
  attribution: string
  meters_per_unit: number
  commercial_use_confirmed: boolean
  training_use_confirmed: boolean
  split: 'train' | 'validation' | 'test'
  tags: string[]
  builtin: boolean
  default_trajectory: Partial<Trajectory>
}

export interface CameraFrame {
  index: number
  timestamp_seconds: number
  c2w: Matrix
  w2c: Matrix
  progress: number
  orientation: {
    base_yaw_deg: number; base_pitch_deg: number; yaw_offset_deg: number; pitch_offset_deg: number
    yaw_deg: number; pitch_deg: number; roll_deg: number
  }
}

export interface SavedPath {
  id: string
  name: string
  description: string
  scene_id: string
  asset_sha256: string
  revision: number
  created_at: string
  updated_at: string
  duration_seconds: number
  trajectory: Trajectory
}

export interface PlannerCapabilities {
  local_available: boolean
  llm_configured: boolean
  llm_provider: string
  llm_model: string
  timeout_seconds: number
  daily_limit: number
}

export interface MotionProposal {
  title: string
  summary: string
  assumptions: string[]
  duration_seconds: number
  trajectory: Trajectory
  provider: 'local' | 'llm'
  cached: boolean
  trace_id: string | null
  preview: { valid: boolean; checks: Plan['checks']; warnings: string[] }
}

export interface Plan {
  recipe: Recipe
  intrinsics: Matrix
  frames: CameraFrame[]
  stats: {
    frame_count: number
    video_duration_seconds: number
    last_sample_seconds: number
    path_length_m: number
    max_speed_mps: number
    max_angular_speed_dps: number
    max_acceleration_mps2: number
    excursion_m: number
    endpoint_position_error_m: number
    endpoint_rotation_error_deg: number
    raw_rgb_depth_bytes: number
    pixel_samples: number
    max_abs_pitch_deg: number
    max_roll_deg: number
    orientation_keyframe_count: number
  }
  checks: { name: string; passed: boolean; value: number; threshold: number }[]
  valid: boolean
  geometry_checked: boolean
  motion_schedule: { kind: string; start_at: number; end_at: number; automatic: boolean; authored_index: number | null }[]
  orientation_knots: OrientationKeyframe[]
  warnings: string[]
  planner_version: string
}

export type JobState = 'queued' | 'preflight' | 'rendering' | 'validating' | 'packaging' |
  'succeeded' | 'rejected' | 'failed' | 'cancelled'

export interface Report {
  passed: boolean
  training_eligible: boolean
  purpose?: 'preview' | 'dataset'
  certified_pairs: number
  minimum_valid_depth?: number
  gap_histogram?: Record<string, number>
  checks: { name: string; passed: boolean; measured: unknown; required: unknown }[]
  renderer?: {
    blender_version: string
    median_frame_seconds: number
    render_seconds: number
    max_position_pass_reprojection_error_px: number
  }
  archive?: { bytes: number; sha256: string }
  warnings?: string[]
}

export interface Job {
  id: string
  name: string
  state: JobState
  created_at: string
  updated_at: string
  recipe: Recipe
  scene: Scene
  progress: number
  completed_frames: number
  error: string | null
  report: Report | null
  cancel_requested: boolean
  parent_id: string | null
  events?: { timestamp: string; state: string; message: string }[]
}

export interface SystemInfo {
  version: string
  blender_available: boolean
  worker_online: boolean
  worker: { state: string; timestamp: string } | null
  render_threads: number
  free_disk_bytes: number
  total_disk_bytes: number
  max_asset_mb: number
  max_queued_jobs: number
  authentication: 'token' | 'loopback-only'
  counts: Partial<Record<JobState, number>>
  statistics: { certified_pairs: number; completed_frames: number; training_datasets: number }
  scene_count: number
}

export interface LoopPair {
  source_frame: number
  target_frame: number
  gap_seconds: number
  kind: 'exact' | 'near'
  position_error_m: number
  rotation_error_deg: number
  overlap_forward: number
  overlap_backward: number
  overlap_min: number
  excursion_m: number
  departure_frame: number
  departure_overlap: number
  certified: boolean
  reasons: string[]
}

export interface Loops {
  pairs: LoopPair[]
  rejected_candidates: LoopPair[]
  gap_histogram: Record<string, number>
}

export const DEFAULT_RECIPE: Recipe = {
  name: 'Courtyard / memory loop',
  scene_id: 'courtyard',
  trajectory: {
    kind: 'orbit', center: [0, 0], radius: 5.5, aspect: 1, height: 1.7,
    phase_deg: 225, laps: 1, heading: 'forward', target: [0, 0, 1.3], waypoints: [],
    closed: true, start: [-3.8, -3.8, 1.7], start_yaw_deg: 0, segments: [], orientation_keyframes: [],
  },
  capture: {
    width: 320, height: 180, fps: 4, duration_seconds: 12, fov_deg: 70,
    engine: 'CYCLES', device: 'CPU', samples: 8, seed: 42, purpose: 'preview', lighting: 'daylight',
  },
  quality: {
    min_gap_seconds: 5, min_excursion_m: 3, max_position_error_m: 0.25,
    max_rotation_error_deg: 8, min_overlap: 0.8, max_departure_overlap: 0.35,
    min_valid_depth: 0.5, camera_radius_m: 0.2, max_speed_mps: 3,
    max_angular_speed_dps: 90, max_acceleration_mps2: 6, min_pairs: 1,
  },
}

export const STATE_LABELS: Record<JobState, string> = {
  queued: '排队中', preflight: '几何预检', rendering: '渲染中', validating: '数据验收',
  packaging: '封装中', succeeded: '已完成', rejected: '未通过', failed: '失败', cancelled: '已取消',
}

export const TERMINAL: JobState[] = ['succeeded', 'rejected', 'failed', 'cancelled']

export const CHECK_LABELS: Record<string, string> = {
  speed: '相机速度', angular_speed: '旋转速度', acceleration: '相机加速度',
  excursion: '离场距离', frame_count: '帧数与文件对齐', swept_camera_clearance: '连续相机碰撞检查',
  camera_matrix_consistency: '相机矩阵一致性', sample_clock: '严格采样时钟',
  position_pass_projection: '渲染投影精度', valid_depth: '有效深度覆盖',
  exposure_not_empty: '曝光与非空画面', non_frozen_video: '非静止视频', certified_revisits: '几何认证回环',
  path_position_closure: '位置闭合', path_orientation_closure: '朝向闭合',
  orientation_sampling: '镜头航点采样密度', motion_sampling: '动作采样密度', zero_camera_roll: '零翻滚约束',
}
