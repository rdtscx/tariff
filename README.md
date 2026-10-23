# 运营商资费监控 · 数据展示站（移动）

本仓库是一个**自包含**的运营商资费监控项目：定时抓取中国移动官网的公开资费数据，
自动构建静态展示站点，并可选推送变化到钉钉 / 飞书 / 企业微信 / 邮箱。

抓取、构建、推送全部由本仓库的 GitHub Actions 完成（公开仓免费且不限量），
**不含任何代码密钥**——所有凭据走环境变量与 Secrets。

> **策略说明**：自 2026-10-24 起，联通 / 电信 / 广电的抓取与部署已永久停用，
> 仅维护**中国移动**资费链路（移动全量抓取 + 站点自动构建部署）。仓库中
> `unicom/`、`telecom/`、`gb/` 目录仅作为历史快照留存，不再自动更新。

## 在线访问

- 中国移动（实时更新）：<https://rdtscx.github.io/tariff/>
- 中国联通（历史快照，已停止更新）：<https://rdtscx.github.io/tariff/unicom/>
- 中国电信（历史快照，已停止更新）：<https://rdtscx.github.io/tariff/telecom/>
- 中国广电（历史快照，已停止更新）：<https://rdtscx.github.io/tariff/gb/>

## 数据说明

- **移动**：全网资费（全国）+ 31 省，含套餐 / 加装包 / 营销活动 × 个人 / 政企 全分类
- 由本仓库的 GitHub Actions 每 2 小时定时抓取并自动部署更新，无需外部依赖
- 数据均为运营商官网公示的公开信息，仅供参考，办理业务请以营业厅官方信息为准

## 站点功能

省份切换与搜索、分类筛选、资费变化历史追踪、**CSV 导出**、**收藏关注**（保存在本机浏览器）。


---

## 📮 接入你自己的通知通道

fork 本项目后，填入**你自己的**凭据，资费变化就能推送到**你的**
**钉钉 / 飞书 / 企业微信 / 邮箱**。四个通道任意组合，配几个推几个。

**完整教程见 → [NOTIFY_SETUP.md](NOTIFY_SETUP.md)**

| 通道 | 要填的 Secrets |
|---|---|
| 🔵 钉钉 | `DINGTALK_WEBHOOK` + `DINGTALK_SECRET` |
| 🟦 飞书 | `FEISHU_WEBHOOK` + `FEISHU_SECRET` |
| 🟩 企业微信 | `WECOM_WEBHOOK` |
| 📧 邮件 | `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `MAIL_TO` |

三步速览：

1. 在对应群里添加机器人，拿到 Webhook 地址（邮件则用邮箱授权码）
2. 仓库 **Settings → Secrets and variables → Actions** 添加上表变量
3. Actions 运行 **Tariff Notify**，约 14 秒后收到；或本地 `python3 scripts/notify.py --test`

推送示例：

```
- 🔵 移动：新增 340、下架 284、修改 1196
  - 湖南：无变化

**合计**：新增 340、下架 284、修改 1196
🔗 查看完整资费站
```

> 🔒 密钥只存在你自己的仓库里，加密存储且**只写不读**，作者与他人均无法查看。
> 抓取与推送脚本**不含任何硬编码凭据**，全部通过环境变量注入。

## 自动化说明

抓取与推送**全部在本仓库内完成**，跑于 GitHub Actions（公开仓免费、不限量）：

| 工作流 | 频率（北京） | 说明 |
|---|---|---|
| `mobile-build.yml` | 每 2 小时（0/2/4…/22 点） | 移动抓取 + 站点构建 + 自动部署 |
| `mobile-heartbeat.yml` | 随每轮抓取结束后约 2 小时 | 心跳自续：抓取完成后延时 95 分钟自动触发下一轮，不依赖 GitHub schedule |
| `announce-sync.yml` | 每 1 小时（:17 / :47） | 移动公告同步 |
| `dingtalk-summary.yml` | 每日 08:00 / 20:00 | 移动资费变化汇总推送 |
| `build-watchdog.yml` | 每 30 分钟 | 看门狗：取消卡死超 2 小时的构建运行，防止并发组排死导致数据停更 |

> 说明：联通 / 电信 / 广电的抓取 workflow（`unicom-fetch.yml`、`telecom-gb-fetch.yml`、
> `telecom-announce.yml`）已删除，三家的抓取与部署永久停用，仅保留历史数据快照。
> probe / verify / diag-dns 等诊断类一次性工作流已删除，避免触发失败产生通知邮件。
