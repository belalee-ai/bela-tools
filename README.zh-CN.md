[English](./README.md)

# bela-tools

有两件事我一直在手动做，直到把它们变成 skill。

一件是把项目发布到 GitHub——脱敏扫描、README 结构、git 推送流程、发布后验证。另一件是在 Claude Code 里管理 skill 列表，防止装太多之后触发错 skill。

两个都在这里。

---

## 里面有什么

**GitHub 发布** — 说 `发布到 GitHub` 或 `publish to GitHub`

完整的发布流程：项目结构检查、隐私扫描、双语 README 规范、git 初始化和推送、发布后验证。那些踩过一次就不想再踩的坑——SSH 切 HTTPS、gitignore 保护私有数据、命名前缀——都在里面。

**Skill 清理** — 说 `skill audit` 或 `检查 skill 重叠`

装新 skill 之前先查重。skill 超过 30 个之后开始触发错，这时候跑完整审查——找重叠、建议合并、软删除不用的，历史记录保留。

---

## 安装

```bash
git clone https://github.com/belalee-ai/bela-tools.git ~/.claude/plugins/bela-tools
```

重启 Claude Code，就好了。

**其他安装方式**

- 在 Claude Code 里输入 `/plugin install https://github.com/belalee-ai/bela-tools`（部分版本支持）
- 装了 Superpowers 的话，输入 `/find-skills bela-tools`
- [下载 ZIP](https://github.com/belalee-ai/bela-tools/archive/refs/heads/main.zip)，解压后拖到 `~/.claude/plugins/bela-tools/`，重启

---

## 关于隐私

这两个 skill 都不涉及个人画像文件。skill-hygiene 的审查脚本只在本地跑，读的是 `~/.claude/skills/`，日志写到 `~/.claude/skills-audit.log`，不往外发。

---

## 目录结构

```
.claude-plugin/plugin.json              版本、关键词、作者
skills/
  bela-github-publish/SKILL.md          GitHub 发布流程（6 个阶段）
  skill-hygiene/SKILL.md                skill 审查和重叠检测
    scripts/skill-audit.sh              本地审查脚本
```
