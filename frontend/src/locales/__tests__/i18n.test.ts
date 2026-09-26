import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import {
  changeLanguage,
  getLanguage,
  t,
  DEFAULT_LOCALE,
  LOCALE_STORAGE_KEY,
  readStoredLocale,
} from '../index'
import zhApprovals from '../zh-CN/approvals.json'
import zhAudit from '../zh-CN/audit.json'
import zhChannels from '../zh-CN/channels.json'
import zhCommon from '../zh-CN/common.json'
import zhConnections from '../zh-CN/connections.json'
import zhDashboard from '../zh-CN/dashboard.json'
import zhDemo from '../zh-CN/demo.json'
import zhEditor from '../zh-CN/editor.json'
import zhMemory from '../zh-CN/memory.json'
import zhMonitoring from '../zh-CN/monitoring.json'
import zhOpenapi from '../zh-CN/openapi.json'
import zhRuntime from '../zh-CN/runtime.json'
import zhSchedules from '../zh-CN/schedules.json'
import zhValidation from '../zh-CN/validation.json'
import enApprovals from '../en-US/approvals.json'
import enAudit from '../en-US/audit.json'
import enChannels from '../en-US/channels.json'
import enCommon from '../en-US/common.json'
import enConnections from '../en-US/connections.json'
import enDashboard from '../en-US/dashboard.json'
import enDemo from '../en-US/demo.json'
import enEditor from '../en-US/editor.json'
import enMemory from '../en-US/memory.json'
import enMonitoring from '../en-US/monitoring.json'
import enOpenapi from '../en-US/openapi.json'
import enRuntime from '../en-US/runtime.json'
import enSchedules from '../en-US/schedules.json'
import enValidation from '../en-US/validation.json'

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
  { key: 'log.superseded', vars: { node: 'wait_1' } },
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

const CONNECTIONS_KEYS = [
  'title',
  'header.back',
  'header.refresh',
  'notice.title',
  'notice.description',
  'list.cardTitle',
  'empty',
  'col.displayName',
  'col.status',
  'col.scopes',
  'col.expiresAt',
  'col.createdAt',
  'col.actions',
  'status.draft',
  'status.connected',
  'status.error',
  'button.create',
  'button.edit',
  'button.authorize',
  'button.completeAuth',
  'button.refresh',
  'button.test',
  'deleteConfirm.title',
  'deleteConfirm.description',
  'modal.createTitle',
  'modal.editTitle',
  'modal.secretKeepHint',
  'form.provider',
  'form.providerPlaceholder',
  'form.displayName',
  'form.displayNamePlaceholder',
  'form.authUrl',
  'form.tokenUrl',
  'form.clientId',
  'form.clientIdPlaceholder',
  'form.clientSecret',
  'form.clientSecretPlaceholder',
  'form.clientSecretKeepPlaceholder',
  'form.clientSecretHint',
  'form.scopes',
  'form.scopesHint',
  'form.redirectUri',
  'form.redirectUriHint',
  'authModal.title',
  'authModal.hint',
  'authModal.submit',
  'authModal.stateLabel',
  'authModal.statePlaceholder',
  'authModal.codeLabel',
  'authModal.codePlaceholder',
  'authModal.codeStateRequired',
  'message.saved',
  'message.deleted',
  'message.authorized',
  'message.refreshed',
  'message.testOk',
  'message.testFail',
  'error.loadList',
  'error.save',
  'error.authorize',
  'error.refresh',
  'error.test',
  'error.delete',
  'error.formInvalid',
]

const CHANNELS_KEYS = [
  'cardTitle',
  'empty',
  'col.shop',
  'col.provider',
  'col.status',
  'col.connection',
  'col.actions',
  'provider.shopify',
  'status.connected',
  'status.error',
  'button.bind',
  'button.test',
  'deleteConfirm.title',
  'deleteConfirm.description',
  'modal.title',
  'form.provider',
  'form.connection',
  'form.connectionPlaceholder',
  'form.connectionRequired',
  'form.shop',
  'form.shopPlaceholder',
  'form.shopRequired',
  'form.apiVersion',
  'message.bound',
  'message.deleted',
  'message.testOk',
  'message.testFail',
  'error.loadList',
  'error.bind',
  'error.test',
  'error.delete',
  'error.formInvalid',
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

  it('falls back from a namespace missing a key to common for the same key', () => {
    // editor namespace holds no auth.login.submit; common does (zh-CN side)
    expect(t('editor:auth.login.submit')).toBe('登录')
  })

  it('returns the key for an unknown namespace prefix (i18next behavior)', () => {
    expect(t('wat:role.viewer')).toBe('wat:role.viewer')
  })

  it('returns English copy under en-US and keeps cross-namespace fallback (docs/57)', () => {
    changeLanguage('en-US')
    expect(getLanguage()).toBe('en-US')
    // en-US/common.json is fully translated (docs/57 D-1)
    expect(t('auth.login.submit')).toBe('Sign in')
    expect(t('role.admin')).toBe('Admin')
    expect(t('auth.session.logout')).toBe('Sign out')
    // Cross-namespace fallback on the English side: editor lacks auth.login.submit → en-US common
    expect(t('editor:auth.login.submit')).toBe('Sign in')
    // An entirely unknown key still returns the key (i18next behavior)
    expect(t('__nonexistent__.x')).toBe('__nonexistent__.x')
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

  it('every wired key resolves to English under en-US (never raw key)', () => {
    changeLanguage('en-US')
    for (const key of [...WIRED_KEYS, ...TEMPLATE_KEYS]) {
      expect(t(key)).not.toBe(key)
    }
    // English copy must carry no Han characters
    for (const key of WIRED_KEYS) {
      expect(hasChinese(t(key)), `${key} English copy must not contain Chinese`).toBe(false)
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

  it('renders English dashboard copy under en-US (docs/57)', () => {
    changeLanguage('en-US')
    for (const key of DASHBOARD_KEYS) {
      const value = t(key, { ns: 'dashboard' })
      expect(value).not.toBe(key)
      expect(hasChinese(value), `${key} English copy must not contain Chinese`).toBe(false)
    }
    expect(t('demo.title', { ns: 'dashboard' })).toContain('refund automation')
    for (const key of SHARED_NAV_KEYS) {
      const value = t(key, { ns: 'dashboard' })
      expect(value).not.toBe(key)
      expect(hasChinese(value), `${key} English copy must not contain Chinese`).toBe(false)
    }
    expect(t('common:brand.appName', { ns: 'dashboard' })).toBe(
      'Atlas Operations Orchestration Platform',
    )
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

  it('renders English editor copy under en-US with English teaching tokens (docs/57)', () => {
    changeLanguage('en-US')
    for (const key of EDITOR_KEYS) {
      const value = t(key, { ns: 'editor' })
      expect(value).not.toBe(key)
      expect(hasChinese(value), `${key} English copy must not contain Chinese`).toBe(false)
    }
    for (const { key, vars } of EDITOR_TEMPLATE_KEYS) {
      const value = t(key, { ns: 'editor', ...vars })
      expect(value).not.toBe(key)
      expect(value).not.toContain('{{')
    }
    for (const key of EDITOR_B_KEYS) {
      const value = t(key, { ns: 'editor' })
      expect(value).not.toBe(key)
      expect(hasChinese(value), `${key} English copy must not contain Chinese`).toBe(false)
    }
    // Interpolated count renders in English without a leftover placeholder
    expect(t('debugConsole.eventCount', { ns: 'editor', count: 3 })).toBe('3 events')
    // Teaching tokens: English {{path}} / {{variable.path}} / {{global.company_name}} survive
    // verbatim because the page passes no such variables ({{path}} matches the interpolate rule
    // but is intentionally left unbound; dotted/CJK tokens never match it).
    expect(t('decision.promptTemplateLabel', { ns: 'editor' })).toContain('{{path}}')
    const hint = t('variables.hint', { ns: 'editor' })
    expect(hint).toContain('{{variable.path}}')
    expect(hint).toContain('{{global.company_name}}')
    expect(hasChinese(hint)).toBe(false)
    // HTTP proper-noun fragment interpolates cleanly in English too
    expect(t('log.toolHttpStatus', { ns: 'editor', status: 200 })).toBe(' (HTTP 200)')
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

  it('renders English monitoring copy under en-US with teaching tokens kept (docs/57)', () => {
    changeLanguage('en-US')
    for (const key of MONITORING_KEYS) {
      const value = t(key, { ns: 'monitoring' })
      expect(value).not.toBe(key)
      expect(hasChinese(value), `${key} English copy must not contain Chinese`).toBe(false)
    }
    for (const { key, vars } of MONITORING_TEMPLATE_KEYS) {
      const value = t(key, { ns: 'monitoring', ...vars })
      expect(value).not.toBe(key)
      expect(value).not.toContain('{{')
    }
    // Spot-check composed shapes in English
    expect(t('rollback.tag', { ns: 'monitoring', actor: 'Auto', from: 3, to: 2 })).toBe(
      'Auto rollback v3 → v2',
    )
    expect(t('run.nodeSummary', { ns: 'monitoring', ok: 3, failed: 1 })).toBe('3 ok / 1 failed')
    // custom.hint teaches the DSL variables; only status/hasError appear braced in the sample
    // expression (mirrors zh-CN), durationMs/failedCount are named bare in the variable list.
    const hint = t('custom.hint', { ns: 'monitoring' })
    expect(hint).toContain('{{status}}')
    expect(hint).toContain('{{hasError}}')
    expect(hint).toContain('durationMs')
    expect(hint).toContain('failedCount')
    expect(hasChinese(hint)).toBe(false)
    // Lib label keys resolve to English
    expect(t('builtinRule.runError', { ns: 'monitoring' })).toBe('Run error')
    expect(t('builtinRule.consecutiveFailures', { ns: 'monitoring' })).toBe('Consecutive failures')
    expect(t('alertStatus.open', { ns: 'monitoring' })).toBe('Open')
    // Shadow copy and the editor entry translate too; technical enums stay verbatim
    expect(t('shadow.modal.title', { ns: 'monitoring' })).toBe('Shadow run (side-channel drill)')
    expect(t('shadow.intent.dryRun', { ns: 'monitoring' })).toBe('Write short-circuited')
    expect(t('shadow.verdict.consistent', { ns: 'monitoring' })).toBe('Consistent')
    expect(t('shadow.action.refunded', { ns: 'monitoring' })).toBe('Auto refund')
    expect(t('shadow.action.humanReview', { ns: 'monitoring' })).toBe('Escalate to human review')
    expect(t('header.shadowRun', { ns: 'editor' })).toBe('Shadow run')
    expect(t('SHADOW_DRY_RUN', { ns: 'monitoring' })).toBe('SHADOW_DRY_RUN')
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

  it('renders English memory copy under en-US (docs/57)', () => {
    changeLanguage('en-US')
    for (const key of MEMORY_KEYS) {
      const value = t(key, { ns: 'memory' })
      expect(value).not.toBe(key)
      expect(hasChinese(value), `${key} English copy must not contain Chinese`).toBe(false)
    }
    expect(t('kind.fact', { ns: 'memory' })).toBe('Fact')
    expect(t('kind.preference', { ns: 'memory' })).toBe('Preference')
    changeLanguage('zh-CN')
  })

  it('resolves connections namespace static copy for the connections page (T4)', () => {
    for (const key of CONNECTIONS_KEYS) {
      const value = t(key, { ns: 'connections' })
      expect(value, `${key} must resolve`).not.toBe(key)
      expect(value.length, `${key} must be non-empty`).toBeGreaterThan(0)
      expect(hasChinese(value), `${key} should carry Chinese copy`).toBe(true)
    }
    expect(t('status.connected', { ns: 'connections' })).toBe('已连接')
    // Dashboard 入口在 common namespace
    expect(t('common:nav.connections', { ns: 'connections' })).toBe('连接管理')
  })

  it('renders English connections copy under en-US (docs/57)', () => {
    changeLanguage('en-US')
    for (const key of CONNECTIONS_KEYS) {
      const value = t(key, { ns: 'connections' })
      expect(value).not.toBe(key)
      expect(hasChinese(value), `${key} English copy must not contain Chinese`).toBe(false)
    }
    expect(t('status.connected', { ns: 'connections' })).toBe('Connected')
    expect(t('common:nav.connections', { ns: 'connections' })).toBe('Connections')
    changeLanguage('zh-CN')
  })

  it('resolves channels namespace static copy for the channel bindings card (docs/38 §1E)', () => {
    for (const key of CHANNELS_KEYS) {
      const value = t(key, { ns: 'channels' })
      expect(value, `${key} must resolve`).not.toBe(key)
      expect(value.length, `${key} must be non-empty`).toBeGreaterThan(0)
      if (key !== 'provider.shopify') {
        expect(hasChinese(value), `${key} should carry Chinese copy`).toBe(true)
      }
    }
    expect(t('provider.shopify', { ns: 'channels' })).toBe('Shopify')
  })

  it('renders English channels copy under en-US (docs/57)', () => {
    changeLanguage('en-US')
    for (const key of CHANNELS_KEYS) {
      const value = t(key, { ns: 'channels' })
      expect(value).not.toBe(key)
      if (key !== 'provider.shopify') {
        expect(hasChinese(value), `${key} English copy must not contain Chinese`).toBe(false)
      }
    }
    expect(t('provider.shopify', { ns: 'channels' })).toBe('Shopify')
    expect(t('status.connected', { ns: 'channels' })).toBe('Connected')
    changeLanguage('zh-CN')
  })
})

// ── Static parity guards over the JSON catalogs themselves (docs/57 §5) ─────────
// These read the raw locale JSON rather than the runtime t(), so a missing/extra
// key, a stray Han character in English copy, or a placeholder mismatch fails the
// build even if no runtime test happens to resolve that key.
const flatten = (obj: unknown, prefix = ''): Record<string, string> => {
  const out: Record<string, string> = {}
  if (obj && typeof obj === 'object' && !Array.isArray(obj)) {
    for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
      const path = prefix ? `${prefix}.${k}` : k
      if (v && typeof v === 'object') {
        Object.assign(out, flatten(v, path))
      } else {
        out[path] = String(v)
      }
    }
  }
  return out
}

const INTERP_IDENT = /\{\{\s*([A-Za-z_$][\w$]*)\s*\}\}/g

/**
 * Teaching tokens deliberately diverge between zh and en (docs/57 §2.3): the zh
 * copy uses CJK/dotted sample identifiers that never match the interpolate rule,
 * while en uses English sample identifiers. They are rendered without variables
 * and must survive verbatim, so they are excluded from the placeholder parity guard.
 */
const INTERPOLATION_PARITY_EXEMPT = new Set<string>([
  'editor:variables.hint',
  'editor:decision.promptTemplateLabel',
])

const PARITY_PAIRS: Array<{ ns: string; zh: unknown; en: unknown }> = [
  { ns: 'approvals', zh: zhApprovals, en: enApprovals },
  { ns: 'audit', zh: zhAudit, en: enAudit },
  { ns: 'channels', zh: zhChannels, en: enChannels },
  { ns: 'common', zh: zhCommon, en: enCommon },
  { ns: 'connections', zh: zhConnections, en: enConnections },
  { ns: 'dashboard', zh: zhDashboard, en: enDashboard },
  { ns: 'demo', zh: zhDemo, en: enDemo },
  { ns: 'editor', zh: zhEditor, en: enEditor },
  { ns: 'memory', zh: zhMemory, en: enMemory },
  { ns: 'monitoring', zh: zhMonitoring, en: enMonitoring },
  { ns: 'openapi', zh: zhOpenapi, en: enOpenapi },
  { ns: 'runtime', zh: zhRuntime, en: enRuntime },
  { ns: 'schedules', zh: zhSchedules, en: enSchedules },
  { ns: 'validation', zh: zhValidation, en: enValidation },
]

describe('zh-CN / en-US catalog parity (docs/57 §5)', () => {
  it('has identical leaf-key sets in every namespace', () => {
    for (const { ns, zh, en } of PARITY_PAIRS) {
      const zhKeys = new Set(Object.keys(flatten(zh)))
      const enKeys = new Set(Object.keys(flatten(en)))
      const missing = [...zhKeys].filter((k) => !enKeys.has(k))
      const extra = [...enKeys].filter((k) => !zhKeys.has(k))
      expect(missing, `${ns}: keys missing in en-US`).toEqual([])
      expect(extra, `${ns}: extra keys in en-US`).toEqual([])
    }
  })

  it('contains no Han characters in any English leaf value (language autonym exempt)', () => {
    // common.language.zhCN deliberately stays "中文" — language names are shown as autonyms
    // in the switcher menu and never translated (docs/57 §4).
    const CJK_EXEMPT = new Set(['common:language.zhCN'])
    for (const { ns, en } of PARITY_PAIRS) {
      for (const [key, value] of Object.entries(flatten(en))) {
        if (CJK_EXEMPT.has(`${ns}:${key}`)) continue
        expect(/[一-鿿]/.test(value), `${ns}:${key} still contains Chinese: ${value}`).toBe(false)
      }
    }
  })

  it('uses the same interpolation identifier set on both sides (teaching tokens exempt)', () => {
    for (const { ns, zh, en } of PARITY_PAIRS) {
      const zhFlat = flatten(zh)
      const enFlat = flatten(en)
      for (const key of Object.keys(zhFlat)) {
        if (INTERPOLATION_PARITY_EXEMPT.has(`${ns}:${key}`)) continue
        const zhVars = new Set(zhFlat[key].match(INTERP_IDENT) ?? [])
        const enVars = new Set(enFlat[key].match(INTERP_IDENT) ?? [])
        expect([...enVars].sort(), `${ns}:${key} placeholder mismatch`).toEqual(
          [...zhVars].sort(),
        )
      }
    }
  })
})

// ── D-3 component copy: release / rollout / feedback / approval card (docs/57 §6 U619–U622) ──
describe('docs/57 D-3 extracted component copy resolves bilingually', () => {
  it('renders FeedbackButton copy (common.feedback.*) in both languages', () => {
    expect(t('feedback.button')).toBe('反馈')
    changeLanguage('en-US')
    expect(t('feedback.button')).toBe('Feedback')
    expect(t('feedback.doneTitle')).not.toMatch(/[一-鿿]/)
    changeLanguage('zh-CN')
  })

  it('renders approval card copy (approvals.card.*) with interpolation in both languages', () => {
    expect(t('card.requiredMissing', { ns: 'approvals', fields: '备注' })).toContain('备注')
    changeLanguage('en-US')
    expect(t('card.loading', { ns: 'approvals' })).toBe('Loading the interactive card...')
    const warned = t('card.degradedWarning', { ns: 'approvals', error: 'boom' })
    expect(warned).toContain('boom')
    expect(warned).not.toMatch(/[一-鿿]/)
    changeLanguage('zh-CN')
  })

  it('renders ReleaseModal copy (editor.release.*) including meta label keys and interpolation', () => {
    expect(t('release.title', { ns: 'editor' })).toBe('发布门禁')
    expect(t('release.conclusion.blocked', { ns: 'editor' })).toBe('未通过')
    expect(t('release.trigger.manual', { ns: 'editor' })).toBe('手动门禁')
    changeLanguage('en-US')
    expect(t('release.title', { ns: 'editor' })).toBe('Release gate')
    expect(t('release.blocked', { ns: 'editor', failed: 1, total: 3 })).toContain('1/3')
    expect(t('release.upgrade.firstPin', { ns: 'editor', version: 7 })).toContain('@7')
    expect(t('release.diff.part.nodesAdded', { ns: 'editor', count: 2 })).toBe('nodes +2')
    changeLanguage('zh-CN')
  })

  it('renders RolloutModal copy (editor.rollout.*) including status/metric/segment meta keys', () => {
    expect(t('rollout.action.start', { ns: 'editor' })).toBe('启动 canary')
    expect(t('rollout.status.canary', { ns: 'editor' })).toBe('金丝雀中')
    expect(t('rollout.metric.runErrorRate', { ns: 'editor' })).toBe('运行错误率')
    expect(t('rollout.segment.lowValueBucket', { ns: 'editor' })).toBe('低金额桶')
    changeLanguage('en-US')
    expect(t('rollout.action.promote', { ns: 'editor' })).toBe('Promote to full (manual)')
    expect(t('rollout.status.full', { ns: 'editor' })).toBe('Full rollout')
    expect(t('rollout.eventLandedVersion', { ns: 'editor', version: 4 })).toContain('v4')
    changeLanguage('zh-CN')
  })
})

// D-4 browser smoke (U624) found two whole pages and the property-panel/canvas
// layer still hard-coded in Chinese; they were extracted in the same batch.
describe('docs/57 D-4 smoke-discovered copy (approval pages, canvas, property panels)', () => {
  it('renders approval queue page copy (approvals title/tabs/source/error) bilingually', () => {
    expect(t('title', { ns: 'approvals' })).toBe('审批队列')
    expect(t('tabs.pending', { ns: 'approvals' })).toBe('待处理')
    expect(t('source.timeout', { ns: 'approvals' })).toBe('超时自动处理')
    expect(t('error.submitFailed', { ns: 'approvals' })).toBe('提交失败')
    changeLanguage('en-US')
    expect(t('title', { ns: 'approvals' })).toBe('Approval queue')
    expect(t('tabs.decided', { ns: 'approvals' })).toBe('Decided')
    expect(t('source.emailLink', { ns: 'approvals' })).toBe('Handled via email link')
    expect(t('emptyDecided', { ns: 'approvals' })).not.toMatch(/[一-鿿]/)
    changeLanguage('zh-CN')
  })

  it('renders email deep-link page copy (approvals.email.*) with resolved-line interpolation', () => {
    const zhLine = t('email.resolvedLine', {
      ns: 'approvals',
      decision: '同意',
      source: '人工处理',
    })
    expect(zhLine).toContain('同意')
    expect(zhLine).toContain('人工处理')
    changeLanguage('en-US')
    expect(t('email.invalid', { ns: 'approvals' })).toBe(
      'The approval link is invalid or has expired',
    )
    const enLine = t('email.resolvedLine', {
      ns: 'approvals',
      decision: 'Approved',
      source: 'Handled by a human',
    })
    expect(enLine).toContain('Approved')
    expect(enLine).toContain('Handled by a human')
    expect(enLine).not.toMatch(/[一-鿿]/)
    changeLanguage('zh-CN')
  })

  it('renders canvas node copy (editor.canvas.*) including branch/breakpoint interpolation', () => {
    expect(t('canvas.branchFallback', { ns: 'editor', n: 2 })).toBe('分支 2')
    expect(t('canvas.bp.node', { ns: 'editor' })).toBe('节点断点（点击取消）')
    expect(t('canvas.statusRunning', { ns: 'editor' })).toBe('运行中…')
    changeLanguage('en-US')
    expect(t('canvas.branchFallback', { ns: 'editor', n: 2 })).toBe('Branch 2')
    expect(t('canvas.bp.conditional', { ns: 'editor', expression: 'x > 1' })).toContain('x > 1')
    expect(t('canvas.loopBody', { ns: 'editor' })).toBe('Loop body')
    changeLanguage('zh-CN')
  })

  it('renders problems panel and node config titles bilingually', () => {
    expect(t('problems.errors', { ns: 'editor', count: 3 })).toBe('错误 3')
    expect(t('nodeTitles.wait', { ns: 'editor' })).toContain('等待设置')
    changeLanguage('en-US')
    expect(t('problems.warnings', { ns: 'editor', count: 2 })).toBe('2 warnings')
    expect(t('nodeTitles.loopForeach', { ns: 'editor' })).toContain('For-each loop')
    expect(t('nodeTitles.condition', { ns: 'editor' })).not.toMatch(/[一-鿿]/)
    changeLanguage('zh-CN')
  })

  it('renders wait/tool/form/nodePicker/widget property-panel copy with interpolation', () => {
    expect(t('wait.durationInvalid', { ns: 'editor', min: 1, max: 86400 })).toContain('86400')
    expect(t('wait.addEvent', { ns: 'editor', max: 5 })).toContain('5')
    expect(t('form.addCount', { ns: 'editor', count: 2, max: 10 })).toBe('添加（2/10）')
    expect(t('nodePicker.nodeCount', { ns: 'editor', id: 'g1', count: 7 })).toContain('g1')
    changeLanguage('en-US')
    expect(t('tool.field', { ns: 'editor' })).toBe('Tool (adapter/capability)')
    expect(t('wait.matchAll', { ns: 'editor' })).toContain('AND')
    expect(t('form.empty', { ns: 'editor' })).toBe('No items')
    expect(t('widget.pathExpr', { ns: 'editor' })).not.toMatch(/[一-鿿]/)
    expect(t('nodePicker.nodeCount', { ns: 'editor', id: 'g1', count: 7 })).toBe('g1 (7 nodes)')
    changeLanguage('zh-CN')
  })
})

// ── Language switcher persistence runtime (docs/57 §4) ─────────────────────────
// Vitest runs under node (no jsdom); install an in-memory localStorage for this
// describe so the persistence path is exercised deterministically without a DOM dep.
function memoryStorage(): Storage {
  const m = new Map<string, string>()
  return {
    getItem: (k: string) => (m.has(k) ? (m.get(k) as string) : null),
    setItem: (k: string, v: string) => {
      m.set(k, String(v))
    },
    removeItem: (k: string) => {
      m.delete(k)
    },
    clear: () => m.clear(),
    key: (i: number) => [...m.keys()][i] ?? null,
    get length() {
      return m.size
    },
  } as Storage
}

describe('language persistence and switcher runtime (docs/57 §4)', () => {
  const g = globalThis as { localStorage?: Storage }
  const originalStorage = g.localStorage

  beforeAll(() => {
    g.localStorage = memoryStorage()
  })

  afterAll(() => {
    if (originalStorage === undefined) delete g.localStorage
    else g.localStorage = originalStorage
    vi.resetModules()
  })

  it('readStoredLocale accepts a supported stored value', () => {
    expect(readStoredLocale({ getItem: () => 'en-US' })).toBe('en-US')
    expect(readStoredLocale({ getItem: () => 'zh-CN' })).toBe('zh-CN')
  })

  it('readStoredLocale falls back to the default for missing/unsupported values', () => {
    expect(readStoredLocale({ getItem: () => null })).toBe(DEFAULT_LOCALE)
    expect(readStoredLocale({ getItem: () => 'fr-FR' })).toBe(DEFAULT_LOCALE)
    expect(readStoredLocale({ getItem: () => '' })).toBe(DEFAULT_LOCALE)
    expect(readStoredLocale(null)).toBe(DEFAULT_LOCALE)
  })

  it('readStoredLocale falls back when the storage throws', () => {
    expect(readStoredLocale({ getItem: () => { throw new Error('blocked') } })).toBe(DEFAULT_LOCALE)
  })

  it('persists the chosen language and reads it back', () => {
    changeLanguage('en-US')
    expect(g.localStorage?.getItem(LOCALE_STORAGE_KEY)).toBe('en-US')
    expect(readStoredLocale()).toBe('en-US')
    changeLanguage('zh-CN')
    expect(g.localStorage?.getItem(LOCALE_STORAGE_KEY)).toBe('zh-CN')
    expect(readStoredLocale()).toBe('zh-CN')
  })

  it('initializes the module language from localStorage on first import', async () => {
    g.localStorage?.setItem(LOCALE_STORAGE_KEY, 'en-US')
    vi.resetModules()
    const fresh = await import('../index')
    expect(fresh.getLanguage()).toBe('en-US')
    expect(fresh.t('auth.login.submit')).toBe('Sign in')
    vi.resetModules()
    g.localStorage?.removeItem(LOCALE_STORAGE_KEY)
  })

  it('resolves waits namespace copy (docs/64 J-2c 操作台) in both languages', () => {
    expect(t('waits:title')).toBe('等待与任务')
    expect(t('waits:signal')).toBe('发信号')
    changeLanguage('en-US')
    expect(t('waits:title')).toBe('Waits & Tasks')
    expect(t('waits:signal')).toBe('Signal')
    changeLanguage('zh-CN')
  })

  it('reaches waits nav copy via the common: prefix', () => {
    expect(t('common:nav.waits')).toBe('等待与任务')
    changeLanguage('en-US')
    expect(t('common:nav.waits')).toBe('Waits & Tasks')
    changeLanguage('zh-CN')
  })
})
