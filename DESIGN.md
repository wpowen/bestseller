# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-05-23
- Primary product surfaces: Web Studio quickstart, 书稿库, 阅读器, 设计审查包, REST inspection APIs.
- Evidence reviewed: README.md, src/bestseller/web/server.py, src/bestseller/web/novel_library.html, src/bestseller/web/novel_reader.html, src/bestseller/services/inspection.py, src/bestseller/services/narrative.py, src/bestseller/services/story_design_kernel.py, docs/architecture.md, docs/plans/2026-05-12-story-design-core-capability-integration.md, tests/unit/test_web_server.py.

## Brand
- Personality: editorial, production-grade, auditable, literary-engineering focused.
- Trust signals: structured counts, source artifacts, current versions, readiness checks, explicit missing surfaces.
- Avoid: marketing hero copy, decorative dashboards, opaque "quality" labels without backing evidence, hiding missing design data until final chapters exist.

## Product goals
- Goals: expose every book's design-stage products before chapter-level lag hides structural problems; make outline, cast, relationship graph, world rules, narrative lines, contracts, and planning artifacts inspectable from the library.
- Non-goals: replace the prose reader, edit JSON inline, create a separate design-system layer, or generate new story content from the review surface.
- Success signals: an editor can open one book and verify whether the critical design surfaces exist; missing outline/cast/relationships/contracts are visible as gaps; detailed source artifacts remain accessible for audit.

## Personas and jobs
- Primary personas: editor/operator running automated novel production; developer maintaining pipeline observability; reviewer diagnosing planning failures.
- User jobs: inspect book design before drafting; compare intended arcs against generated chapters; find missing relationship/world/contract data; trace a visible issue back to its planning artifact.
- Key contexts of use: active generation monitoring, post-failure repair, prewrite readiness review, finished-book retrospective.

## Information architecture
- Primary navigation: 书稿库 -> book card -> 阅读正文 or 设计审查.
- Core routes/screens: `/library`, `/read/{slug}`, `/design/{slug}`, `/api/projects/{slug}/design-dossier`, `/api/projects/{slug}/design-artifact?artifact_id=...`, existing `/api/projects/{slug}/structure`, `/story-bible`, `/narrative`, `/workflow`.
- Content hierarchy: project identity -> readiness and coverage -> planning artifacts -> outline -> cast -> relationship graph -> world bible -> narrative graph -> chapter/scene contracts.

## Design principles
- Principle 1: Show upstream intent before downstream prose; the editor should not need to read final chapters to detect missing design work.
- Principle 2: Treat absence as data; empty people, relationships, outline, or contracts must be called out as review gaps.
- Tradeoffs: dense editorial surfaces are preferred over spacious marketing layouts; raw JSON is acceptable when it preserves auditability, but summaries must make first-pass review fast.

## Visual language
- Color: continue the existing warm manuscript palette, with restrained red/green/amber/blue accents for gaps, present surfaces, warnings, and structural groupings.
- Typography: Source Serif / Noto Serif SC for editorial reading; JetBrains Mono for counts, artifact types, versions, and machine states.
- Spacing/layout rhythm: compact dashboards and full-width bands; repeated items may use bordered cards.
- Shape/radius/elevation: square or small-radius editorial panels; no floating nested cards or heavy shadows.
- Motion: minimal; tab switching and refresh only.
- Imagery/iconography: relationship graph is rendered as a node-link relationship network with labeled edges and a fixed evidence list; no decorative imagery required for this operational tool.

## Components
- Existing components to reuse: static HTML/CSS pattern in `novel_library.html` and server route pattern in `web/server.py`.
- New/changed components: 设计审查 book action, `/design/{slug}` dossier page, `/api/projects/{slug}/design-dossier` aggregate payload, 展开即加载的原始产物查看器, 侦查式关系图谱, readiness gap helper.
- Variants and states: loading, error, empty surface, present surface, missing surface, collapsed artifact JSON, responsive single-column layout.
- Token/component ownership: local static page CSS owns this surface; shared app tokens are inferred from existing web pages until a central token file exists.

## Accessibility
- Target standard: practical WCAG AA for contrast and keyboard navigation.
- Keyboard/focus behavior: native buttons, links, details/summary, and tabs remain focusable.
- Contrast/readability: dark text on paper background; warning colors do not carry meaning without text labels.
- Screen-reader semantics: tab nav has an aria label; relationship SVG has an accessible label; sections retain textual counts and lists.
- Reduced motion and sensory considerations: no animation required.

## Responsive behavior
- Supported breakpoints/devices: desktop editor workstations first; tablet and mobile single-column fallbacks.
- Layout adaptations: 12-column grid collapses to full-width panels; metric grid moves from four to two to one columns; outline chapters collapse to one column.
- Touch/hover differences: all commands are visible text buttons and do not depend on hover-only affordances.

## Interaction states
- Loading: dossier status text while fetching.
- Empty: each surface has explicit empty copy for missing data.
- Error: fetch failure renders a concise error in the page status area.
- Success: readiness status and counts update from the aggregate endpoint.
- Disabled: not used.
- Offline/slow network, if applicable: static page remains loaded; refresh reports fetch failure.

## Content voice
- Tone: concise editorial operations language.
- Terminology: use "设计审查", "规划产物", "大纲", "人物", "关系图", "世界观", "叙事线", "合约", and "缺口" consistently.
- Microcopy rules: name the missing surface and consequence directly; avoid vague positive labels without counts.

## Implementation constraints
- Framework/styling system: Python stdlib HTTP server serving static HTML; no frontend build step.
- Design-token constraints: no new dependency or component framework.
- Performance constraints: aggregate endpoint returns planning artifact metadata plus normalized design surfaces; large raw workflow logs and full artifact JSON should stay out of the first-load payload.
- Compatibility constraints: preserve existing `/library`, `/read/{slug}`, and project inspection APIs.
- Test/screenshot expectations: unit tests cover readiness gap logic and page/route markers; browser smoke is useful when a dev server is available.

## Open questions
- [ ] Should planning artifacts be editable from the design dossier, or remain read-only audit material? / product owner / affects mutation and permissions.
- [ ] Should the relationship graph support filtering by chapter frontier? / editor / affects API shape and graph controls.
- [ ] Should very large artifact content get an on-demand detail endpoint? / developer / affects whether the planning artifact tab can expand from metadata into raw source JSON.
