import type { OrientationKeyframe, Trajectory } from './types'

export const PATH_NAMES: Record<Trajectory['kind'], string> = {
  orbit: '闭合环游', figure_eight: '8 字回访', waypoints: '空间控制点', program: '组合动作',
}

export function parseSpatialPoints(text: string, closed: boolean): [number, number, number][] {
  const rows = text.trim().split('\n')
  if (rows.length < (closed ? 4 : 2) || rows.length > 64) throw new Error(closed ? '闭合样条需要 4–64 个三维控制点。' : '开放样条需要 2–64 个三维控制点。')
  return rows.map((row, index) => {
    const values = row.trim().split(/[,，]/).map((value) => value.trim())
    if (values.length !== 3 || values.some((value) => value === '' || !Number.isFinite(Number(value)))) {
      throw new Error(`第 ${index + 1} 行必须是三个有限数字，格式为 x, y, z。`)
    }
    return [Number(values[0]), Number(values[1]), Number(values[2])]
  })
}

export function insertOrientationKey(
  keys: OrientationKeyframe[], key: OrientationKeyframe, closed: boolean,
): OrientationKeyframe[] {
  if (closed && key.at >= 1) throw new Error('闭合末帧与起点联动，请编辑起点或其他航点。')
  if (keys.some((item) => Math.abs(item.at - key.at) < 0.0001)) throw new Error('此进度已经有一个镜头航点，请直接修改它。')
  if (keys.length >= 64) throw new Error('每条路径最多保存 64 个镜头航点。')
  return [...keys, key].sort((a, b) => a.at - b.at)
}

export function newPathKind(previous: Trajectory, kind: Trajectory['kind']): Trajectory {
  const [x, y] = previous.center
  const r = previous.radius
  const z = previous.height
  return {
    ...previous, kind, heading: 'forward', closed: true,
    waypoints: kind === 'waypoints' ? [[x - r, y - r, z], [x + r, y - r, z], [x + r, y + r, z], [x - r, y + r, z]] : [],
    segments: kind === 'program' ? [
      { kind: 'move', direction: 'forward', distance_m: 3, weight: 1 },
      { kind: 'move', direction: 'right', distance_m: 3, weight: 1 },
      { kind: 'move', direction: 'backward', distance_m: 3, weight: 1 },
      { kind: 'move', direction: 'left', distance_m: 3, weight: 1 },
    ] : [],
  }
}

export function recenterTrajectory(previous: Trajectory, x: number, y: number, z: number): Trajectory {
  const points = previous.waypoints
  const originX = points.length ? (Math.min(...points.map((point) => point[0])) + Math.max(...points.map((point) => point[0]))) / 2 : previous.center[0]
  const originY = points.length ? (Math.min(...points.map((point) => point[1])) + Math.max(...points.map((point) => point[1]))) / 2 : previous.center[1]
  const originZ = points.length ? Math.min(...points.map((point) => point[2])) : previous.height
  return {
    ...previous, center: [x, y], height: z, start: [x, y, z], target: [x, y, z - 0.4],
    waypoints: points.map((point) => [point[0] - originX + x, point[1] - originY + y, point[2] - originZ + z]),
  }
}
