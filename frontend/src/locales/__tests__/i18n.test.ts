import { afterEach, describe, expect, it } from 'vitest'
import { changeLanguage, getLanguage, t, DEFAULT_LOCALE } from '../index'

/** Login.tsx / UserBadge.tsx 实际接线的全部 key（M12 样板范围）。 */
const WIRED_KEYS = [
  'role.viewer',
  'role.operator',
  'role.admin',
  'auth.login.title',
  'auth.login.subtitle',
  'auth.login.username',
  'auth.login.usernameRequired',
  'auth.login.password',
  'auth.login.passwordRequired',
  'auth.login.submit',
  'auth.login.failed',
  'auth.login.seedHintTitle',
  'auth.login.seed.tenantA',
  'auth.login.seed.tenantB',
  'auth.session.logout',
]

/** 纯插值模板：中文来自插值变量，单独验证渲染结果。 */
const TEMPLATE_KEYS = ['auth.login.seed.accountLine']

/** Dashboard.tsx 在 dashboard namespace 下接线的页面专属 key（M12 续批）。 */
const DASHBOARD_KEYS = ['demo.title', 'demo.description', 'demo.cards.graph', 'demo.cards.loop', 'demo.cards.harness']

/** Dashboard 经 common: 前缀取用的跨页通用 key（品牌名/主导航）。 */
const SHARED_NAV_KEYS = ['common:brand.appName', 'common:nav.openEditor', 'common:nav.monitoring', 'common:nav.memory']

/**
 * Editor.tsx + 画布/左栏面板在 editor namespace 下接线的静态（无插值）key（M12 续批 editor-a）。
 * 业务数据（退款原因、节点目录 label/description、模板名、后端枚举）不在此列——它们不抽 key。
 */
const EDITOR_KEYS = [
  'header.title',
  'header.nlGenerate',
  'header.newFromTemplate',
  'header.recordings',
  'header.exportJson',
  'header.publish',
  'header.rollout',
  'header.debug',
  'header.compileAndRun',
  'header.emergencyStop',
  'header.draftRun',
  'tabs.nodes',
  'tabs.variables',
  'nodePanel.title',
  'variables.title',
  'variables.invalidName',
  'variables.duplicateName',
  'variables.column.ref',
  'variables.column.name',
  'variables.column.type',
  'variables.column.value',
  'variables.column.actions',
  'variables.add',
  'canvas.branchDefault',
  'canvas.loopBody',
  'canvas.loopExit',
  'canvas.approvalApproved',
  'canvas.approvalRejected',
  'export.title',
  'runResult.title',
  'nl.title',
  'nl.submit',
  'template.title',
  'template.replaceWarning',
  'template.loading',
  'template.use',
  'recording.title',
  'recording.namePlaceholder',
  'recording.recordButton',
  'recording.recordHint',
  'recording.empty',
  'recording.replay',
  'recording.collapse',
  'recording.edit',
  'recording.deleteConfirm',
  'recording.loadingCase',
  'recording.inputsPlaceholder',
  'recording.editHint',
  'recording.mockCheckbox',
  'recording.overridePlaceholder',
  'recording.match',
  'recording.mismatch',
  'recording.editRequired',
  'approval.title',
  'approval.reject',
  'approval.approve',
  'approval.nodeLabel',
  'approval.summaryLabel',
  'approval.approverLabel',
  'approval.approverUnspecified',
  'approval.decisionApproved',
  'approval.decisionRejected',
  'approval.sourceHuman',
  'approval.sourceTimeout',
  'approval.sourceInput',
  'debug.reason.step',
  'debug.reason.breakpoint',
  'debug.reason.condition',
  'debug.reason.exception',
  'debug.pausedTitle',
  'debug.step',
  'debug.continue',
  'debug.stop',
  'debug.varFilterPlaceholder',
  'debug.globalsHint',
  'debug.outputsHint',
  'debug.exceptionHint',
  'debug.historyEmpty',
  'log.branchDefault',
  'log.loopReasonConditionFalse',
  'log.loopReasonMaxIterations',
  'log.loopReasonExpressionError',
  'log.unknownError',
  'log.noActiveRun',
  'error.compileRunFailed',
  'error.inputsInvalidJson',
  'error.inputsNotObject',
]

/** editor namespace 带插值的 key：给齐变量后不得残留 {{，且应含中文。 */
const EDITOR_TEMPLATE_KEYS: Array<{ key: string; vars: Record<string, unknown> }> = [
  { key: 'header.publishedRun', vars: { version: 3 } },
  { key: 'runResult.compiled', vars: { entrypoints: 'start', terminals: 'end' } },
  { key: 'runResult.status', vars: { status: 'completed' } },
  { key: 'nl.paramWarning', vars: { warning: '缺少字段' } },
  { key: 'template.nodeCount', vars: { count: 5 } },
  { key: 'template.loaded', vars: { name: '退款模板', id: 'tpl-1' } },
  { key: 'recording.listTitle', vars: { count: 2 } },
  { key: 'recording.meta', vars: { nodes: 4, steps: 9, time: '2026/9/21' } },
  { key: 'recording.saved', vars: { id: 'rr-9', steps: 7 } },
  { key: 'recording.defaultName', vars: { graphId: 'g-1', time: 'now' } },
  { key: 'recording.mockToolsTag', vars: { count: 2 } },
  { key: 'recording.stepDiffKeys', vars: { keys: 'result' } },
  { key: 'approval.timeoutHint', vars: { seconds: 30 } },
  { key: 'approval.subgraphLabel', vars: { label: 'sub-refund' } },
  {
    key: 'approval.resultLog',
    vars: { prefix: '', node: 'human_1', decision: '通过', source: '人工', target: 'refund' },
  },
  { key: 'debug.exceptionTitle', vars: { type: 'ValueError' } },
  { key: 'debug.historyTitle', vars: { count: 3 } },
  { key: 'debug.historySince', vars: { path: 'a → b' } },
  { key: 'log.paused', vars: { prefix: '', node: 'cond_1', reason: '断点' } },
  { key: 'log.runComplete', vars: { status: 'completed' } },
]

/** editor 页经 common: 前缀复用的跨页通用键（删除/取消/保存/加载中）。 */
const EDITOR_SHARED_COMMON_KEYS = [
  'common:button.delete',
  'common:button.cancel',
  'common:button.save',
  'common:status.loading',
]

/**
 * PropertyPanel.tsx + DebugConsole.tsx 在 editor namespace 下接线的静态（无插值）key（editor-b）。
 * decision.promptTemplateLabel 携带字面 {{路径}} 教学语法，单独在花括号守护用例断言，不入此表；
 * debugConsole.eventCount 为插值键，亦单独断言。后端校验 message / 节点目录 label 不抽 key。
 */
const EDITOR_B_KEYS = [
  'property.title',
  'property.empty',
  'property.nodeId',
  'property.type',
  'property.nodeName',
  'property.description',
  'property.valid',
  'breakpoint.section',
  'breakpoint.pauseBefore',
  'breakpoint.conditionLabel',
  'breakpoint.conditionHint',
  'breakpoint.hitCountLabel',
  'breakpoint.hitCountPlaceholder',
  'breakpoint.logMessageLabel',
  'breakpoint.logMessagePlaceholder',
  'breakpoint.logMessageHint',
  'breakpoint.exceptionLabel',
  'breakpoint.exceptionHint',
  'retry.section',
  'retry.maxRetries',
  'retry.timeoutSeconds',
  'retry.onError',
  'decision.insertVariable',
  'decision.insertPlaceholder',
  'decision.modelLabel',
  'decision.confidenceLabel',
  'debugConsole.title',
]

const hasChinese = (s: string): boolean => /[\u4e00-\u9fff]/.test(s)

afterEach(() => {
  changeLanguage(DEFAULT_LOCALE)
})

describe('zero-dependency i18n skeleton (docs/17 §2.3, M12)', () => {
  it('defaults to zh-CN', () => {
    expect(getLanguage()).toBe('zh-CN')
  })

  it('resolves nested dotted keys', () => {
    expect(t('auth.login.submit')).toBe('登录')
    expect(t('role.admin')).toBe('管理员')
  })

  it('interpolates {{var}} placeholders', () => {
    expect(
      t('auth.login.seed.accountLine', { user: 'admin-a', pass: 'admin123', role: '管理员' }),
    ).toBe('admin-a / admin123（管理员）')
  })

  it('keeps an unknown interpolation placeholder as-is', () => {
    const out = t('auth.login.seed.accountLine', { user: 'admin-a', pass: 'admin123' })
    expect(out).toContain('{{role}}')
  })

  it('returns the key itself when missing (i18next behavior)', () => {
    expect(t('does.not.exist')).toBe('does.not.exist')
  })

  it('uses defaultValue when the key is missing', () => {
    expect(t('nope.x', { defaultValue: '兜底' })).toBe('兜底')
  })

  it('falls back from an empty namespace to common for the same key', () => {
    // editor.json is an empty {} placeholder; common holds auth.login.submit
    expect(t('editor:auth.login.submit')).toBe('登录')
  })

  it('returns the key for an unknown namespace prefix (i18next behavior)', () => {
    expect(t('wat:role.viewer')).toBe('wat:role.viewer')
  })

  it('falls back to zh-CN text when switched to an empty en-US skeleton', () => {
    changeLanguage('en-US')
    expect(getLanguage()).toBe('en-US')
    // en-US/common.json is {} — must surface Chinese, never the raw key
    expect(t('auth.login.submit')).toBe('登录')
    changeLanguage('zh-CN')
    expect(t('auth.login.submit')).toBe('登录')
  })

  it('ignores an unsupported language in changeLanguage', () => {
    changeLanguage('fr-FR' as never)
    expect(getLanguage()).toBe('zh-CN')
  })

  it('every wired key resolves to a non-empty Chinese string in zh-CN', () => {
    for (const key of WIRED_KEYS) {
      const value = t(key)
      expect(typeof value).toBe('string')
      expect(value.length).toBeGreaterThan(0)
      expect(value).not.toBe(key)
      expect(hasChinese(value), `${key} should carry Chinese copy`).toBe(true)
    }
  })

  it('interpolation templates render Chinese once a role variable is supplied', () => {
    const rendered = t('auth.login.seed.accountLine', {
      user: 'admin-a',
      pass: 'admin123',
      role: t('role.admin'),
    })
    expect(rendered).toBe('admin-a / admin123（管理员）')
    expect(hasChinese(rendered)).toBe(true)
  })

  it('every wired key still resolves under the empty en-US skeleton (never raw key)', () => {
    changeLanguage('en-US')
    for (const key of [...WIRED_KEYS, ...TEMPLATE_KEYS]) {
      expect(t(key)).not.toBe(key)
    }
  })

  it('resolves dashboard namespace copy for the home page (second wired namespace)', () => {
    for (const key of DASHBOARD_KEYS) {
      const value = t(key, { ns: 'dashboard' })
      expect(value).not.toBe(key)
      expect(value.length).toBeGreaterThan(0)
      expect(hasChinese(value), `${key} should carry Chinese copy`).toBe(true)
    }
    expect(t('demo.title', { ns: 'dashboard' })).toContain('电商退款自动化')
  })

  it('reaches shared brand/nav copy via the common: prefix from a dashboard hook', () => {
    // Mirrors Dashboard.tsx: useTranslation('dashboard') then t('common:...')
    expect(t('common:brand.appName', { ns: 'dashboard' })).toBe('Atlas 运营体编排平台')
    expect(t('common:nav.openEditor', { ns: 'dashboard' })).toBe('打开流程编辑器')
    for (const key of SHARED_NAV_KEYS) {
      const value = t(key, { ns: 'dashboard' })
      expect(value).not.toBe(key)
      expect(hasChinese(value), `${key} should carry Chinese copy`).toBe(true)
    }
  })

  it('falls back to zh-CN for dashboard copy under the empty en-US skeleton', () => {
    changeLanguage('en-US')
    // en-US/dashboard.json stays {} (no translation yet); Chinese must still render
    for (const key of DASHBOARD_KEYS) {
      expect(t(key, { ns: 'dashboard' })).not.toBe(key)
    }
    for (const key of SHARED_NAV_KEYS) {
      expect(t(key, { ns: 'dashboard' })).not.toBe(key)
    }
  })

  it('resolves editor namespace static copy for frame, canvas and left panels (editor-a)', () => {
    for (const key of EDITOR_KEYS) {
      const value = t(key, { ns: 'editor' })
      expect(value, `${key} must resolve`).not.toBe(key)
      expect(value.length, `${key} must be non-empty`).toBeGreaterThan(0)
      expect(hasChinese(value), `${key} should carry Chinese copy`).toBe(true)
    }
  })

  it('renders editor interpolation templates without leftover placeholders', () => {
    for (const { key, vars } of EDITOR_TEMPLATE_KEYS) {
      const value = t(key, { ns: 'editor', ...vars })
      expect(value, `${key} must resolve`).not.toBe(key)
      expect(value, `${key} must not leave a {{placeholder}}`).not.toContain('{{')
      expect(hasChinese(value), `${key} should carry Chinese copy`).toBe(true)
    }
  })

  it('keeps literal braces in editor hint/override copy (Chinese var names and JSON samples)', () => {
    // variables.hint teaches the {{变量路径}} / {{global.company_name}} syntax: neither token
    // matches the interpolate identifier rule, so both must survive verbatim and stay Chinese.
    const hint = t('variables.hint', { ns: 'editor' })
    expect(hint).toContain('{{变量路径}}')
    expect(hint).toContain('{{global.company_name}}')
    // overridePlaceholder carries a single-brace JSON sample; it must render and never be treated
    // as an interpolation (no double braces introduced).
    const override = t('recording.overridePlaceholder', { ns: 'editor' })
    expect(override).toContain('{"amount": 100}')
    expect(override).not.toContain('{{')
    // log.toolHttpStatus is a technical-proper-noun fragment (HTTP kept English per docs/17);
    // it carries no Han characters but must still interpolate the status code cleanly.
    const http = t('log.toolHttpStatus', { ns: 'editor', status: 200 })
    expect(http).toBe('（HTTP 200）')
    expect(http).not.toContain('{{')
  })

  it('reaches shared common copy via the common: prefix from an editor hook', () => {
    for (const key of EDITOR_SHARED_COMMON_KEYS) {
      const value = t(key, { ns: 'editor' })
      expect(value, `${key} must resolve`).not.toBe(key)
      expect(hasChinese(value), `${key} should carry Chinese copy`).toBe(true)
    }
    expect(t('common:button.delete', { ns: 'editor' })).toBe('删除')
    expect(t('common:status.loading', { ns: 'editor' })).toBe('加载中…')
  })

  it('falls back from a missing editor key to common, then zh-CN (never a raw key)', () => {
    // editor.json is now populated but still lacks common-only keys such as auth.login.submit;
    // the namespace-level common fallback must still apply (regression guard for editor != {}).
    expect(t('editor:auth.login.submit')).toBe('登录')
    // Unknown debug reason enum falls back to the supplied defaultValue (backend data, untranslated).
    expect(t('debug.reason.future_reason', { ns: 'editor', defaultValue: 'future_reason' })).toBe(
      'future_reason',
    )
    expect(t('debug.reason.step', { ns: 'editor' })).toBe('单步')
  })

  it('resolves editor namespace static copy for property panel and debug console (editor-b)', () => {
    for (const key of EDITOR_B_KEYS) {
      const value = t(key, { ns: 'editor' })
      expect(value, `${key} must resolve`).not.toBe(key)
      expect(value.length, `${key} must be non-empty`).toBeGreaterThan(0)
      expect(hasChinese(value), `${key} should carry Chinese copy`).toBe(true)
    }
  })

  it('renders debug console event count and keeps literal braces in prompt label', () => {
    // debugConsole.eventCount interpolates the live log count.
    const count = t('debugConsole.eventCount', { ns: 'editor', count: 7 })
    expect(count).toBe('7 条事件')
    expect(count).not.toContain('{{')
    // decision.promptTemplateLabel teaches the literal {{路径}} reference syntax; the Chinese
    // variable name does not match the interpolate identifier rule and must survive verbatim.
    const promptLabel = t('decision.promptTemplateLabel', { ns: 'editor' })
    expect(promptLabel).toContain('{{路径}}')
    expect(hasChinese(promptLabel)).toBe(true)
  })

  it('falls back to zh-CN for editor copy under the empty en-US skeleton', () => {
    changeLanguage('en-US')
    for (const key of EDITOR_KEYS) {
      expect(t(key, { ns: 'editor' })).not.toBe(key)
    }
    for (const { key, vars } of EDITOR_TEMPLATE_KEYS) {
      expect(t(key, { ns: 'editor', ...vars })).not.toBe(key)
    }
    for (const key of EDITOR_B_KEYS) {
      expect(t(key, { ns: 'editor' })).not.toBe(key)
    }
    expect(t('debugConsole.eventCount', { ns: 'editor', count: 3 })).not.toContain('{{')
    expect(t('decision.promptTemplateLabel', { ns: 'editor' })).toContain('{{路径}}')
  })

  it('registers empty monitoring/memory namespaces without breaking resolution', () => {
    // These namespaces are {} placeholders in this batch; missing keys return the key itself
    // rather than throwing, and common fallback still works from them.
    expect(t('monitoring.anything', { ns: 'monitoring' })).toBe('monitoring.anything')
    expect(t('memory.anything', { ns: 'memory' })).toBe('memory.anything')
    expect(t('common:button.save', { ns: 'monitoring' })).toBe('保存')
  })
})
