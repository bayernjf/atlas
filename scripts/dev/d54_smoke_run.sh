#!/bin/bash
# docs/54 打包 A 收口 smoke：起新代码 uvicorn，跑 event_wait + alert_notify + dynamic_wait 三个 HTTP smoke（零回归）。
set -u
cd /Users/jiangfeng/000mycodes/atlas

echo "=== kill old :8000 ==="
lsof -ti tcp:8000 | xargs -r kill -9 2>/dev/null
sleep 1

echo "=== start uvicorn ==="
nohup .venv/bin/uvicorn atlas.api.main:app --port 8000 > /tmp/atlas_uvicorn.log 2>&1 &
UV=$!
echo "uvicorn pid=$UV"

# 等待就绪（最多 ~60s）
for i in $(seq 1 60); do
  if curl -sf -o /dev/null http://127.0.0.1:8000/api/health 2>/dev/null; then
    echo "health ready after ${i}s"; break
  fi
  # 某些版本无 /api/health，用 openapi 兜底
  if curl -sf -o /dev/null http://127.0.0.1:8000/openapi.json 2>/dev/null; then
    echo "openapi ready after ${i}s"; break
  fi
  sleep 1
done

echo "=== event_wait_smoke ==="
.venv/bin/python .smoke/event_wait_smoke.py 2>&1 | tail -25
E1=${PIPESTATUS[0]}

echo "=== alert_notify_smoke ==="
.venv/bin/python .smoke/alert_notify_smoke.py 2>&1 | tail -30
E2=${PIPESTATUS[0]}

echo "=== dynamic_wait_smoke ==="
.venv/bin/python .smoke/dynamic_wait_smoke.py 2>&1 | tail -15
E3=${PIPESTATUS[0]}

echo "=== stop uvicorn $UV ==="
kill -9 "$UV" 2>/dev/null
lsof -ti tcp:8000 | xargs -r kill -9 2>/dev/null

echo "SMOKE_RESULTS event_wait=$E1 alert_notify=$E2 dynamic_wait=$E3"
