# P9 执行范围与证据计划

基线与工作区：`git worktree add scratch/par-worktrees/P9 -b par/P9 refs/tags/e2e17`。
基线提交：419181a41f29363263cee8a12d1694ace0bcef24。仅提交 par/P9，不合并、不推送。

1. 实现范围仅 `src/bestseller/services/fanqie_outline_contract_completion.py`、其原测试/新增段数测试与固定 v1 夹具；报告均在本目录。
2. 先写新增拒绝/扩展测试并保存 red；实现可信 N、对称生成/回放、2N 和 v2；保持四段 v1 请求、来源、回执和 hash。
3. 来源必须证明项目目标、存在时的创建 segment_count、物理 Beat 与候选均为同一连续 1..N；未知结果不增购、不清零。
4. 公共容量/保留注册器不在允许修改范围；v2 使用普通原生请求保留上限 120,000 字符，逐请求核算实际完整负载。不能容纳则在预留/发送前 capacity_blocked，不裁剪、不抬高上限。
5. 运行模块全部既有测试和 delivery_required.txt 的规划相关整模块；检查固定 v1 字节与原生网关替身回读。无 Docker、PostgreSQL、外部模型；仅运行既有隔离 SQLite 测试夹具，夹具禁用 socket 网络。
6. 独立只读 agent 负责外围四段假设盘点，另一 agent 复核补全 diff；主 agent 独占实现与测试写入。禁止读写 G2b/主线私有证据；不动 fanqie_short_* 等其他业务文件。

执行期间内盘可用约 12 GiB，超过公共要求 5 GiB；无依赖新增。测试命令显式选择名为 p9_config_test 的 SQLite 设置，实际文件均由 pytest tmp_path 隔离建立。首轮 `:memory:` 入口受 YAML 标量解析影响未进入行为测试，保留日志后改为明确 test 文件名；没有数据库外连。
