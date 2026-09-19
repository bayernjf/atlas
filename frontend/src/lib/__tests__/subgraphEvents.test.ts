import { describe, expect, it } from 'vitest'
import { isSubgraphInternal, subgraphPathLabel, subgraphPathPrefix } from '../subgraphEvents'

describe('subgraph event namespace helpers (A pack, docs/27 §3)', () => {
  it('classifies top-level vs subgraph-internal events', () => {
    expect(isSubgraphInternal(undefined)).toBe(false)
    expect(isSubgraphInternal([])).toBe(false)
    expect(isSubgraphInternal(['subgraph-1'])).toBe(true)
    expect(isSubgraphInternal(['outer', 'inner'])).toBe(true)
  })

  it('formats log prefixes for single and nested subgraphs', () => {
    expect(subgraphPathPrefix(undefined)).toBe('')
    expect(subgraphPathPrefix([])).toBe('')
    expect(subgraphPathPrefix(['subgraph-1'])).toBe('[subgraph-1] ')
    expect(subgraphPathPrefix(['outer', 'inner'])).toBe('[outer › inner] ')
  })

  it('formats human-readable ownership labels without trailing separator', () => {
    expect(subgraphPathLabel(undefined)).toBe('')
    expect(subgraphPathLabel(['subgraph-1'])).toBe('subgraph-1')
    expect(subgraphPathLabel(['outer', 'inner'])).toBe('outer › inner')
  })
})
