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

/**
 * Monitoring.tsx 在 monitoring namespace 下接线的静态（无插值）key（M12 续批 monitoring）。
 * 插值键见 MONITORING_TEMPLATE_KEYS；custom.hint 携带教学用字面 {{status}}/{{hasError}}，单独守护；
 * 后端告警 message / 校验 message / 英文表达式 placeholder / 技术数据（ruleId、错误码）不抽 key。
 */
const MONITORING_KEYS = [
  'title',
  'header.refresh',
  'header.back',
  'header.unresolvedBadge',
  'col.rule',
  'col.graph',
  'col.message',
  'col.count',
  'col.lastRun',
  'col.status',
  'col.lastSeen',
  'col.actions',
  'col.run',
  'col.version',
  'col.mode',
  'col.nodes',
  'col.duration',
  'col.started',
  'col.time',
  'col.graphId',
  'col.trigger',
  'col.passRate',
  'col.passTotal',
  'col.blocked',
  'col.samples',
  'col.tool',
  'col.calls',
  'col.failed',
  'col.simulated',
  'col.errorCodes',
  'col.node',
  'col.type',
  'col.error',
  'severity.critical',
  'severity.warning',
  'rollback.auto',
  'rollback.manual',
  'alertStatus.open',
  'alertStatus.acknowledged',
  'alertStatus.resolved',
  'alert.acknowledge',
  'alert.resolve',
  'version.draft',
  'mode.sync',
  'mode.stream',
  'health.error',
  'health.unhealthy',
  'health.healthy',
  'trigger.publishGate',
  'trigger.manualGate',
  'report.uncovered',
  'blocked.yes',
  'blocked.no',
  'nodeResult.failed',
  'nodeResult.success',
  'metric.total',
  'metric.healthy',
  'metric.unhealthy',
  'metric.successRate',
  'metric.p50',
  'metric.p95',
  'reportCard.title',
  'reportCard.subtitle',
  'business.autoRefundRate',
  'business.manualEscalationRate',
  'business.amountDiffRate',
  'business.cardTitle',
  'business.colAutoRefundRate',
  'business.colManualEscalationRate',
  'business.colAmountDiffRate',
  'tool.cardTitle',
  'alertCard.title',
  'filter.allStatus',
  'filter.allGraphs',
  'rules.cardTitle',
  'rules.saveFailed',
  'rules.saved',
  'rules.save',
  'builtinRule.runError',
  'builtinRule.nodeFailed',
  'builtinRule.consecutiveFailures',
  'builtinRule.failureRateName',
  'builtinRule.rolloutGate',
  'ruleForm.threshold',
  'ruleForm.window',
  'ruleForm.minSamples',
  'ruleForm.failureRate',
  'ruleForm.rate',
  'ruleForm.unitTimes',
  'custom.title',
  'custom.namePlaceholder',
  'custom.enabled',
  'custom.nameEmpty',
  'custom.add',
  'runsCard.title',
  'empty.reports',
  'empty.business',
  'empty.tools',
  'empty.alerts',
  'empty.runs',
  'shadow.modal.title',
  'shadow.modal.hint',
  'shadow.modal.submit',
  'shadow.modal.inputsLabel',
  'shadow.modal.inputsHelp',
  'shadow.modal.humanLabel',
  'shadow.modal.humanPlaceholder',
  'shadow.modal.noteLabel',
  'shadow.modal.notePlaceholder',
  'shadow.result.error',
  'shadow.result.verdict',
  'shadow.result.autoAction',
  'shadow.result.humanAction',
  'shadow.result.noHuman',
  'shadow.result.empty',
  'shadow.result.intentsTitle',
  'shadow.result.decisionsTitle',
  'shadow.card.title',
  'shadow.card.autoAction',
  'shadow.card.verdict',
  'shadow.card.human',
  'shadow.card.noHuman',
  'shadow.card.selectHuman',
  'shadow.card.attach',
  'shadow.card.empty',
  'shadow.col.id',
  'shadow.col.graph',
  'shadow.col.node',
  'shadow.col.nodeType',
  'shadow.col.target',
  'shadow.col.tool',
  'shadow.col.permission',
  'shadow.col.intent',
  'shadow.col.parameters',
  'shadow.col.createdAt',
  'shadow.intent.dryRun',
  'shadow.intent.passThrough',
  'shadow.intent.simulated',
  'shadow.intent.failed',
  'shadow.verdict.consistent',
  'shadow.verdict.mismatch',
  'shadow.verdict.pending',
  'shadow.action.refunded',
  'shadow.action.humanReview',
  'shadow.error.inputsInvalidJson',
  'shadow.error.inputsNotObject',
  'trace.tabNodes',
  'trace.tabTimeline',
  'trace.error',
  'trace.retry',
  'trace.empty',
  'trace.col.name',
  'trace.col.timeline',
  'trace.kind.run',
  'trace.kind.node',
  'trace.kind.tool',
  'trace.kind.parallel',
  'trace.kind.subgraph',
  'trace.kind.taskDispatch',
  'trace.kind.taskDone',
  'trace.kind.approval',
  'col.assignee',
  'escalation.tag',
  'ruleForm.escalationLabel',
  'ruleForm.escalationUnit',
  'onCall.label',
  'onCall.loading',
  'onCall.nobody',
  'onCall.rotate',
  'onCall.configure',
  'onCall.modalTitle',
  'onCall.modalHint',
  'onCall.placeholder',
  'onCall.save',
  'onCall.error.empty',
  'onCall.error.tooMany',
  'silence.action',
  'silence.popTitle',
  'silence.durationLabel',
  'silence.durationCustom',
  'silence.reasonLabel',
  'silence.reasonPlaceholder',
  'silence.confirm',
  'silence.error.reasonRequired',
  'silence.error.durationRange',
  'silence.col.rule',
  'silence.col.graph',
  'silence.col.reason',
  'silence.col.createdBy',
  'silence.col.expiresAt',
  'silence.col.suppressed',
  'silence.col.status',
  'silence.col.actions',
  'silence.allRules',
  'silence.allGraphs',
  'silence.active',
  'silence.expired',
  'silence.delete',
  'silence.deleteConfirm',
]

/** monitoring namespace 带插值的 key：给齐变量后不得残留 {{，且应含中文。 */
const MONITORING_TEMPLATE_KEYS: Array<{ key: string; vars: Record<string, unknown> }> = [
  { key: 'rollback.tag', vars: { actor: '自动', from: 3, to: 2 } },
  { key: 'run.nodeSummary', vars: { ok: 3, failed: 1 } },
  { key: 'run.uncaughtError', vars: { error: 'RuntimeError: x' } },
  { key: 'rules.nameEmpty', vars: { cid: 'c-1' } },
  { key: 'rules.exprInvalid', vars: { name: '错误即告警', errors: '变量未定义' } },
  { key: 'onCall.rotationHint', vars: { index: 1, total: 3 } },
  { key: 'silence.durationMinutes', vars: { minutes: 30 } },
  { key: 'silence.managerTitle', vars: { active: 1, total: 2 } },
]

/**
 * Memory.tsx 在 memory namespace 下接线的全部静态 key（M12 续批 memory；本 namespace 无插值键）。
 * lib/memory.ts 的 MEMORY_KIND_LABELS 改返 i18n key 由页面 t() 解析；buildMemoryPayload/parseStringMapText
 * 的表单校验 message 与 conditions 校验 message 同例原样上屏（不抽 key）；JSON 示例 placeholder 不抽。
 */
const MEMORY_KEYS = [
  'title',
  'header.refresh',
  'header.back',
  'notice.message',
  'notice.description',
  'col.content',
  'col.kind',
  'col.confidence',
  'col.scope',
  'col.createdAt',
  'col.actions',
  'col.score',
  'kind.fact',
  'kind.preference',
  'button.edit',
  'button.search',
  'deleteConfirm.title',
  'deleteConfirm.description',
  'filter.allKinds',
  'search.cardTitle',
  'search.placeholder',
  'search.empty',
  'search.queryRequired',
  'list.cardTitle',
  'list.create',
  'list.empty',
  'error.loadList',
  'error.search',
  'error.save',
  'error.formInvalid',
  'modal.createTitle',
  'modal.editTitle',
  'modal.sourceHint',
  'modal.kindLabel',
  'modal.contentLabel',
  'modal.contentPlaceholder',
  'modal.confidenceLabel',
  'modal.scopeLabel',
  'modal.metadataLabel',
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

  it('resolves monitoring namespace static copy for the monitoring page', () => {
    for (const key of MONITORING_KEYS) {
      const value = t(key, { ns: 'monitoring' })
      expect(value, `${key} must resolve`).not.toBe(key)
      expect(value.length, `${key} must be non-empty`).toBeGreaterThan(0)
      expect(hasChinese(value), `${key} should carry Chinese copy`).toBe(true)
    }
  })

  it('renders monitoring interpolation templates without leftover placeholders', () => {
    for (const { key, vars } of MONITORING_TEMPLATE_KEYS) {
      const value = t(key, { ns: 'monitoring', ...vars })
      expect(value, `${key} must resolve`).not.toBe(key)
      expect(value, `${key} must not leave a {{placeholder}}`).not.toContain('{{')
      expect(hasChinese(value), `${key} should carry Chinese copy`).toBe(true)
    }
    // Spot-check the composed rollback tag and node summary shapes.
    expect(t('rollback.tag', { ns: 'monitoring', actor: '自动', from: 3, to: 2 })).toBe(
      '自动回滚 v3 → v2',
    )
    expect(t('run.nodeSummary', { ns: 'monitoring', ok: 3, failed: 1 })).toBe('3 成 / 1 败')
  })

  it('keeps the literal teaching braces in the custom-rule hint and resolves lib label keys', () => {
    // custom.hint teaches {{status}}/{{hasError}}; the page calls t() without those variables,
    // so the tokens must survive verbatim while the surrounding guidance stays Chinese.
    const hint = t('custom.hint', { ns: 'monitoring' })
    expect(hint).toContain('{{status}}')
    expect(hint).toContain('{{hasError}}')
    expect(hasChinese(hint)).toBe(true)
    // lib/monitoring.ts returns i18n keys for built-in rules/status; the page resolves via t().
    expect(t('builtinRule.runError', { ns: 'monitoring' })).toBe('运行异常')
    expect(t('builtinRule.consecutiveFailures', { ns: 'monitoring' })).toBe('连续失败')
    expect(t('alertStatus.open', { ns: 'monitoring' })).toBe('待处理')
    // ruleLabel() fallbacks: a custom rule_name (any string) or a custom:{cid} id must surface
    // verbatim — missing dotted key returns the string, unknown namespace prefix returns it too.
    expect(t('错误即告警', { ns: 'monitoring' })).toBe('错误即告警')
    expect(t('custom:abc', { ns: 'monitoring' })).toBe('custom:abc')
  })

  it('reaches the shared delete action via common: prefix from a monitoring hook', () => {
    expect(t('common:button.delete', { ns: 'monitoring' })).toBe('删除')
  })

  it('falls back to zh-CN for monitoring copy under the empty en-US skeleton', () => {
    changeLanguage('en-US')
    // en-US/monitoring.json stays {} (no translation yet); Chinese must still render, never a raw key.
    for (const key of MONITORING_KEYS) {
      expect(t(key, { ns: 'monitoring' })).not.toBe(key)
    }
    for (const { key, vars } of MONITORING_TEMPLATE_KEYS) {
      expect(t(key, { ns: 'monitoring', ...vars })).not.toBe(key)
    }
    expect(t('custom.hint', { ns: 'monitoring' })).toContain('{{status}}')
    changeLanguage('zh-CN')
  })

  it('resolves shadow mode copy and the editor entry, and lib label keys via t()', () => {
    expect(t('shadow.modal.title', { ns: 'monitoring' })).toBe('影子运行（旁路演练）')
    expect(t('shadow.intent.dryRun', { ns: 'monitoring' })).toBe('写操作短路')
    expect(t('shadow.verdict.consistent', { ns: 'monitoring' })).toBe('一致')
    expect(t('shadow.action.humanReview', { ns: 'monitoring' })).toBe('转人工审核')
    // lib/shadow.ts returns i18n keys for standard actions; unknown actions stay verbatim.
    expect(t('shadow.action.refunded', { ns: 'monitoring' })).toBe('自动退款')
    // editor toolbar entry lives in the editor namespace.
    expect(t('header.shadowRun', { ns: 'editor' })).toBe('影子运行')
    // technical enum values are not translated: missing key returns the raw string.
    expect(t('SHADOW_DRY_RUN', { ns: 'monitoring' })).toBe('SHADOW_DRY_RUN')
  })

  it('resolves memory namespace static copy for the memory page', () => {
    for (const key of MEMORY_KEYS) {
      const value = t(key, { ns: 'memory' })
      expect(value, `${key} must resolve`).not.toBe(key)
      expect(value.length, `${key} must be non-empty`).toBeGreaterThan(0)
      expect(hasChinese(value), `${key} should carry Chinese copy`).toBe(true)
    }
  })

  it('resolves lib kind labels via t() and keeps an unknown kind verbatim', () => {
    // lib/memory.ts returns i18n keys for fact/preference; the page resolves via t().
    expect(t('kind.fact', { ns: 'memory' })).toBe('事实')
    expect(t('kind.preference', { ns: 'memory' })).toBe('偏好')
    // kindLabel() falls back to the raw kind for an unexpected value; a missing key returns it.
    expect(t('other', { ns: 'memory' })).toBe('other')
    // Missing keys still return the key rather than throwing (skeleton behaviour preserved).
    expect(t('memory.anything', { ns: 'memory' })).toBe('memory.anything')
  })

  it('reaches shared save/cancel/delete actions via common: prefix from a memory hook', () => {
    expect(t('common:button.save', { ns: 'memory' })).toBe('保存')
    expect(t('common:button.cancel', { ns: 'memory' })).toBe('取消')
    expect(t('common:button.delete', { ns: 'memory' })).toBe('删除')
  })

  it('falls back to zh-CN for memory copy under the empty en-US skeleton', () => {
    changeLanguage('en-US')
    // en-US/memory.json stays {} (no translation yet); Chinese must still render, never a raw key.
    for (const key of MEMORY_KEYS) {
      expect(t(key, { ns: 'memory' })).not.toBe(key)
    }
    expect(t('kind.fact', { ns: 'memory' })).toBe('事实')
    changeLanguage('zh-CN')
  })
})
