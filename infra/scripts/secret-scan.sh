#!/usr/bin/env bash
# focused secret scan：git tracked 文件 + 最近 10 个 commit 的 diff。
# 只匹配“赋值形态的真实 secret”：
#   - 环境变量赋值（POSTGRES_PASSWORD=... 等）
#   - KAIROS_CREDENTIAL_MASTER_KEY=<64 hex>
#   - 私钥块 / Authorization: Bearer / OpenAI 风格 sk- 长 key
# 参数签名（api_key: str | None）、变量引用（password: password.value）、
# 测试占位（sk-test）与 .env.example 模板不计为命中。
# 返回 0=未发现明文 secret，1=发现。
#
# 性能：先用 GNU grep 过滤候选行，再用小 bash 循环做二次判定，避免对大 diff 逐行 bash 处理。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

# 二次判定：过滤掉模板/示例/变量引用，只保留真实 secret 形态。
refine() {  # refine <label>  从 stdin 读候选行
  local label="$1" line val
  while IFS= read -r line; do
    # 文档可能原样引用本扫描器；正则定义本身包含 KEY=<pattern>，不是凭据赋值。
    if printf '%s' "$line" | grep -Eq 'grep[[:space:]]+-Eiq.*KAIROS_CREDENTIAL_MASTER_KEY='; then
      continue
    fi
    # 值形如裸标识符（api_key: str / password: password.value）→ 变量引用/类型标注，跳过
    # 去掉 grep -Hn 的 "path:line:" 前缀，再取 "name=value" 的 value
    val="$(printf '%s' "$line" | sed -E 's/^[^:]*:[0-9]+:[[:space:]]*//; s/^[^=:]*[=:][[:space:]]*//; s/[[:space:],;)]+$//')"
    if printf '%s' "$val" | grep -Eiq '^[a-z_][a-z0-9_.]*$'; then
      continue
    fi
    # 模板 / 示例 / 测试占位 / canary 变量引用
    if printf '%s' "$line" | grep -Eiq "example|your-|your_key|xxx|dummy|placeholder|changeme|REPLACE_ME|\.env\.example|\.env\.production\.example|M17_SECRET|CANARY|sk-test"; then
      continue
    fi
    echo "SECRET-HIT[$label]: ${line:0:160}" >> "$TMP"
  done
}

BROAD_PATTERN="(postgres_password|minio_secret_key|kairos_session_secret|session_secret|secret_key|aws_secret_access_key|access_key_secret)=|KAIROS_CREDENTIAL_MASTER_KEY=[0-9a-f]{64}|BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY|authorization: bearer|sk-[a-zA-Z0-9]{20,}"

# 1) git tracked 文件（排除 .env 模板 / 含 secret 模式字面量的脚本）
git ls-files -z | xargs -0 grep -HnE "$BROAD_PATTERN" 2>/dev/null \
  | grep -vE "\.env\.example|\.env\.production\.example|infra/scripts/secret-scan\.sh|infra/scripts/_backup_common\.py" \
  | refine "git-tracked" || true

# 2) 最近 10 个 commit 的 diff（一次扫描；排除含 secret 模式字面量的脚本，避免自我命中）
git log -p --format= -10 -- . \
  ':(exclude)*.md' \
  ':(exclude)*tests*' \
  ':(exclude)infra/scripts/secret-scan.sh' \
  ':(exclude)infra/scripts/_backup_common.py' \
  ':(exclude)backend/app/observability/*' 2>/dev/null \
  | grep -E "$BROAD_PATTERN" \
  | refine "recent-commits" || true

if [ -s "$TMP" ]; then
  cat "$TMP"
  echo "SECRET_SCAN_RESULT: FAIL($(wc -l < "$TMP"))"
  exit 1
fi
echo "SECRET_SCAN_RESULT: PASS"
exit 0
