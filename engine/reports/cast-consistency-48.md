# 第 48 条：人物一致性——称呼语义、性别与身份漂移

结论：开发完成。新圣经的称呼契约统一为“说话人 → 被称呼者 → 称呼 → 来历”；旧书继续兼容读取，
但不把旧格式当成新书的合法生成结果。正文侧只把确定绑定的性别/身份漂移交给一次定向修复，
多人指代不清保持 Unknown。真实模型成功修复第 42 条试写第 7 章；eval-39 第 8 章的两次候选均未通过复检，
已回滚，未把不完整修复写入书稿。

## Evidence

### 1. 称呼契约

- `address_rows` 现在输出唯一语义行：`speaker / name(target) / address / origin`，并兼容旧卡把称呼放在目标卡、
  `address_target` 回指自身、同一关系在说话人卡和目标卡重复出现等旧格式。
- eval-45 圣经回放后，“陆大哥”只保留一行：`顾川序 → 陆烈 → 陆大哥 → 顾川序在边境猎场初见时按斥候排行称呼`；
  陆烈卡上的重复旧行不再被解释为“陆烈称呼自己”。“阿序”按姓名派生关系解释为 `顾湛宁 → 顾川序`。
- 新书 schema 与生成提示明确要求 address 放在说话人卡、address_target 写目标真名、address_origin 写来历。
  `validate_cast_contract` 会拒绝目标回指自身但称呼实际派生自另一人物、或由另一人物起名却仍放在目标卡的新增圣经；
  完全缺少这些新字段的旧书仍可读取。
- 体检中的 `address_contract` 按逻辑问题去重。eval-45 有 4 组目标卡旧格式（斐然、小秋、沈狐、霍岐）；
  eval-39 有 1 组（砚哥）；eval-33 有 7 组缺少称呼来历。这里只报告，不改旧圣经。

### 2. 确定性检查与整书体检

检查只在可绑定时产生 Inference：性别化称呼直接绑定姓名；代词必须由前句唯一人物和明确主语/受事结构绑定；
“敌方领头人”必须由领头身份句、敌对动作和非敌方人物卡共同成立。多人上下文只记 Unknown，不进入修复。

| 只读副本（draft） | 称呼契约 | 性别化称呼 | 性别代词 | 身份漂移 | 代词 Unknown |
|---|---:|---:|---:|---:|---:|
| eval-45（50 章） | 4 | 0 | 0 | 0 | 14 |
| eval-39（50 章） | 1 | 1 | 1 | 1 | 7 |
| eval-33（50 章） | 7 | 0 | 0 | 0 | 40 |
| 第 42 条 `rewrite-10-fixed7`（10 章） | 1 | 0 | 1 | 0 | 4 |

确定命中：

- eval-39 第 8 章：女药师沈夏珩被称作“沈师兄”；“竟是沈夏珩”后用“他”；并被写成指挥追兵封死矿道的领头人。
- 第 42 条试写第 7 章：前句唯一人物是顾安澜，后句为“他猝不及防……”，可确定应为“她”。
- eval-45、eval-33 没有达到自动修复门槛的正文性别/身份命中；Unknown 不冒充错误。

`book_checkup.json` 新增 `cast_consistency`：包含去重计数、逐条 findings、Unknown 计数和称呼语义行，
authority 固定为 `agent_editorial_not_human`。

### 3. 写后定向修复与回滚

`q1_chapter.py` 公共文件只增加一个 import 和一处调用；结果写入现有 `polish.cast_consistency`。
模型只能编辑命中句的真实 S 编号；修后重新跑确定性检查、格式/引号/重复检查和状态硬冲突检查，任一不通过即恢复原文。

真实模型试验均在 `/private/tmp/bestseller-f48-Jy4dZr/model-repairs/`：

1. 第 42 条第 7 章：通过。
   - 修前：`他猝不及防，整个人被拖得向前滑去……`
   - 修后：`她猝不及防，整个人被拖得向前滑去……`
   - 其他句逐字保留，复检无剩余 Inference。
2. eval-39 第 8 章：未采用。
   - 修前：`沈师兄在矿洞那边`；`他隔着风雪……指挥众人……`
   - 第一候选改成“沈师姐”和“游历矿镇的年轻女药师……指挥众人”，仍把她保留为追兵领头人，复检失败后回滚。
   - 根据该失败把整条串角链和保存章纲纳入修复目标后再验证，模型返回示例 id=1 而非真实句号，零编辑被采用，再次回滚。
   - 最终文本与修前完全相同；没有用一个性别词替换掩盖身份串写。

调用账本共 3 次，MiniMax-M3.1-Flash：prompt 15,141 tokens，completion 760 tokens，总计 15,901 tokens；
usage 均已确认，无重试、无缺失用量。按仓库现有价格表估算 $0.00272715（无缓存折扣保守口径）。

### 4. 验证

- RED：新增测试最初因 `bookengine.cast_consistency` 不存在而 collection error。
- GREEN：`tests/test_cast_consistency48.py` 8 passed；相关 identity/address/plan 回归 127 passed、61 subtests passed。
- 完整门禁：`1523 passed, 1 skipped, 761 subtests passed in 42.77s`。
- `python tools/check_scope.py`：PASS；`max_file_lines=490`，无 bestseller import、无跨包反向依赖、无禁用词。
- `python -m ruff format --check .`：250 files already formatted。
- `tools/real_library_smoke.py`：4 books、107 requests、20 exports、20 reader chapters、0 models started、fixture unchanged=true。
- 验证源均先复制到 `/private/tmp/bestseller-f48-Jy4dZr/`；未写 `books/`，未修改第 42 条原目录。

## Inference

- eval-39 第 8 章不是单纯“他/她”错误，而是人物身份整段串入追兵链；只改性别词会留下更隐蔽的硬伤，回滚比勉强采用安全。
- eval-45 的 4 组称呼契约问题和 eval-39 的“砚哥”属于旧卡存储方向问题；兼容读取能恢复语义，但是否迁移旧圣经应由监工另行决定。
- 当前确定性规则偏保守：漏报的代价小于把动物、对话对象或多人物段落误当成上一句唯一人物后自动改稿。

## Unknown

- 表中 65 条代词 Unknown 只表示上下文无法确定指代，不代表 65 个错误；未送模型、未改稿。
- eval-39 第 8 章应如何把沈夏珩从追兵链恢复成正确人物，需要可靠的对手身份或人工编辑决定；本轮模型未给出可采用答案。
- 程序检查与同源模型修复不等于读者验收；人物自然度和长期区分度仍以人读为准。
