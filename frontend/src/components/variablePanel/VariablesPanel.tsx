import { useState } from 'react'
import { Button, Card, Input, Select, Space, Table, Tag, Typography } from 'antd'
import { useEditorStore } from '../../store/editorStore'
import {
  isValidVariableName,
  VARIABLE_TYPES,
  type GraphVariable,
  type VariableSource,
  type VariableType,
} from '../../lib/variables'
import { useTranslation } from '../../locales'

const SOURCE_OPTIONS = [
  { value: '', label: '' },
  { value: 'env', label: 'Env' },
  { value: 'secret', label: 'Secret' },
]

export function VariablesPanel() {
  const { t } = useTranslation('editor')
  const variables = useEditorStore((state) => state.variables)
  const addVariable = useEditorStore((state) => state.addVariable)
  const removeVariable = useEditorStore((state) => state.removeVariable)

  const [name, setName] = useState('')
  const [type, setType] = useState<VariableType>('string')
  const [value, setValue] = useState('')
  const [source, setSource] = useState<VariableSource | ''>('')
  const [error, setError] = useState('')

  const submit = () => {
    const trimmed = name.trim()
    if (!isValidVariableName(trimmed)) {
      setError(t('variables.invalidName'))
      return
    }
    if (variables.some((variable) => variable.name === trimmed)) {
      setError(t('variables.duplicateName'))
      return
    }
    // 打包 A3（docs/99 §2.1）：受限来源时 value 只存引用名（Secret 型输入框不显示明文）。
    const variable: GraphVariable = {
      name: trimmed,
      type,
      value,
      scope: 'global',
      ...(source ? { source } : {}),
    }
    addVariable(variable)
    setName('')
    setValue('')
    setSource('')
    setError('')
  }

  const sourceLabel = (row: GraphVariable) => {
    if (!row.source) return t('variables.sourcePlain')
    return row.source === 'env' ? t('variables.sourceEnv') : t('variables.sourceSecret')
  }

  const columns = [
    { title: t('variables.column.ref'), dataIndex: 'ref', render: (_: string, row: GraphVariable) => `{{global.${row.name}}}` },
    { title: t('variables.column.name'), dataIndex: 'name' },
    {
      title: t('variables.column.source'),
      dataIndex: 'source',
      render: (_: unknown, row: GraphVariable) => (
        <Tag color={row.source === 'secret' ? 'red' : row.source === 'env' ? 'blue' : 'default'}>
          {sourceLabel(row)}
        </Tag>
      ),
    },
    { title: t('variables.column.type'), dataIndex: 'type' },
    {
      title: t('variables.column.value'),
      dataIndex: 'value',
      ellipsis: true,
      render: (raw: string, row: GraphVariable) =>
        row.source === 'secret' ? '••••' : raw,
    },
    {
      title: t('variables.column.actions'),
      render: (_: unknown, row: GraphVariable) => (
        <Button size="small" danger onClick={() => removeVariable(row.name)}>
          {t('common:button.delete')}
        </Button>
      ),
    },
  ]

  return (
    <Card className="side-card" title={t('variables.title')} size="small">
      <Space orientation="vertical" style={{ width: '100%' }} size="small">
        <Typography.Text type="secondary">
          {t('variables.hint')}
        </Typography.Text>
        <Input
          placeholder={t('variables.namePlaceholder')}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <Space style={{ width: '100%' }}>
          <Select
            value={type}
            onChange={setType}
            style={{ width: 110 }}
            options={VARIABLE_TYPES.map((value) => ({ value, label: value }))}
          />
          <Select
            value={source}
            onChange={setSource}
            style={{ width: 100 }}
            placeholder={t('variables.sourcePlaceholder')}
            options={SOURCE_OPTIONS.map((option) => ({
              value: option.value,
              label: option.value === 'env' ? t('variables.sourceEnv') : option.value === 'secret' ? t('variables.sourceSecret') : t('variables.sourcePlain'),
            }))}
          />
          <Input
            placeholder={
              source === 'secret' ? t('variables.secretValueHint') : t('variables.valuePlaceholder')
            }
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
        </Space>
        {error && <Typography.Text type="danger">{error}</Typography.Text>}
        <Button type="primary" size="small" onClick={submit} block>
          {t('variables.add')}
        </Button>
        <Table
          size="small"
          rowKey="name"
          columns={columns}
          dataSource={variables}
          pagination={false}
        />
      </Space>
    </Card>
  )
}
