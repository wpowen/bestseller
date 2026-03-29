# 交互式小说支持设计方案

**版本**: 2.0
**日期**: 2026-03-23
**状态**: Design Draft

---

## 1. 目标与范围

### 1.1 核心目标

在 Web Studio 中增加「交互式小说模式」开关。开关打开后：

1. 显示 story-factory 专用的参数面板（genre、章节数、人物设定、故事结构等）
2. 系统按照 story-factory 的四阶段流水线生成内容
3. 最终输出符合 `STORY_ARTIFACT_SPEC.md` 规范的产物：
   - `story_package.json`（中间产物，含完整数据）
   - `book_<id>.json` / `chapters_<id>.json` / `walkthrough_<id>.json`（App 资源，通过 compile 脚本生成）

### 1.2 两种模式的关系

```
bestseller Web Studio
│
├── 线性模式（默认）
│   └── 输出：markdown / docx / epub / pdf
│
└── 交互式小说模式（开关）
    ├── 参数面板：genre / 章节数 / 人物 / 故事结构
    ├── 4阶段流水线：Story Bible → Arc Plan → Chapter Gen → Walkthrough
    └── 输出：story_package.json → book.json / chapters.json / walkthrough.json
```

### 1.3 story-factory 现有能力

`story-factory/scripts/generate_story.py` 已实现完整 4 阶段流水线：

| 阶段 | 模型 | 产物 |
|------|------|------|
| Phase 1: Story Bible | claude-sonnet-4-6 | book 元数据 + reader_desire_map + story_bible + route_graph |
| Phase 2: Arc Plan | claude-sonnet-4-6 | 每 50 章一组的章节卡（chapter cards） |
| Phase 3: Chapter Gen | claude-haiku-4-5 | 每章的完整 nodes（text/dialogue/choice/notification） |
| Phase 4: Walkthrough | claude-sonnet-4-6 | stages + chapter_guides |

bestseller 的任务是：**将这个流水线集成进 Web Studio**，让用户通过 UI 配置和启动生成，实时监控进度，最终下载产物。

---

## 2. WritingProfile 扩展：InteractiveFictionConfig

### 2.1 设计原则

`InteractiveFictionConfig` 包含两类参数：

- **Concept 参数**（生成前确定）：映射到 `concept.json` 字段，决定故事的基本设定
- **Generation 参数**（控制生成行为）：覆盖写作规则，注入 Prompt

### 2.2 新增配置类

在 `src/bestseller/domain/project.py` 的 `WritingProfile` 中新增：

```python
class IFStatConfig(BaseModel):
    """主角初始属性（0-100）"""
    combat: int = Field(default=10, ge=0, le=100, description="战力")
    fame: int = Field(default=5, ge=0, le=100, description="名望")
    strategy: int = Field(default=20, ge=0, le=100, description="谋略")
    wealth: int = Field(default=5, ge=0, le=100, description="财富")
    charm: int = Field(default=10, ge=0, le=100, description="魅力")
    darkness: int = Field(default=0, ge=0, le=100, description="黑化值")
    destiny: int = Field(default=30, ge=0, le=100, description="天命值")


class IFCharacterDraft(BaseModel):
    """人物草稿（concept 阶段填写，Story Bible 阶段由 LLM 丰富）"""
    name: str = Field(min_length=1, max_length=20)
    role: str = Field(description="盟友|宿敌|红颜|师尊|家族|中立|反派")
    description: str = Field(default="", max_length=200)

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        allowed = {"盟友", "宿敌", "红颜", "师尊", "家族", "中立", "反派"}
        if v not in allowed:
            raise ValueError(f"role must be one of {allowed}")
        return v


class InteractiveFictionConfig(BaseModel):
    """
    交互式小说专用配置。
    enabled=True 时，系统切换为 story-factory 生产模式，
    输出符合 STORY_ARTIFACT_SPEC.md 的 JSON 产物。
    """

    enabled: bool = False

    # ── Concept 参数（决定故事基本设定） ──────────────────────────────
    if_genre: str = Field(
        default="修仙升级",
        description="故事类型，必须是 VALID_GENRES 之一：都市逆袭|修仙升级|悬疑生存|职场商战|末日爽文",
    )
    target_chapters: int = Field(
        default=100,
        ge=10,
        le=2000,
        description="目标章节数。影响 Arc Plan 的分组数量和里程碑设计。",
    )
    free_chapters: int = Field(
        default=20,
        ge=5,
        le=100,
        description="免费章节数，前 N 章 is_paid=false。",
    )
    premise: str = Field(
        default="",
        max_length=500,
        description="故事核心前提（一句话），对应 concept.premise。",
    )
    protagonist: str = Field(
        default="",
        max_length=200,
        description="主角设定描述，对应 concept.protagonist。",
    )
    core_conflict: str = Field(
        default="",
        max_length=300,
        description="核心冲突描述，对应 concept.core_conflict。",
    )
    tone: str = Field(
        default="爽快、热血、有悬念",
        max_length=100,
        description="故事基调，对应 concept.tone。",
    )
    arc_structure: list[str] = Field(
        default_factory=list,
        description="故事弧结构，每项描述一段章节范围的主要事件（对应 concept.arc_structure）。",
    )
    key_characters: list[IFCharacterDraft] = Field(
        default_factory=list,
        description="关键人物草稿列表（4-6人）。LLM 在 Story Bible 阶段会基于此生成完整 character 定义。",
    )
    initial_stats: IFStatConfig = Field(
        default_factory=IFStatConfig,
        description="主角初始属性值。",
    )

    # ── Generation 参数（控制章节生成行为） ────────────────────────────
    chapter_text_length: str = Field(
        default="3000-6000",
        description="每章总文字量区间（汉字），注入 Chapter Gen Prompt。规范要求 1200-3200 字通过 QA。",
    )
    choice_nodes_per_chapter: str = Field(
        default="3-5",
        description="每章 choice 节点数量区间，注入 Chapter Gen Prompt。",
    )
    text_node_length: str = Field(
        default="200-400",
        description="单个 text 节点的汉字数区间。",
    )
    arc_batch_size: int = Field(
        default=50,
        ge=20,
        le=100,
        description="Arc Plan 每批规划的章节数。影响规划质量和 LLM 调用次数。",
    )
    parallel_chapter_batch: int = Field(
        default=8,
        ge=1,
        le=20,
        description="Chapter Gen 并行生成批次大小（每批同时提交多少章）。",
    )

    @field_validator("if_genre")
    @classmethod
    def validate_genre(cls, v: str) -> str:
        allowed = {"都市逆袭", "修仙升级", "悬疑生存", "职场商战", "末日爽文"}
        if v not in allowed:
            raise ValueError(f"if_genre must be one of {allowed}")
        return v
```

### 2.3 集成到 WritingProfile

```python
class WritingProfile(BaseModel):
    market: MarketPositioningConfig = Field(default_factory=MarketPositioningConfig)
    character: CharacterEngineConfig = Field(default_factory=CharacterEngineConfig)
    world: WorldDesignConfig = Field(default_factory=WorldDesignConfig)
    style: StylePreferenceConfig = Field(default_factory=StylePreferenceConfig)
    serialization: SerializationStrategyConfig = Field(default_factory=SerializationStrategyConfig)
    interactive_fiction: InteractiveFictionConfig = Field(  # 新增
        default_factory=InteractiveFictionConfig
    )
```

### 2.4 IF 参数对其他 WritingProfile 字段的影响

当 `interactive_fiction.enabled = True` 时，`resolve_writing_profile` 自动注入以下覆盖（无需用户手动调整）：

| 字段 | 原值 | IF 覆盖值 | 说明 |
|------|------|---------|------|
| `market.platform_target` | 番茄小说 | LifeScript | IF 产物面向 iOS App |
| `market.content_mode` | 中文网文长篇连载 | 交互式小说 | |
| `market.update_strategy` | 日更连载 | 全本发布 | story package 一次生成 |
| `style.pov_type` | third-limited | second | 第二人称"你"是 IF 标准 |
| `market.chapter_hook_strategy` | 章节尾钩 | 每章以 next_chapter_hook 字段收尾 | |

---

## 3. 数据模型

### 3.1 ProjectModel 扩展

在 `projects` 表增加一列：

```python
# infra/db/models.py — ProjectModel
project_type: Mapped[str] = mapped_column(
    String(32), nullable=False, server_default=text("'linear'"),
    comment="linear | interactive"
)
```

### 3.2 新增枚举

```python
# domain/enums.py
class ProjectType(StrEnum):
    LINEAR = "linear"
    INTERACTIVE = "interactive"

class IFNodeType(StrEnum):
    TEXT = "text"
    DIALOGUE = "dialogue"
    CHOICE = "choice"
    NOTIFICATION = "notification"

class IFGenerationPhase(StrEnum):
    STORY_BIBLE = "story_bible"
    ARC_PLAN = "arc_plan"
    CHAPTER_GEN = "chapter_gen"
    WALKTHROUGH = "walkthrough"
    ASSEMBLY = "assembly"
    COMPILE = "compile"
    COMPLETED = "completed"
    FAILED = "failed"
```

### 3.3 story_package 存储策略

`story_package.json` 是一个结构化 JSON，包含完整的 book/chapters/walkthrough 数据，最大可达数十 MB（1000 章 × 每章 ~50 nodes）。

存储方案：**双轨制**

| 层 | 内容 | 存储位置 |
|----|------|---------|
| 进度追踪 | 各阶段状态、已完成章节数、错误信息 | PostgreSQL（`if_generation_runs` 表） |
| 中间产物 | story_bible JSON、arc_plans JSON | PostgreSQL `PlanningArtifactVersion`（已有表） |
| 完整 story_package | 所有章节正文 JSON | 本地文件（`output/<slug>/story_package.json`） |
| 编译产物 | book.json / chapters.json / walkthrough.json | 本地文件（`output/<slug>/build/`） |
| 进度快照 | 已完成章节列表 | 本地文件（`output/<slug>/progress.json`） |

这与现有 bestseller 导出体系一致：数据库存状态和元数据，文件系统存大型产物。

### 3.4 新增表：if_generation_runs

```python
class IFGenerationRunModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "if_generation_runs"

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    book_id: Mapped[str] = mapped_column(
        String(64), nullable=False,
        comment="story-factory book_id，如 xianxia_001"
    )
    phase: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'story_bible'"),
        comment="IFGenerationPhase"
    )
    target_chapters: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_chapters: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    concept_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict,
        comment="生成时使用的 concept.json 内容"
    )
    bible_artifact_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("planning_artifact_versions.id", ondelete="SET NULL"),
        nullable=True,
        comment="Story Bible 产物的 PlanningArtifactVersion.id"
    )
    arc_plan_artifact_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("planning_artifact_versions.id", ondelete="SET NULL"),
        nullable=True,
        comment="Arc Plan 产物的 PlanningArtifactVersion.id"
    )
    walkthrough_artifact_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("planning_artifact_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    story_package_path: Mapped[str | None] = mapped_column(
        String(500), comment="story_package.json 的相对路径"
    )
    qa_report_path: Mapped[str | None] = mapped_column(
        String(500), comment="qa_report.json 的相对路径"
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    resumable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
        comment="是否可通过 --resume 继续"
    )
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
```

**新增 `ArtifactType` 枚举值**（在 `domain/enums.py`）：

```python
class ArtifactType(StrEnum):
    # 已有 ...
    IF_STORY_BIBLE = "if_story_bible"     # Phase 1 产物
    IF_ARC_PLAN = "if_arc_plan"           # Phase 2 产物（含所有 arc cards）
    IF_WALKTHROUGH = "if_walkthrough"     # Phase 4 产物
```

---

## 4. 服务层：IFGenerationService

### 4.1 新建 `services/if_generation.py`

将 `story-factory/scripts/generate_story.py` 的逻辑提升为 bestseller 服务层方法：

```python
# services/if_generation.py

async def build_concept_json(
    project: ProjectModel,
    profile: WritingProfile,
) -> dict[str, Any]:
    """
    从 WritingProfile.interactive_fiction 组装 concept.json。
    这是 generate_story.py 的 concept 文件的等价物。
    """
    ifc = profile.interactive_fiction
    return {
        "book_id": project.slug,
        "title": project.title,
        "genre": ifc.if_genre,
        "target_chapters": ifc.target_chapters,
        "premise": ifc.premise or project.metadata_json.get("premise", ""),
        "protagonist": ifc.protagonist,
        "core_conflict": ifc.core_conflict,
        "tone": ifc.tone,
        "arc_structure": ifc.arc_structure,
        "key_characters": [c.model_dump() for c in ifc.key_characters],
    }


async def generate_story_bible(
    session: AsyncSession,
    run: IFGenerationRunModel,
    concept: dict[str, Any],
    llm_service: LLMService,
) -> dict[str, Any]:
    """
    Phase 1：调用 Sonnet 生成 Story Bible。
    对应 generate_story.py::StoryGenerator.generate_bible()
    存为 PlanningArtifactVersion(artifact_type='if_story_bible')
    """


async def generate_arc_plan(
    session: AsyncSession,
    run: IFGenerationRunModel,
    bible: dict[str, Any],
    llm_service: LLMService,
) -> list[list[dict[str, Any]]]:
    """
    Phase 2：分批规划所有章节。
    对应 generate_story.py::StoryGenerator.generate_arc_plan()
    存为 PlanningArtifactVersion(artifact_type='if_arc_plan')
    """


async def generate_chapters(
    session: AsyncSession,
    run: IFGenerationRunModel,
    bible: dict[str, Any],
    arc_plans: list[list[dict[str, Any]]],
    output_dir: Path,
    llm_service: LLMService,
    *,
    resume: bool = True,
) -> list[dict[str, Any]]:
    """
    Phase 3：逐章生成 chapter JSON（text/dialogue/choice 节点）。
    对应 generate_story.py::StoryGenerator.generate_chapters_batch()
    进度保存到 output_dir/progress.json（可中断恢复）
    每生成 8 章更新 run.completed_chapters
    """


async def generate_walkthrough(
    session: AsyncSession,
    run: IFGenerationRunModel,
    bible: dict[str, Any],
    arc_plans: list[list[dict[str, Any]]],
    llm_service: LLMService,
) -> dict[str, Any]:
    """
    Phase 4：生成 walkthrough（stages + chapter_guides）。
    对应 generate_story.py::StoryGenerator.generate_walkthrough()
    存为 PlanningArtifactVersion(artifact_type='if_walkthrough')
    """


async def assemble_story_package(
    run: IFGenerationRunModel,
    bible: dict[str, Any],
    chapters: list[dict[str, Any]],
    walkthrough: dict[str, Any],
    output_dir: Path,
) -> Path:
    """
    Phase 5：组装 story_package.json。
    对应 generate_story.py run() 的 Assembly 步骤。
    """


async def compile_story_package(
    run: IFGenerationRunModel,
    package_path: Path,
    resources_dir: Path,
    build_dir: Path,
) -> dict[str, Any]:
    """
    Phase 6：调用 compile_story_package.py 验证并拆分。
    输出：book_<id>.json / chapters_<id>.json / walkthrough_<id>.json + qa_report.json
    """


async def run_if_pipeline(
    session: AsyncSession,
    project: ProjectModel,
    profile: WritingProfile,
    output_dir: Path,
    llm_service: LLMService,
    *,
    resume: bool = True,
) -> IFGenerationRunModel:
    """
    完整 IF 生成流水线（对应 generate_story.py::run()）。
    创建或恢复 IFGenerationRunModel，按阶段推进，
    每阶段完成后更新 run.phase，支持中断恢复。
    """


async def get_if_generation_run(
    session: AsyncSession,
    project_id: UUID,
) -> IFGenerationRunModel | None:
    """获取项目最新的 IF 生成运行记录。"""
```

### 4.2 Prompt 复用策略

`services/if_generation.py` 直接复用 `generate_story.py` 中已经验证过的 Prompt 构建函数：

```python
# 从 story-factory/scripts/generate_story.py 导入（或复制到 services/）
from bestseller.services.if_prompts import (
    build_bible_prompt,      # 对应 _bible_prompt()
    build_arc_plan_prompt,   # 对应 _arc_plan_prompt()
    build_chapter_prompt,    # 对应 _chapter_prompt()
    build_walkthrough_prompt, # 对应 _walkthrough_prompt()
    CHAPTER_SCHEMA,
    VALID_STATS,
    VALID_SATISFACTION,
    VALID_REL_DIMS,
)
```

新建 `src/bestseller/services/if_prompts.py`，将 `generate_story.py` 中所有 prompt 函数迁移过来，同时：
- 增加 `InteractiveFictionConfig.generation_params` 的注入（chapter_text_length、choice_nodes_per_chapter 等）
- 增加 `if_genre` 对应的 genre_rule_summary 注入

---

## 5. Web Studio 改动

### 5.1 「新建项目」面板：交互式小说开关

在项目创建表单的写作配置区域末尾，增加「交互式小说」折叠区域：

```
┌─────────────────────────────────────────────────────────────┐
│  写作配置                                                      │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ 平台 / 视角 / 语气 ...（现有线性模式参数）               │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                               │
│  ╔═══════════════════════════════════════════════════════╗  │
│  ║  交互式小说模式（LifeScript）      [ OFF ●──────── ]   ║  │
│  ║                                                       ║  │
│  ║  开启后：生成含选择节点的互动小说，输出 App 可用的      ║  │
│  ║  JSON 产物（story_package.json）。                     ║  │
│  ╚═══════════════════════════════════════════════════════╝  │
└─────────────────────────────────────────────────────────────┘
```

**开关打开后展开 IF 参数面板（共两组）：**

```
╔═══════════════════════════════════════════════════════════════╗
║  交互式小说模式（LifeScript）         [ ON  ────●── ]          ║
║                                                               ║
║  【故事设定】                                                   ║
║  ┌─────────────────────────────────────────────────────────┐ ║
║  │ 类型        [ 修仙升级 ▼ ]  (都市逆袭/修仙升级/悬疑生存    │ ║
║  │                             /职场商战/末日爽文)           │ ║
║  │ 目标章节数  [ 100  ⬆⬇ ]   免费章节数  [ 20  ⬆⬇ ]        │ ║
║  │ 故事前提    [________________________] (一句话)           │ ║
║  │ 主角设定    [________________________]                    │ ║
║  │ 核心冲突    [________________________]                    │ ║
║  │ 故事基调    [ 爽快、热血、有悬念     ]                    │ ║
║  │                                                         │ ║
║  │ 关键人物（4-6位）                                        │ ║
║  │ ┌──────────┬──────────┬──────────────────────┐         │ ║
║  │ │ 姓名     │ 角色     │ 描述                  │         │ ║
║  │ ├──────────┼──────────┼──────────────────────┤         │ ║
║  │ │ 宋玄     │ 反派   ▼ │ 天青宗掌门...        │  [删]   │ ║
║  │ │ 李青云   │ 宿敌   ▼ │ 首席弟子...          │  [删]   │ ║
║  │ └──────────┴──────────┴──────────────────────┘         │ ║
║  │ [ + 添加人物 ]                                          │ ║
║  │                                                         │ ║
║  │ 主角初始属性                                             │ ║
║  │ 战力[10] 名望[5] 谋略[20] 财富[5] 魅力[10]              │ ║
║  │ 黑化值[0] 天命值[30]                                     │ ║
║  └─────────────────────────────────────────────────────────┘ ║
║                                                               ║
║  【生成参数】（高级，默认值已符合规范）                           ║
║  ┌─────────────────────────────────────────────────────────┐ ║
║  │ 每章字数范围   [ 3000-6000 汉字 ]                        │ ║
║  │ 每章选择节点数 [ 3-5 个 ]                                │ ║
║  │ 弧规划批次     [ 50 章/批 ]                              │ ║
║  └─────────────────────────────────────────────────────────┘ ║
╚═══════════════════════════════════════════════════════════════╝
```

**开关联动规则**（前端提示，用户可忽略）：

| 字段 | 联动提示 |
|------|---------|
| 目标章节数 | IF 目标章节数同步更新 project 的 `target_chapters` |
| 视角 | 自动建议切换为「第二人称」 |
| 平台目标 | 自动切换为「LifeScript」 |

### 5.2 项目执行看板：IF 生成进度

IF 项目的看板页展示四阶段进度：

```
┌────────────────────────────────────────────────────────────┐
│  [运行 IF 生成]   [继续/恢复]   [下载产物]                   │
│                                                             │
│  生成进度                                                    │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ ① Story Bible      ██████████  完成  ✓               │  │
│  │ ② Arc Plan (20弧)  ██████████  完成  ✓               │  │
│  │ ③ Chapter Gen      ████████░░  82/100 章 (82%)        │  │
│  │   当前章节：第83章「宗门大典」正在生成...               │  │
│  │ ④ Walkthrough      ░░░░░░░░░░  等待中                 │  │
│  │ ⑤ Assembly         ░░░░░░░░░░  等待中                 │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  QA 报告（完成后显示）                                       │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ 章节总数: 100   选择节点: 327   平均选择节点/章: 3.27  │  │
│  │ 平均选项数/节点: 2.8                                   │  │
│  │ ⚠ 警告 3 条  ✗ 错误 0 条                             │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  产物下载                                                    │
│  [story_package.json]  [book_<id>.json]                    │
│  [chapters_<id>.json]  [walkthrough_<id>.json]             │
│  [qa_report.json]                                           │
└────────────────────────────────────────────────────────────┘
```

### 5.3 Web Server API 新增端点

```
POST /api/projects/{slug}/if/run          # 启动/恢复 IF 生成流水线
GET  /api/projects/{slug}/if/status       # 获取 IFGenerationRunModel 状态
GET  /api/projects/{slug}/if/download/{filename}  # 下载产物文件
POST /api/projects/{slug}/if/cancel       # 中止当前运行
```

---

## 6. 完整生产流程

```
用户操作                    系统动作
─────────                    ────────
1. 新建项目                  → ProjectCreate(project_type='interactive', writing_profile 含 IF 配置)
   开启 IF 开关
   填写 IF 参数

2. 保存项目                  → ProjectModel 写入 DB
                              resolve_writing_profile 应用 IF 覆盖
                              concept.json 从 IF config 动态构建

3. 点击「运行 IF 生成」       → POST /api/projects/{slug}/if/run
                              创建 IFGenerationRunModel(phase='story_bible')
                              后台启动 run_if_pipeline()

4. ── Phase 1: Story Bible ──
   Web Studio 显示进度 ①     → 调用 LLM (Sonnet) 生成 story bible
                              存为 PlanningArtifactVersion(type='if_story_bible')
                              更新 run.phase = 'arc_plan'

5. ── Phase 2: Arc Plan ──
   Web Studio 显示进度 ②     → 按 arc_batch_size 分批调用 LLM (Sonnet)
                              存为 PlanningArtifactVersion(type='if_arc_plan')
                              更新 run.phase = 'chapter_gen'

6. ── Phase 3: Chapter Gen ──
   Web Studio 显示进度 ③     → 按 parallel_chapter_batch 分批调用 LLM (Haiku)
   （可刷新实时更新）           每批完成后更新 run.completed_chapters
                              进度快照写入 output/<slug>/progress.json
                              更新 run.phase = 'walkthrough'

7. ── Phase 4: Walkthrough ──
   Web Studio 显示进度 ④     → 调用 LLM (Sonnet) 生成 walkthrough
                              存为 PlanningArtifactVersion(type='if_walkthrough')
                              更新 run.phase = 'assembly'

8. ── Phase 5: Assembly ──
   Web Studio 显示进度 ⑤     → 组装 story_package.json
                              写入 output/<slug>/story_package.json
                              更新 run.story_package_path

9. ── Phase 6: Compile ──
   Web Studio 显示 QA 报告   → 调用 compile_story_package.py
                              输出 output/<slug>/build/ 下 4 个文件
                              更新 run.phase = 'completed'

10. 用户下载产物              → GET /api/projects/{slug}/if/download/{filename}
```

### 6.1 中断恢复机制

生成中途中断（网络问题、服务重启）后，用户点击「继续」：

```
GET  /api/projects/{slug}/if/status   → 返回 run.phase + run.completed_chapters
POST /api/projects/{slug}/if/run      → resume=true

服务层逻辑：
  if phase == 'story_bible'   → 直接从 PlanningArtifactVersion 加载已生成的 bible
  if phase == 'arc_plan'      → 加载已有 bible + arc_plan artifact
  if phase == 'chapter_gen'   → 加载 progress.json，从断点章节继续
  if phase == 'walkthrough'   → 加载已有 bible + arc_plans
  if phase == 'assembly'      → 直接重新组装（fast）
  if phase == 'compile'       → 直接重新编译（fast）
```

---

## 7. CLI 命令扩展

在 `cli/main.py` 中新增 `if` 命令组：

```
bestseller if run <slug>            # 运行完整 IF 生成（含 --resume 支持）
bestseller if run <slug> --resume   # 从断点恢复
bestseller if status <slug>         # 显示当前阶段和进度
bestseller if compile <slug>        # 只跑 compile 步骤（已有 story_package.json）
bestseller if download <slug>       # 列出可下载的产物文件
```

---

## 8. 产物文件结构

```
output/
└── <project_slug>/
    ├── story_package.json          # 完整中间产物（Phase 5 Assembly 输出）
    ├── progress.json               # 生成进度快照（Phase 3 实时写入）
    └── build/
        ├── book_<book_id>.json     # App 书目元数据（compile 输出）
        ├── chapters_<book_id>.json # App 章节内容（compile 输出）
        ├── walkthrough_<book_id>.json  # App 攻略图（compile 输出）
        └── qa_report.json          # QA 报告（compile 输出）
```

`story_package.json` 结构（与现有 story-factory 完全一致）：

```json
{
  "book": { "id", "title", "genre", "characters", "initial_stats", ... },
  "reader_desire_map": { "core_fantasy", "reward_promises", ... },
  "story_bible": { "premise", "mainline_goal", "side_threads", ... },
  "route_graph": { "mainline", "milestones", ... },
  "walkthrough": { "stages", "chapter_guides", ... },
  "chapters": [ { "id", "nodes": [...], ... }, ... ]
}
```

---

## 9. 验证规则（复用 compile_story_package.py）

IF 模式下，生成完成后自动执行 `compile_story_package.py` 中的所有验证规则：

| 规则 | 类型 | 说明 |
|------|------|------|
| 章节数等于 `total_chapters` | Error | |
| 章节 ID 唯一 | Error | |
| 章节编号连续从 1 开始 | Error | |
| 每个 choice 节点有 2-4 个选项 | Error | |
| 每个 choice 必须有 result_nodes | Error | |
| walkthrough chapter_guides 覆盖所有章节 | Error | |
| 每章字数在 1200-3200 区间 | Warning | |
| choice 缺少 preview 字段 | Warning | |

`qa_report.json` 最终包含：errors、warnings、章节统计摘要。

---

## 10. 实施阶段

### Phase 1：配置层（开关 + 参数面板）

**交付物**：Web Studio IF 开关可用，IF 配置参数可保存，不涉及生成。

- `domain/project.py`：增加 `IFStatConfig`、`IFCharacterDraft`、`InteractiveFictionConfig`，集成到 `WritingProfile`
- `domain/enums.py`：增加 `ProjectType`、`IFGenerationPhase`、`ArtifactType.IF_*`
- `infra/db/models.py`：`ProjectModel` 增加 `project_type`；增加 `IFGenerationRunModel`
- Alembic 迁移：2 个新字段 + 1 张新表
- `services/writing_profile.py`：`resolve_writing_profile` 支持 IF 覆盖规则
- `web/novel_studio.html`：IF 开关 + 参数面板（故事设定 + 生成参数两组）
- `web/server.py`：`ProjectCreate` 传入 `project_type` 和 IF 配置

**验收**：创建一个 IF 项目，确认 project_type='interactive' 写入 DB，IF config 存在 metadata_json 中。

---

### Phase 2：Prompt 层（if_prompts.py）

**交付物**：将 `generate_story.py` 的 Prompt 函数迁移为 bestseller 服务，支持 IF config 参数注入。

- 新建 `src/bestseller/services/if_prompts.py`
  - 迁移 `_bible_prompt`、`_arc_plan_prompt`、`_chapter_prompt`、`_walkthrough_prompt`
  - 增加 `InteractiveFictionConfig` 参数注入（chapter_text_length、choice_nodes_per_chapter 等）
  - 增加 genre_rule_summary 注入
  - 迁移验证常量（`VALID_STATS`、`VALID_SATISFACTION` 等）
- 单元测试：prompt 生成正确包含 IF config 参数

---

### Phase 3：服务层（if_generation.py）

**交付物**：完整 4 阶段 + Assembly + Compile 流水线可运行。

- 新建 `src/bestseller/services/if_generation.py`
  - `build_concept_json`
  - `generate_story_bible`（Phase 1，存 PlanningArtifactVersion）
  - `generate_arc_plan`（Phase 2，存 PlanningArtifactVersion）
  - `generate_chapters`（Phase 3，写 progress.json，支持 resume）
  - `generate_walkthrough`（Phase 4，存 PlanningArtifactVersion）
  - `assemble_story_package`（Phase 5）
  - `compile_story_package`（Phase 6，调用现有 compile_story_package.py）
  - `run_if_pipeline`（入口）
- `cli/main.py`：`if` 命令组（run / resume / status / compile）
- 集成测试：小规模（3章）端到端跑通

---

### Phase 4：Web Studio 集成

**交付物**：Web Studio 看板显示 IF 生成进度，产物可下载。

- `web/server.py`：
  - `POST /api/projects/{slug}/if/run`
  - `GET  /api/projects/{slug}/if/status`
  - `GET  /api/projects/{slug}/if/download/{filename}`
  - `POST /api/projects/{slug}/if/cancel`
- `web/novel_studio.html`：
  - 执行看板新增 IF 进度视图（4阶段进度条 + 当前章节显示）
  - QA 报告展示
  - 产物文件下载按钮
- E2E 测试：从 Web Studio 创建 IF 项目 → 生成 → 下载 qa_report

---

## 11. 关键设计决策说明

### 11.1 为什么不新建独立的 IF 章节表？

story_package.json 中章节的 `nodes` 是高度嵌套的 JSON，结构与线性章节的 `scene draft` 完全不同。用 JSONB 存整个 chapter 对象比建 nodes 表更简单，且 compile_story_package.py 只需要文件输入，不依赖 DB 结构。

### 11.2 为什么 Prompt 层单独成文件？

`generate_story.py` 已经是经过验证的 Prompt，不应在集成过程中随意改动。将其迁移到 `if_prompts.py` 后，Prompt 本身保持 story-factory 已验证版本，只在边界处增加 IF config 参数注入，降低引入回归的风险。

### 11.3 compile_story_package.py 保持不变

现有 `story-factory/scripts/compile_story_package.py` 不做任何修改。`if_generation.py::compile_story_package()` 通过 Python subprocess 或直接 import 调用它，保持 story-factory 子目录的独立性。

### 11.4 生成进度的实时更新

Chapter Gen 阶段耗时最长（100章约需数分钟）。进度通过两个机制更新：
- `run.completed_chapters` 每批（8章）完成后写入 DB
- Web Studio 前端每 5 秒 poll `GET /api/.../if/status` 刷新进度条
