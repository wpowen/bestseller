#!/bin/bash
# 容器跑的到底是不是我这份代码？
#
# 2026-08-16：一整批修复没有一次真正运行过。症状是「改了代码、重启了容器、
# 容器内断言仍然失败」，看起来极像 Python 进程缓存导入，实际是 src/ 根本
# 没有挂进容器 —— 栈是用 `-f docker-compose.yml -f docker-compose.ssd.yml`
# 起的，显式 -f 关掉了 docker-compose.override.yml 的自动加载，而活挂载
# `./src:/app/src:ro` 只写在 override 里。
#
# 「重启了」不等于「新代码进去了」。这个脚本把那句假设变成一条可执行判断：
# 在宿主机写一个哨兵字符串，立刻到容器里找它。找得到才是真挂载。
#
# 用法：  bash scripts/verify_live_code_mount.sh [服务名...]   默认 worker web
set -uo pipefail
cd "$(dirname "$0")/.."

SERVICES=("${@:-}")
[ -z "${SERVICES[0]:-}" ] && SERVICES=(worker web)

PROBE="src/bestseller/_live_mount_probe.py"
STAMP="live-mount-probe-$$-$(od -An -N4 -tu4 </dev/urandom | tr -d ' ')"
echo "# 哨兵 $STAMP" > "$PROBE"
trap 'rm -f "$PROBE"' EXIT

fail=0
for svc in "${SERVICES[@]}"; do
  c="bestseller-${svc}-1"
  if ! docker ps --format '{{.Names}}' | grep -qx "$c"; then
    echo "  ⚠️  $c 未运行，跳过"
    continue
  fi
  if docker exec "$c" grep -q "$STAMP" "/app/$PROBE" 2>/dev/null; then
    echo "  ✓ $svc  活挂载正常（宿主机改动即时可见）"
  else
    echo "  ✗ $svc  **没有活挂载** —— 容器跑的是镜像里烘焙的旧代码"
    fail=1
  fi
done

if [ "$fail" -ne 0 ]; then
  cat <<'EOF'

修法：用不带 -f 的命令重新起栈，让 docker-compose.override.yml 自动加载。

    docker compose up -d

不带 -f 不会丢 SSD：override 用 external:true 引用已经绑在 SSD 上的同一批卷。
带上 docker-compose.ssd.yml 反而会在 pgdata 上报 conflicting parameters。
EOF
  exit 1
fi
echo
echo "全部服务确认在跑宿主机当前代码。"
