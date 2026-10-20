import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { Badge, NumberField, SceneThumbnail, SpatialPointsField } from './components'
import { DEFAULT_RECIPE, SOURCE_GROUP_PATTERN } from './types'
import type { Scene, Vec3 } from './types'
import { useState } from 'react'

afterEach(cleanup)

describe('truthful and accessible studio controls', () => {
  it('preserves the newline while entering another spatial control point', () => {
    function Editor() {
      const [value, setValue] = useState<Vec3[]>([[0, 0, 1], [4, 0, 1]])
      return <SpatialPointsField value={value} closed={false} onChange={setValue} />
    }
    render(<Editor />)
    const input = screen.getByRole('textbox', { name: '空间控制点' }) as HTMLTextAreaElement
    fireEvent.change(input, { target: { value: '0, 0, 1\n4, 0, 1\n' } })
    expect(input.value.endsWith('\n')).toBe(true)
    fireEvent.change(input, { target: { value: '0, 0, 1\n4, 0, 1\n4, , 1' } })
    expect(input.checkValidity()).toBe(false)
    fireEvent.change(input, { target: { value: '0, 0, 1\n4, 0, 1\n4, 4, 1' } })
    expect(input.checkValidity()).toBe(true)
  })

  it('accepts valid fractional backend values without hidden step mismatches', () => {
    render(<NumberField label="长宽比" value={0.75} min={0.2} max={3} step={0.1} onChange={vi.fn()} />)
    expect((screen.getByRole('spinbutton', { name: '长宽比' }) as HTMLInputElement).checkValidity()).toBe(true)
  })

  it('uses a valid modern HTML pattern for source-group identifiers', () => {
    const pattern = new RegExp(`^(?:${SOURCE_GROUP_PATTERN})$`, 'v')
    expect(pattern.test('courtyard-v1_test')).toBe(true)
    expect(pattern.test('../invalid')).toBe(false)
  })

  it('does not silently turn an invalid numeric edit into a valid recipe', () => {
    const change = vi.fn()
    render(<form aria-label="test form"><NumberField label="相机高度" value={1.7} min={0.1} max={20} step={0.1} onChange={change} /></form>)
    const input = screen.getByRole('spinbutton', { name: '相机高度' })
    fireEvent.change(input, { target: { value: '' } })
    expect(change).not.toHaveBeenCalled()
    expect((input as HTMLInputElement).checkValidity()).toBe(false)
    fireEvent.change(input, { target: { value: '2.2' } })
    expect(change).toHaveBeenCalledWith(2.2)
  })

  it('visually distinguishes rejection from success', () => {
    render(<><Badge state="succeeded" /><Badge state="rejected" /></>)
    expect(screen.getByText('已完成').className).toContain('state-succeeded')
    expect(screen.getByText('未通过').className).toContain('state-rejected')
  })

  it('does not invent a thumbnail for an uploaded scene', () => {
    const scene: Scene = {
      id: 'test', title: 'Imported hall', description: '', asset_sha256: 'a'.repeat(64),
      asset_info: { mesh_count: 1, material_count: 1, bytes: 100, animation_count: 0 },
      source_url: 'original:test', source_group: 'test', license: 'OWNED', attribution: 'test',
      meters_per_unit: 1, commercial_use_confirmed: true, training_use_confirmed: true,
      split: 'train', tags: [], builtin: false, default_trajectory: DEFAULT_RECIPE.trajectory,
    }
    render(<SceneThumbnail scene={scene} />)
    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.getByText('在三维视口查看真实几何')).toBeTruthy()
  })
})
