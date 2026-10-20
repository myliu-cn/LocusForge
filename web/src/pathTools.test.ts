import { describe, expect, it } from 'vitest'
import { insertOrientationKey, newPathKind, parseSpatialPoints, recenterTrajectory } from './pathTools'
import { DEFAULT_RECIPE } from './types'

describe('two-stage path design contracts', () => {
  it('accepts open spatial paths and does not turn blank coordinates into zeros', () => {
    expect(parseSpatialPoints('0, 0, 1.7\n4, 2, 1.7', false)).toEqual([[0, 0, 1.7], [4, 2, 1.7]])
    expect(() => parseSpatialPoints('0, , 1.7\n4, 2, 1.7', false)).toThrow('三个有限数字')
    expect(() => parseSpatialPoints('0,0,1\n4,2,1', true)).toThrow('4–64')
    expect(() => parseSpatialPoints('0,0,Infinity\n4,2,1', false)).toThrow('有限数字')
  })

  it('keeps no-roll offsets while changing motion templates', () => {
    const path = structuredClone(DEFAULT_RECIPE.trajectory)
    path.orientation_keyframes = [{ at: 0.3, yaw_deg: 20, pitch_deg: -10 }]
    const program = newPathKind(path, 'program')
    expect(program.heading).toBe('forward')
    expect(program.orientation_keyframes).toEqual(path.orientation_keyframes)
    expect(program.segments).toHaveLength(4)
    expect(newPathKind(program, 'orbit').segments).toEqual([])
  })

  it('prevents duplicate keys and accidental independent closing rotations', () => {
    const keys = [{ at: 0.25, yaw_deg: 30, pitch_deg: 10 }]
    expect(() => insertOrientationKey(keys, { ...keys[0] }, true)).toThrow('已经有')
    expect(() => insertOrientationKey(keys, { at: 1, yaw_deg: 0, pitch_deg: 0 }, true)).toThrow('联动')
    const sorted = insertOrientationKey(keys, { at: 0, yaw_deg: -10, pitch_deg: 0 }, true)
    expect(sorted.map((key) => key.at)).toEqual([0, 0.25])
    expect(insertOrientationKey(keys, { at: 1, yaw_deg: 40, pitch_deg: 0 }, false)).toHaveLength(2)
  })

  it('recenters actual controls and the program start, not unused template parameters alone', () => {
    const path = newPathKind(DEFAULT_RECIPE.trajectory, 'waypoints')
    path.waypoints = [[8, 10, 2], [12, 10, 3], [12, 14, 2], [8, 14, 2]]
    const centered = recenterTrajectory(path, 0, 0, 1.7)
    expect(centered.waypoints[0]).toEqual([-2, -2, 1.7])
    expect(centered.waypoints[1][2]).toBeCloseTo(2.7)
    expect(centered.start).toEqual([0, 0, 1.7])
    expect(path.waypoints[0]).toEqual([8, 10, 2])
  })
})
