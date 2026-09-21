import { useState } from 'react'
import { Button, Card, Input, Select, Space, Table, Typography } from 'antd'
import { useEditorStore } from '../../store/editorStore'
import {
  isValidVariableName,
  VARIABLE_TYPES,
  type GraphVariable,
  type VariableType,
} from '../../lib/variables'
import { useTranslation } from '../../locales'

export function VariablesPanel() {
  const { t } = useTranslation('editor')
  const variables = useEditorStore((state) => state.variables)
  const addVariable = useEditorStore((state) => state.addVariable)
  const removeVariable = useEditorStore((state) => state.removeVariable)

  const [name, setName] = useState('')
  const [type, setType] = useState<VariableType>('string')
  const [value, setValue] = useState('')
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
    const variable: GraphVariable = { name: trimmed, type, value, scope: 'global' }
    addVariable(variable)
    setName('')
    setValue('')
    setError('')
  }

  const columns = [
    { title: t('variables.column.ref'), dataIndex: 'ref', render: (_: string, row: GraphVariable) => `{{global.${row.name}}}` },
    { title: t('variables.column.name'), dataIndex: 'name' },
    { title: t('variables.column.type'), dataIndex: 'type' },
    { title: t('variables.column.value'), dataIndex: 'value', ellipsis: true },
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
          <Input
            placeholder={t('variables.valuePlaceholder')}
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
