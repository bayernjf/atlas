/**
 * 画布 → Graph 定义 JSON 序列化（node_schema 形状，03 契约索引）。
 * W5-W6 只做编辑器侧序列化，W7-W8 的 DSL→LangGraph 编译以此为输入。
 */

import type { Edge } from '@xyflow/react'
import type { EditorNodeData } from './nodeCatalog'
import type { GraphVariable } from './variables'

export type EditorNode = {
  id: string
  position: { x: number; y: number }
  data: EditorNodeData
}

export type SerializedGraph = {
  version: number
  variables: GraphVariable[]
  nodes: Array<{
    id: string
    type: EditorNodeData['kind']
    name: string
    description: string
    position: { x: number; y: number }
    config: EditorNodeData['config']
    retry: EditorNodeData['retry']
  }>
  edges: Array<{ id: string; source: string; target: string }>
}

export function serializeGraph(nodes: EditorNode[], edges: Edge[], variables: GraphVariable[]): SerializedGraph {
  return {
    version: 1,
    variables,
    nodes: nodes.map((node) => ({
      id: node.id,
      type: node.data.kind,
      name: node.data.label,
      description: node.data.description ?? '',
      position: { x: Math.round(node.position.x), y: Math.round(node.position.y) },
      config: node.data.config,
      retry: node.data.retry,
    })),
    edges: edges.map((edge) => ({ id: edge.id, source: edge.source, target: edge.target })),
  }
}

/**
 * 后端 Graph JSON（NL 草稿/已保存图）→ 编辑器元素。
 * 草稿节点统一补 idle 状态，缺省的 retry 由序列化侧保证。
 */
export function deserializeGraph(graph: SerializedGraph): {
  nodes: EditorNode[]
  edges: Edge[]
  variables: GraphVariable[]
} {
  return {
    nodes: graph.nodes.map((node) => ({
      id: node.id,
      position: node.position,
      data: {
        label: node.name,
        kind: node.type,
        status: 'idle' as const,
        description: node.description ?? '',
        config: node.config,
        retry: node.retry,
      },
    })),
    edges: graph.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
    })),
    variables: graph.variables,
  }
}
