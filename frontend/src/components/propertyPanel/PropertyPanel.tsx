import {
  Button,
  Card,
  Empty,
  Input,
  InputNumber,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
} from 'antd'
import { useEffect, useState } from 'react'
import { useEditorStore, validateNodeId } from '../../store/editorStore'
import {
  NODE_CATALOG,
  ON_ERROR_STRATEGIES,
  type NodeConfig,
} from '../../lib/nodeCatalog'
import { useScopeIndex, useToolOutputSchemas } from '../../lib/useScope'
import { useNodeDiagnostics } from '../../store/validationStore'
import { validateExpression } from '../../lib/conditions'
import { ConditionConfig } from './ConditionConfig'
import { LoopConfig } from './LoopConfig'
import { ParallelConfig } from './ParallelConfig'
import { WaitConfig } from './WaitConfig'
import { SubgraphConfig } from './SubgraphConfig'
import { HumanApprovalConfig } from './HumanApprovalConfig'
import { ToolCallConfig } from './ToolCallConfig'
import { NodeConfigForm } from '../../lib/forms/NodeConfigForm'
import { useTranslation } from '../../locales'

export function PropertyPanel() {
  const { t } = useTranslation('editor')
  const nodes = useEditorStore((state) => state.nodes)
  const edges = useEditorStore((state) => state.edges)
  const variables = useEditorStore((state) => state.variables)
  const selectedNodeId = useEditorStore((state) => state.selectedNodeId)
  const updateSelectedNode = useEditorStore((state) => state.updateSelectedNode)
  const updateSelectedConfig = useEditorStore((state) => state.updateSelectedConfig)
  const deleteSelectedNode = useEditorStore((state) => state.deleteSelectedNode)
  const breakpoints = useEditorStore((state) => state.breakpoints)
  const toggleBreakpoint = useEditorStore((state) => state.toggleBreakpoint)
  const setBreakpointExpression = useEditorStore(
    (state) => state.setBreakpointExpression,
  )
  const setBreakpointHitCount = useEditorStore((state) => state.setBreakpointHitCount)
  const setBreakpointLogMessage = useEditorStore((state) => state.setBreakpointLogMessage)
  const toggleBreakpointException = useEditorStore(
    (state) => state.toggleBreakpointException,
  )

  const scope = useScopeIndex({ nodes, edges, variables })
  const toolOutputSchemas = useToolOutputSchemas()
  // M4 批 2 ⑦：诊断来自分层校验引擎结果 store（L1 同步、L2 防抖）。
  const engineDiagnostics = useNodeDiagnostics(selectedNodeId ?? '')
  const renameNode = useEditorStore((state) => state.renameNode)

  // D30/B3：节点 ID 可编辑重命名（草稿态；切换选中节点时同步并清错）。
  const [idDraft, setIdDraft] = useState('')
  const [idError, setIdError] = useState<string | null>(null)
  useEffect(() => {
    setIdDraft(nodes.find((node) => node.id === selectedNodeId)?.id ?? '')
    setIdError(null)
    // 仅随选中节点切换重置；编辑草稿不依赖 nodes，避免输入中被冲掉。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedNodeId])

  const selectedNode = nodes.find((node) => node.id === selectedNodeId)

  if (!selectedNode) {
    return (
      <Card className="side-card" title={t('property.title')} size="small">
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('property.empty')} />
      </Card>
    )
  }

  const { data } = selectedNode
  const meta = NODE_CATALOG[data.kind]
  const diagnostics = engineDiagnostics
  const config = data.config
  const variablePaths = scope.listPathsAt(selectedNode.id, toolOutputSchemas)
  const targetOptions = nodes
    .filter((node) => node.id !== selectedNode.id)
    .map((node) => ({ value: node.id, label: `${node.data.label}（${node.id}）` }))

  const commitRename = () => {
    if (!selectedNodeId) return
    const trimmed = idDraft.trim()
    if (trimmed === selectedNodeId) {
      setIdError(null)
      setIdDraft(selectedNodeId)
      return
    }
    const error = validateNodeId(idDraft, selectedNodeId, nodes.map((node) => node.id))
    if (error) {
      setIdError(error)
      return
    }
    renameNode(selectedNodeId, idDraft)
    setIdError(null)
  }

  const insertVariable = (path: string) => {
    if (!path) return
    if (data.kind === 'ai_decision') {
      updateSelectedConfig({ promptTemplate: `${config.promptTemplate ?? ''}{{${path}}} ` })
    } else if (data.kind === 'tool_call') {
      updateSelectedConfig({ params: `${config.params ?? ''}{{${path}}} ` })
    }
  }

  return (
    <Card
      className="side-card"
      title={t('property.title')}
      size="small"
      extra={
        <Button size="small" danger onClick={deleteSelectedNode}>
          {t('common:button.delete')}
        </Button>
      }
    >
      <Space orientation="vertical" style={{ width: '100%' }} size="small">
        <div>
          <Typography.Text type="secondary">{t('property.nodeId')}</Typography.Text>
          <Input
            size="small"
            value={idDraft}
            status={idError ? 'error' : undefined}
            aria-label={t('property.nodeId')}
            onChange={(event) => {
              setIdDraft(event.target.value)
              setIdError(null)
            }}
            onPressEnter={commitRename}
            onBlur={commitRename}
          />
          {idError ? (
            <div>
              <Typography.Text type="danger" style={{ fontSize: 12 }}>
                {idError}
              </Typography.Text>
            </div>
          ) : null}
        </div>
        <div>
          <Typography.Text type="secondary">{t('property.type')}</Typography.Text>
          <div>
            <Tag color={meta.color}>{meta.label}</Tag>
          </div>
        </div>
        <Field label={t('property.nodeName')}>
          <Input
            value={data.label}
            status={!data.label.trim() ? 'error' : undefined}
            onChange={(event) => updateSelectedNode({ label: event.target.value })}
          />
        </Field>
        <Field label={t('property.description')}>
          <Input.TextArea
            rows={2}
            value={data.description}
            onChange={(event) => updateSelectedNode({ description: event.target.value })}
          />
        </Field>

        {data.kind === 'trigger' && (
          <NodeConfigForm
            kind="trigger"
            config={config}
            update={updateSelectedConfig}
            variablePaths={[]}
            targetOptions={[]}
            diagnostics={diagnostics}
          />
        )}
        {data.kind === 'ai_decision' && (
          <DecisionConfig
            config={config}
            update={updateSelectedConfig}
            variablePaths={variablePaths}
            onInsert={insertVariable}
          />
        )}
        {data.kind === 'condition' && (
          <ConditionConfig
            config={config}
            update={updateSelectedConfig}
            variablePaths={variablePaths}
            targetOptions={targetOptions}
            diagnostics={diagnostics}
          />
        )}
        {data.kind === 'loop' && (
          <LoopConfig
            config={config}
            update={updateSelectedConfig}
            variablePaths={variablePaths}
            targetOptions={targetOptions}
            diagnostics={diagnostics}
          />
        )}
        {data.kind === 'parallel' && (
          <ParallelConfig
            config={config}
            update={updateSelectedConfig}
            targetOptions={targetOptions}
            diagnostics={diagnostics}
          />
        )}
        {data.kind === 'wait' && <WaitConfig config={config} update={updateSelectedConfig} />}
        {data.kind === 'subgraph' && (
          <SubgraphConfig
            config={config}
            update={updateSelectedConfig}
            variablePaths={variablePaths}
            targetOptions={targetOptions}
            diagnostics={diagnostics}
          />
        )}
        {data.kind === 'human_approval' && (
          <HumanApprovalConfig
            config={config}
            update={updateSelectedConfig}
            variablePaths={variablePaths}
            targetOptions={targetOptions}
            diagnostics={diagnostics}
          />
        )}
        {data.kind === 'tool_call' && (
          <ToolCallConfig
            config={config}
            update={updateSelectedConfig}
            variablePaths={variablePaths}
            onInsert={insertVariable}
            nodeId={selectedNode.id}
          />
        )}

        <Typography.Text strong>{t('breakpoint.section')}</Typography.Text>
        <Field label={t('breakpoint.pauseBefore')}>
          <Switch
            checked={selectedNode.id in breakpoints}
            onChange={() => toggleBreakpoint(selectedNode.id)}
          />
        </Field>
        {selectedNode.id in breakpoints && (
          <Field label={t('breakpoint.conditionLabel')}>
            <Input
              value={breakpoints[selectedNode.id]?.expression ?? ''}
              placeholder="{{trigger-1.context.payload.amount}} > 1000"
              status={
                breakpoints[selectedNode.id]?.expression?.trim() &&
                validateExpression(breakpoints[selectedNode.id]!.expression!.trim()).length > 0
                  ? 'error'
                  : undefined
              }
              onChange={(event) =>
                setBreakpointExpression(selectedNode.id, event.target.value)
              }
            />
            {breakpoints[selectedNode.id]?.expression?.trim() &&
              validateExpression(breakpoints[selectedNode.id]!.expression!.trim()).map(
                (message) => (
                  <Typography.Text key={message} type="danger">
                    {message}
                  </Typography.Text>
                ),
              )}
            <Typography.Text type="secondary">{t('breakpoint.conditionHint')}</Typography.Text>
          </Field>
        )}
        {selectedNode.id in breakpoints && (
          <Field label={t('breakpoint.hitCountLabel')}>
            <InputNumber
              min={1}
              precision={0}
              style={{ width: '100%' }}
              placeholder={t('breakpoint.hitCountPlaceholder')}
              value={breakpoints[selectedNode.id]?.hitCount ?? null}
              onChange={(value) =>
                setBreakpointHitCount(selectedNode.id, (value as number | null) ?? null)
              }
            />
          </Field>
        )}
        {selectedNode.id in breakpoints && (
          <Field label={t('breakpoint.logMessageLabel')}>
            <Input
              value={breakpoints[selectedNode.id]?.logMessage ?? ''}
              placeholder={t('breakpoint.logMessagePlaceholder')}
              onChange={(event) =>
                setBreakpointLogMessage(selectedNode.id, event.target.value)
              }
            />
            <Typography.Text type="secondary">{t('breakpoint.logMessageHint')}</Typography.Text>
          </Field>
        )}
        <Field label={t('breakpoint.exceptionLabel')}>
          <Switch
            checked={!!breakpoints[selectedNode.id]?.onException}
            onChange={() => toggleBreakpointException(selectedNode.id)}
          />
          <Typography.Text type="secondary">{t('breakpoint.exceptionHint')}</Typography.Text>
        </Field>

        <Typography.Text strong>{t('retry.section')}</Typography.Text>
        <Field label={t('retry.maxRetries')}>
          <InputNumber
            min={0}
            value={data.retry.maxRetries}
            onChange={(value) =>
              updateSelectedNode({ retry: { ...data.retry, maxRetries: value ?? 0 } })
            }
          />
        </Field>
        <Field label={t('retry.timeoutSeconds')}>
          <InputNumber
            min={1}
            value={data.retry.timeout}
            onChange={(value) =>
              updateSelectedNode({ retry: { ...data.retry, timeout: value ?? 30 } })
            }
          />
        </Field>
        <Field label={t('retry.onError')}>
          <Select
            value={data.retry.onError}
            style={{ width: '100%' }}
            onChange={(value) => updateSelectedNode({ retry: { ...data.retry, onError: value } })}
            options={ON_ERROR_STRATEGIES.map((value) => ({ value, label: value }))}
          />
        </Field>

        {diagnostics.length > 0 ? (
          <div className="property-errors">
            {diagnostics.map((diagnostic, index) => (
              <div key={`${diagnostic.code}-${index}`}>• {diagnostic.message}</div>
            ))}
          </div>
        ) : (
          <Typography.Text type="success">{t('property.valid')}</Typography.Text>
        )}
      </Space>
    </Card>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="property-field">
      <Typography.Text type="secondary">{label}</Typography.Text>
      {children}
    </label>
  )
}

type ConfigProps = {
  config: NodeConfig
  update: (patch: Partial<NodeConfig>) => void
}

type VariableInsertProps = ConfigProps & {
  variablePaths: string[]
  onInsert: (path: string) => void
}

function DecisionConfig({ config, update, variablePaths, onInsert }: VariableInsertProps) {
  const { t } = useTranslation('editor')
  return (
    <>
      <Field label={t('decision.promptTemplateLabel')}>
        <Input.TextArea
          rows={4}
          value={config.promptTemplate}
          status={!config.promptTemplate?.trim() ? 'error' : undefined}
          onChange={(event) => update({ promptTemplate: event.target.value })}
        />
      </Field>
      <Field label={t('decision.insertVariable')}>
        <Select
          style={{ width: '100%' }}
          value={undefined}
          placeholder={t('decision.insertPlaceholder')}
          onChange={onInsert}
          options={variablePaths.map((path) => ({ value: path, label: `{{${path}}}` }))}
        />
      </Field>
      <Field label={t('decision.modelLabel')}>
        <Input value={config.model} onChange={(event) => update({ model: event.target.value })} />
      </Field>
      <Field label={t('decision.confidenceLabel')}>
        <InputNumber
          min={0}
          max={1}
          step={0.05}
          value={config.confidenceThreshold}
          onChange={(value) => update({ confidenceThreshold: value ?? 0.6 })}
        />
      </Field>
    </>
  )
}
