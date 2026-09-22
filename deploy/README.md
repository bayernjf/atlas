# 部署样例（TLS 反代 / 就绪探针 / 指标拉取）

本目录是 Atlas 从「单机持久化原型」走向「对外可访问」的最小部署样例，
对应 [docs/34](../docs/34-MVP上线就绪评审-2026-09-22复审.md) §五 P1 与
docs/14 D10b 的进程内子集。**不是**完整的多实例/CD 方案（NATS、Go 网关、
蓝绿 CD、KMS 仍缓做）。

## 组件

| 文件/端点 | 作用 |
|---|---|
| `Caddyfile` | Caddy 2 反代：自动 TLS（Let's Encrypt）、gzip、`/metrics` 网段白名单 |
| `/api/health` | 存活探针：进程在跑即 200（不检查依赖） |
| `/api/ready` | 就绪探针：PG 档执行 `SELECT 1`，失败 503，供编排摘流 |
| `/metrics` | Prometheus 文本指标（0.0.4），无鉴权，靠网络层/反代白名单隔离 |
| `prometheus/prometheus.yml` | observability profile：Prometheus 抓 `atlas:8000/metrics`（15s） |
| `grafana/` | observability profile：数据源 + Atlas Overview 看板自动装配 |

## 快速启动（本地 HTTP 联调）

```bash
# 不起反代，直连应用（现状）
docker compose up --build
# http://localhost:8000  ；/api/ready 应返回 200
```

## 启用 TLS 反代

1. 编辑 `deploy/Caddyfile`，把站点地址 `atlas.example.com` 改成真实域名，
   并把 `/metrics` 的 `remote_ip` 网段改成你的监控网段；
2. DNS 把该域名 A 记录指向部署主机；
3. 起 edge profile：

```bash
docker compose --profile edge up -d --build
```

Caddy 首次启动会自动向 Let's Encrypt 申请证书并在到期前自动续期，
证书数据持久化在 `caddy-data` 卷。

4. （生产）确认对外只暴露 80/443：把 `docker-compose.yml` 中 atlas 服务的
   `"8000:8000"` 端口映射删除或改为绑定 `127.0.0.1:8000:8000`。

## 就绪/存活探针

- compose 已给 atlas 配置 healthcheck（打 `/api/ready`）；
- Kubernetes 场景：

```yaml
livenessProbe:
  httpGet: { path: /api/health, port: 8000 }
readinessProbe:
  httpGet: { path: /api/ready, port: 8000 }
  initialDelaySeconds: 10
```

## Prometheus 拉取

`/metrics` 输出进程级与按租户聚合的 gauge（运行计数、成功率、p50/p95 耗时、
工具调用计数）。scrape 配置示例：

```yaml
scrape_configs:
  - job_name: atlas
    metrics_path: /metrics
    static_configs:
      - targets: ["atlas.example.com"]
```

指标端点不含密钥/PII，但会暴露租户 id 与工具调用量，务必通过 Caddy 的
`/metrics` 网段白名单或安全组限制访问。Grafana/Prometheus provisioning 样例
见上节（docs/35 T5，D11 子集，部分取回不解除缓做）；OTel SDK/Collector、
长期/多实例时序存储仍缓做 docs/14 D11。

## 一键可观测栈（observability profile，docs/35 T5 / D11 Grafana 子集）

除手写 scrape 外，仓库内置 Prometheus + Grafana 的 provisioning 样例，
默认**不启动**、与 edge profile 正交（可叠加）：

```bash
docker compose --profile observability up -d --build
# Prometheus  http://localhost:9090  （抓 atlas:8000/metrics，15s，保留 15d）
# Grafana      http://localhost:3000  （admin / GRAFANA_ADMIN_PASSWORD）
```

- Grafana 首次启动自动装配数据源（uid `atlas-prometheus`）与 **Atlas / Atlas Overview**
  看板（`deploy/grafana/dashboards/atlas-overview.json`）：进程存活、活跃租户、
  存储后端、运行成功率、运行计数（健康状态）、p50/p95 耗时、工具调用计数。
- 看板面板指标严格对齐 `/metrics` 的 7 个 gauge 序列（均为进程内窗口快照，不使用 `rate()`）。
- Grafana 管理员密码由 `GRAFANA_ADMIN_PASSWORD` 注入，默认 `atlasadmin` **仅供本地演示，
  生产必须在 `.env` 覆盖**；Prometheus 9090 / Grafana 3000 端口生产环境不要直接对公网开放。

## 仍未覆盖（生产化缺口）

- 真实电商渠道 OAuth2 连接（D22）、IM/短信/webhook 投递（D24，email 已支持）；
- 多实例水平扩展（NATS/Go 网关，D5/D6）、灰度 ingress 签名验签（D32）；
- CD 流水线（D10b 的自动部署/回滚部分）、KMS 密钥轮换。
