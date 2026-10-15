/**
 * A/B 实验数据层（打包 BR docs/134 D77）。
 *
 * **写调用面白名单**（docs/94 E-7 / U1124 机检纪律）：本模块消费六只 apiClient 函数
 * （四只实验端点＋`listGraphs`/`listVersions` 供创建表单选图选版），
 * `API_WHITELIST` 供测试机检。页面不直接 import apiClient。
 * 判定只读建议、conclude 人决（BR-D4）；创建/conclude 为 operate 写面。
 */
import {
  concludeExperiment,
  createExperiment,
  getExperiment,
  listExperiments,
  listGraphs,
  listVersions,
  type Experiment,
  type ExperimentArmStats,
  type ExperimentCreateBody,
  type ExperimentDetail,
  type ExperimentMetric,
  type ExperimentVerdict,
} from './apiClient'

export const API_WHITELIST = [
  'listExperiments',
  'createExperiment',
  'getExperiment',
  'concludeExperiment',
  'listGraphs',
  'listVersions',
] as const

export {
  concludeExperiment,
  createExperiment,
  getExperiment,
  listExperiments,
  listGraphs,
  listVersions,
}
export type {
  Experiment,
  ExperimentArmStats,
  ExperimentCreateBody,
  ExperimentDetail,
  ExperimentMetric,
  ExperimentVerdict,
}
