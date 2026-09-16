/**
 * 最小只读 SchemaRegistry（M1，04 §4.9）：内置节点单一来源。
 * get(kind, version) 只读；未知 kind/version 拒绝。工具/技能/记忆/部署/卡片
 * 五类实体入册缓做 docs/14 D29。
 */

import { assertNodeConfigSchema, type NodeConfigSchema } from './metaSchema'
import { triggerSchema } from './nodes/trigger.schema'
import { aiDecisionSchema } from './nodes/ai_decision.schema'
import { toolCallSchema } from './nodes/tool_call.schema'
import { waitSchema } from './nodes/wait.schema'

export const SCHEMA_VERSION = 'v1'

const SCHEMAS: Record<string, Record<string, NodeConfigSchema>> = {
  trigger: { [SCHEMA_VERSION]: triggerSchema },
  ai_decision: { [SCHEMA_VERSION]: aiDecisionSchema },
  tool_call: { [SCHEMA_VERSION]: toolCallSchema },
  wait: { [SCHEMA_VERSION]: waitSchema },
}

for (const versions of Object.values(SCHEMAS)) {
  for (const schema of Object.values(versions)) assertNodeConfigSchema(schema)
}

export const schemaRegistry = {
  get(kind: string, version: string = SCHEMA_VERSION): NodeConfigSchema {
    const versions = SCHEMAS[kind]
    if (!versions) throw new Error(`SchemaRegistry：未知节点种类 "${kind}"`)
    const schema = versions[version]
    if (!schema) throw new Error(`SchemaRegistry：节点种类 "${kind}" 无版本 "${version}"`)
    return schema
  },
  registeredKinds(): string[] {
    return Object.keys(SCHEMAS)
  },
}
