---
name: bela-github-publish
description: >
  Standard workflow for publishing a local project to a new public GitHub repository.
  Use when the user says "发布到 GitHub", "publish to GitHub", "push to GitHub",
  "create a GitHub repo", "我要发布仓库", or "帮我发布到 GitHub".
  Covers project structure, bilingual README, privacy scan, git init, repo creation, and verification.
---

# GitHub Publish Workflow

将本地项目发布到 GitHub 的标准流程。每次发布走这一套，不重复踩坑。

---

## 命名规范

**读取用户前缀：** 先读 `shared/publish-profile.md` 的 `repo_prefix` 字段，用该前缀构造仓库名。

- **格式：** `{repo_prefix}` + 内容描述，全小写，用连字符分隔
- **示例：** 若 `repo_prefix: bela`，则命名为 `bela-content-creator`、`bela-job-briefing`

> 如果 `shared/publish-profile.md` 不存在：提示用户"请先复制 `publish-profile.template.md` 到 `shared/` 并填写 `repo_prefix`，或告诉我你想用的前缀，本次会话直接使用"。不要假设前缀，不要写死。

## 语言页面规范

- 默认展示英文版（`README.md`）
- 顶部第一行提供中文版跳转链接，点击后进入独立中文页面
- **中英文必须是两个独立文件**，不允许同一文件内上英下中

```
README.md       → 第一行：[中文版](./README.zh-CN.md)
README.zh-CN.md → 第一行：[English](./README.md)
```

---

## Skill 写作原则（每次新建 SKILL.md 必须遵守）

这两条是底层约束，优先级高于其他所有写法建议。参考标杆：宝玉的 skill 体系和张枣拉的 daily-job-briefing——它们的共同特点是软编码 + 边界处理做得彻底。

### 原则一：软编码，不硬编码

**硬编码的典型错误：**
- 把公司名、城市名、行业名直接写死在 skill 里
- 把搜索词、输出格式、文件名固定死，不读用户配置
- 用户信息（背景、偏好、目标）直接写进 SKILL.md 正文

**正确做法：**
- 所有用户相关信息从 `shared/xxx-profile.md` 读取，skill 只定义字段名和读取逻辑
- 搜索词、公司列表、模板等放进 `references/` 或 `templates/` 目录，skill 引用路径而非内容
- 输出文件名用变量构造（如 `YYYY年M月D日`），不写死
- 触发词、语言、频率等偏好让用户在 profile 里设置，skill 动态读取

```
✅ 读取：profile.target_city → 搜索词变量
❌ 写死："上海 AI 产品经理 招聘"
```

### 原则二：处理边界条件，做到足够灵活

每个 skill 在写作时，必须预先考虑并明确处理以下边界情况：

| 边界类型 | 要求 |
|----------|------|
| **文件不存在** | profile / template / references 文件缺失时，明确说明 fallback 行为（提示用户创建 / 用默认值继续 / 终止并给出指引） |
| **搜索无结果** | 明确写"搜索返回空时怎么办"（换词重搜 / 标注⚠️ / 跳过该来源） |
| **字段为空** | profile 某字段用户没填时，写清楚默认值是什么，不能直接报错或跳过 |
| **多次运行** | 第二次、第三次运行时行为是否和第一次一样？有历史记录时如何去重？ |
| **首次运行** | 明确标注"首次运行"的特殊流程（onboarding、文件初始化等） |
| **输出目录不存在** | 写文件前先判断目录是否存在，不存在则创建，不能假设目录已在 |
| **外部依赖不可用** | 依赖另一个 skill 或工具时，它不存在时的降级方案要写清楚 |

**每个 Phase / Step 后要附 fallback 说明**，格式：
```
> 如果 [条件不满足]：[具体处理方式]，不要直接失败。
```

---

## Phase 1：结构检查

发布前确认项目具备以下文件结构。缺什么补什么。

### 必须存在

```
README.md                  # 英文主 README（默认展示）
README.zh-CN.md            # 中文 README（独立文件，不是同一文件内拼接）
.gitignore                 # 至少包含 shared/ 和 .DS_Store
```

### 如果是 Claude Code plugin，还需要

```
.claude-plugin/
  plugin.json              # name, version, description, author, keywords
skills/
  [skill-name]/
    SKILL.md               # frontmatter: name + description + trigger words
```

### 推荐包含

```
publish-profile.template.md  # 命名前缀、GitHub 账号等发布配置的填写模板
[project-name].template.md   # 其他用户本地配置文件的填写模板（不含真实数据）
```

---

## Phase 2：隐私脱敏扫描

**在 git add 之前必须执行**，不是可选项。

### 扫描命令

```bash
grep -rn \
  "真实姓名\|前公司名\|手机号\|邮箱\|微信号\|身份证\|具体薪资数字\|家庭地址" \
  . --include="*.md" --include="*.json" --include="*.txt" \
  2>/dev/null && echo "FOUND SENSITIVE" || echo "CLEAN"
```

根据实际项目，把上面的关键词替换为该项目相关的敏感词（公司名、人名、账号等）。

### 脱敏原则

| 文件类型 | 处理方式 |
|----------|----------|
| `shared/` 目录下的画像、配置 | 加入 `.gitignore`，永不提交 |
| SKILL.md / README 中的硬编码个人信息 | 替换为"读取 `shared/xxx.md`"的动态引用 |
| 模板文件（.template.md） | 只写字段名和占位符，不填真实值 |
| plugin.json 的 author | 只填笔名或 GitHub 用户名，不填真实姓名 |

### .gitignore 最低配置

```gitignore
# 本地私有配置，不上传
shared/

# OS
.DS_Store
```

---

## Phase 3：README 规范

### 结构要求

两个独立文件，**不是一个文件里上下拼接**：

- `README.md` — 纯英文
- `README.zh-CN.md` — 纯中文

每个文件**第一行**是跳转链接：

```markdown
<!-- README.md 第一行 -->
[中文版](./README.zh-CN.md)

<!-- README.zh-CN.md 第一行 -->
[English](./README.md)
```

### README 写作原则（先看这里）

README 是给真人看的，不是给 AI 评审的。写完之后问自己：**这像一个做过这件事的人写的，还是像一份产品说明书？**

**禁止使用的表达：**
- "automates your workflow" / "自动化你的工作流"（套话）
- "personalized to your background and platform"（废话）
- "streamlines"、"leverages"、"empowers"（AI 高频词）
- 过多的 bullet list 堆砌（读起来像 PPT 大纲）
- 每个功能都以"支持……"开头（像产品经理写的需求文档）

**应该做到的：**
- 开头第一段讲一个真实场景，让目标用户立刻认出自己
- 功能描述说清楚"你得到什么"，不只是"它做什么"
- 安装说明：先给最简单的一条命令，其他方式折叠在后面，不堆并列
- 中文版要比英文版更口语化，用"你"，说短句，可以带一点个人口吻

### README 必须包含的章节（顺序）

1. **开场故事/痛点**（1–3 句，具体场景，不是泛泛介绍，不涉及个人隐私）
2. **里面有什么**（每个功能单独一段，说触发词 + 你会得到什么，散文 > 表格）
3. **它怎么工作 / 个性化机制**（profile 怎么建立，数据在哪里）
4. **安装**（一条主命令 + 其他方式作为补充，不要 4 个并列标题）
5. **隐私说明**（1–2 句，说清楚数据不出本地）
6. **目录结构**（代码块，关键文件加行内注释）

### 四种标准安装方式（Claude Code plugin 通用）

```markdown
### 方式一 — 直接 clone（推荐）
\`\`\`bash
git clone https://github.com/[用户名]/[仓库名].git ~/.claude/plugins/[仓库名]
\`\`\`
重启 Claude Code 即可使用。

### 方式二 — 通过 Claude Code 插件命令
\`\`\`bash
/plugin install https://github.com/[用户名]/[仓库名]
\`\`\`
> 具体语法因版本不同可能有差异，以当前版本文档为准。

### 方式三 — 通过 Superpowers / 第三方 skill 管理器
\`\`\`
/find-skills [仓库名]
\`\`\`

### 方式四 — 手动下载（无需 git）
1. [点击下载 ZIP](https://github.com/[用户名]/[仓库名]/archive/refs/heads/main.zip)
2. 解压后复制到 `~/.claude/plugins/[仓库名]/`
3. 重启 Claude Code
```

---

## Phase 4：Git 初始化 & 发布

### 步骤

```bash
# 1. 初始化
cd /path/to/project
git init

# 2. 暂存（指定文件，不用 git add -A 避免误提交）
git add README.md README.zh-CN.md .gitignore .claude-plugin/ skills/
# 如有其他文件按需追加，每次确认没有 shared/ 混进去

# 3. 确认暂存内容（肉眼核查一遍）
git status

# 4. 提交
git commit -m "feat: initial release vX.X.X — [一句话描述]

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"

# 5. 创建 GitHub 仓库并推送
gh repo create [仓库名] \
  --public \
  --description "[英文一句话描述]" \
  --source . \
  --remote origin \
  --push

# 如果 SSH 被拦，切换 HTTPS 重推
git remote set-url origin https://github.com/[用户名]/[仓库名].git
git push -u origin main
```

---

## Phase 5：发布后验证

发布完必须跑以下验证，不做裸奔声明。

```bash
# 验证 1：仓库元信息
gh repo view [用户名]/[仓库名] \
  --json name,url,description,visibility,defaultBranchRef

# 验证 2：文件结构是否完整
gh api "repos/[用户名]/[仓库名]/git/trees/HEAD?recursive=1" \
  --jq '.tree[].path' | sort

# 验证 3：敏感词最终扫描（用本项目的关键词替换）
git ls-files | xargs grep -in "敏感词1\|敏感词2" 2>/dev/null \
  && echo "FOUND" || echo "CLEAN"
```

**验收标准：**
- `visibility: PUBLIC` ✅
- 文件树包含 README.md、README.zh-CN.md、.gitignore ✅
- 敏感词扫描返回 CLEAN ✅

---

## Phase 6：后续版本迭代规范

### 版本号约定（plugin.json）

| 变更类型 | 版本号变化 | 示例 |
|----------|------------|------|
| 新增 skill / 重大功能 | minor +1 | 0.2.0 → 0.3.0 |
| 修复 bug / 小调整 | patch +1 | 0.3.0 → 0.3.1 |
| 重大重构或不兼容变更 | major +1 | 0.3.0 → 1.0.0 |

### Commit message 格式

```
feat: 新功能描述
fix: 修复描述
docs: 文档变更
refactor: 重构（不影响功能）
security: 安全/脱敏相关

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

---

## 快速检查清单（每次发布前过一遍）

- [ ] `shared/` 在 `.gitignore` 里
- [ ] `README.md` 纯英文，第一行是中文跳转链接
- [ ] `README.zh-CN.md` 纯中文，第一行是英文跳转链接
- [ ] README 包含：背景、功能表格、安装4种方式、隐私说明
- [ ] `plugin.json` 版本号已更新，无真实姓名
- [ ] 敏感词扫描 CLEAN
- [ ] `git status` 确认没有 `shared/` 文件混入
- [ ] 发布后 `gh repo view` 确认 visibility: PUBLIC
