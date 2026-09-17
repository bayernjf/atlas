/**
 * FormRenderer（M3，08 M3 立项条② / 04 §4.10）：schema + 已解析值 → 控件树。
 *
 * 薄封装：递归与不可变更新的全部逻辑在 formTree.ts（纯逻辑、可在 node 环境单测），
 * 本组件只把 FormNode 树落到 AntD 控件——object 按 properties 顺序分组、array
 * 增删行、additionalProperties-only 键值行、叶子字段按 resolveWidget 结果取控件
 * （未注册控件名降级 json）。任何变更都产出「下一整个根对象」，不改入参。
 */
import { createElement, type ReactElement, type ReactNode } from 'react'
import { Button, Empty, Input, Typography } from 'antd'
import type { MetaSchema } from '../schemas/metaSchema'
import type { Diagnostic } from '../validation/diagnostics'
import { renderMarkers } from '../validation/markers'
import { widgetComponent, widgetRegistry } from './defaultRegistry'
import {
  appendAtPath,
  buildFormTree,
  defaultValueFor,
  diagnosticsAt,
  nextKeyName,
  removeAtPath,
  renameKeyAtPath,
  setAtPath,
  type FormArrayNode,
  type FormGroupNode,
  type FormKeyValueNode,
  type FormNode,
  type FormWidgetNode,
} from './formTree'
import type { WidgetRegistry } from './registry'
import type { SchemaSource } from './resolveWidget'
import type { WidgetScope } from './types'
import { applyUiSchema, decorateNodeForRender, type UiSchema } from './uiSchema'

export type FormRendererProps = {
  /** 根 schema（工具 input_schema，或节点字段 schema 片段）。 */
  schema: MetaSchema
  /** 已解析的根值；根非对象时调用方应自行降级旧 JSON 文本框，不进本组件。 */
  value: unknown
  /** 接收下一整个根值（不可变新对象）。 */
  onChange(next: unknown): void
  /** 默认 'tool'（M3 只渲染工具 schema；节点 schema 传 'node' 使 x-widget 生效）。 */
  source?: SchemaSource
  scope?: WidgetScope
  nodeId?: string
  /** 表单内诊断（pointer 相对根）；未命中字段的条目在根下非阻塞汇总。 */
  diagnostics?: Diagnostic[]
  registry?: WidgetRegistry
  /** M4：UISchema 最小子集（ui:group 视觉分组 + hiddenWhen 条件显隐），仅作用于根 object。 */
  uiSchema?: UiSchema
}

type ViewContext = {
  root: unknown
  onChange(next: unknown): void
  registry: WidgetRegistry
  source: SchemaSource
  scope?: WidgetScope
  nodeId?: string
  diagnostics?: Diagnostic[]
  uiSchema?: UiSchema
}

export function FormRenderer({
  schema,
  value,
  onChange,
  source = 'tool',
  scope,
  nodeId,
  diagnostics,
  registry = widgetRegistry,
  uiSchema,
}: FormRendererProps): ReactElement {
  const tree = applyUiSchema(buildFormTree(schema, value, { source }), value, uiSchema)
  const ctx: ViewContext = {
    root: value,
    onChange,
    registry,
    source,
    scope,
    nodeId,
    diagnostics,
    uiSchema,
  }
  const unmapped = diagnosticsAt(diagnostics, tree.pointer)

  return (
    <div className="form-renderer">
      <FormNodeView node={tree} ctx={ctx} />
      {unmapped.length > 0 && (
        <div className="form-unmapped" style={{ marginTop: 4 }}>
          {unmapped.map((diagnostic, index) => (
            <Typography.Text
              key={`${diagnostic.code}-${index}`}
              type={diagnostic.severity === 'error' ? 'danger' : 'warning'}
              style={{ display: 'block', fontSize: 12 }}
            >
              {diagnostic.message}
            </Typography.Text>
          ))}
        </div>
      )}
    </div>
  )
}

function FormNodeView({ node: rawNode, ctx }: { node: FormNode; ctx: ViewContext }): ReactElement {
  // 渲染期装饰：数组行/键值行等懒建子树也在此统一烘焙嵌套 UISchema 文案。
  const node = decorateNodeForRender(rawNode, ctx.uiSchema)
  if (node.kind === 'group') return <GroupView node={node} ctx={ctx} />
  if (node.kind === 'array') return <ArrayView node={node} ctx={ctx} />
  if (node.kind === 'keyvalue') return <KeyValueView node={node} ctx={ctx} />
  return <WidgetView node={node} ctx={ctx} />
}

function Field({
  label,
  required,
  pointer,
  children,
}: {
  label: string
  required?: boolean
  /** RFC6901 指针：挂 data-pointer 供 Problems 面板点击定位（M4 批 2 ⑧）。 */
  pointer?: string
  children: ReactNode
}): ReactElement {
  if (!label) return <>{children}</>
  return (
    <label className="property-field" style={{ display: 'block' }} data-pointer={pointer}>
      <Typography.Text type="secondary">
        {label}
        {required && <Typography.Text type="danger"> *</Typography.Text>}
      </Typography.Text>
      {children}
    </label>
  )
}

function GroupView({ node, ctx }: { node: FormGroupNode; ctx: ViewContext }): ReactElement {
  // M4 ui:group：layout 'row' 让组内字段并排（视觉组专用）；缺省垂直堆叠。
  const row = node.layout === 'row'
  return (
    <div
      className="form-group"
      style={row ? { display: 'flex', gap: 8, alignItems: 'flex-start' } : undefined}
    >
      {node.label && (
        <Typography.Text type="secondary">
          {node.label}
          {node.required && <Typography.Text type="danger"> *</Typography.Text>}
        </Typography.Text>
      )}
      {node.children.map((child) =>
        row ? (
          <div key={child.pointer} style={{ flex: 1, minWidth: 0 }}>
            <FormNodeView node={child} ctx={ctx} />
          </div>
        ) : (
          <FormNodeView key={child.pointer} node={child} ctx={ctx} />
        ),
      )}
    </div>
  )
}

function ArrayView({ node, ctx }: { node: FormArrayNode; ctx: ViewContext }): ReactElement {
  const itemSchema = node.schema.items ?? {}
  // minItems/maxItems 门控（M4 批 2 ⑨：parallel branches 2-10；schema 解释器
  // 同时产出 ITEMS_MIN/MAX 诊断，按钮禁用只负责防越界）。
  const minItems = typeof node.schema.minItems === 'number' ? node.schema.minItems : 0
  const maxItems = typeof node.schema.maxItems === 'number' ? node.schema.maxItems : undefined
  const canRemove = node.items.length > minItems
  const canAdd = maxItems === undefined || node.items.length < maxItems
  const addLabel = maxItems === undefined ? '添加' : `添加（${node.items.length}/${maxItems}）`
  return (
    <div className="property-field form-array" style={{ marginBottom: 8 }} data-pointer={node.pointer}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Typography.Text type="secondary">
          {node.label}
          {node.required && <Typography.Text type="danger"> *</Typography.Text>}
        </Typography.Text>
        <Button
          size="small"
          disabled={!canAdd}
          onClick={() => ctx.onChange(appendAtPath(ctx.root, node.path, defaultValueFor(itemSchema)))}
        >
          {addLabel}
        </Button>
      </div>
      {node.items.length === 0 && (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无条目" />
      )}
      {node.items.map((item, index) => (
        <div
          key={item.pointer}
          className="form-array-item"
          data-pointer={item.pointer}
          style={{
            display: 'flex',
            gap: 8,
            alignItems: 'flex-start',
            border: '1px solid var(--atlas-color-border, #d9d9d9)',
            borderRadius: 6,
            padding: 8,
            marginTop: 8,
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <FormNodeView node={item} ctx={ctx} />
          </div>
          <Button
            size="small"
            danger
            disabled={!canRemove}
            onClick={() => ctx.onChange(removeAtPath(ctx.root, [...node.path, index]))}
          >
            删除
          </Button>
        </div>
      ))}
    </div>
  )
}

function KeyValueView({ node, ctx }: { node: FormKeyValueNode; ctx: ViewContext }): ReactElement {
  const keys = node.entries.map((entry) => entry.key)
  return (
    <div className="property-field form-keyvalue" style={{ marginBottom: 8 }} data-pointer={node.pointer}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Typography.Text type="secondary">
          {node.label}
          {node.required && <Typography.Text type="danger"> *</Typography.Text>}
        </Typography.Text>
        <Button
          size="small"
          onClick={() =>
            ctx.onChange(
              setAtPath(
                ctx.root,
                [...node.path, nextKeyName(keys)],
                defaultValueFor(node.valueSchema),
              ),
            )
          }
        >
          添加
        </Button>
      </div>
      {node.entries.map((entry, index) => (
        // key 用下标：改键名时输入框不重挂载（保证连续输入不丢焦点）
        <div key={index} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginTop: 4 }}>
          <Input
            style={{ width: '40%' }}
            value={entry.key}
            placeholder={node.keyPlaceholder ?? '键名'}
            onChange={(event) => ctx.onChange(renameKeyAtPath(ctx.root, node.path, entry.key, event.target.value))}
          />
          <div style={{ flex: 1 }}>
            <FormNodeView
              node={buildFormTree(node.valueSchema, entry.value, {
                path: [...node.path, entry.key],
                source: ctx.source,
              })}
              ctx={ctx}
            />
          </div>
          <Button size="small" danger onClick={() => ctx.onChange(removeAtPath(ctx.root, [...node.path, entry.key]))}>
            删除
          </Button>
        </div>
      ))}
    </div>
  )
}

function WidgetView({ node, ctx }: { node: FormWidgetNode; ctx: ViewContext }): ReactElement {
  const component = widgetComponent(node.widget, ctx.registry)
  return (
    <Field label={node.label} required={node.required} pointer={node.pointer}>
      {createElement(component, {
        value: node.value,
        onChange: (next: unknown) => ctx.onChange(setAtPath(ctx.root, node.path, next)),
        schema: node.schema,
        scope: ctx.scope,
        nodeId: ctx.nodeId,
        diagnostics: diagnosticsAt(ctx.diagnostics, node.pointer),
        markers: renderMarkers(ctx.diagnostics ?? [], node.pointer),
        placeholder: node.placeholder,
        optionLabels: node.optionLabels,
        rows: node.rows,
      })}
    </Field>
  )
}
