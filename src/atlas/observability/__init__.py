"""可观测性导出：就绪探针与 Prometheus 指标（docs/34 §五 P1，D11 进程内子集）。

- health.check_ready：就绪探针，内存档恒就绪，PG 档执行 SELECT 1 探活。
- metrics_export.render_prometheus：把进程内 monitoring 快照渲染为
  Prometheus text exposition format（0.0.4），零新依赖、纯函数可单测。

正式 OTel/Prometheus 拉取栈（Grafana dashboard、长期存储、多实例聚合）
仍缓做 docs/14 D11；本模块只暴露单实例进程内指标。
"""
