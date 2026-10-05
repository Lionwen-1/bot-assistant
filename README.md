<p align="center"><img src="assets/bot-assistant.png" alt="bot助手图标" width="96"></p>

<h1 align="center">bot助手</h1>

<p align="center">多账号 QQ Bot 与 AstrBot 项目运维台<br><em>A self-hosted Windows control desk for AstrBot and NapCat QQ bots.</em></p>

<p align="center">Windows · 本机或远程部署 · 自带账号管理 · 仅非商业使用</p>

[English](README.en.md) · [部署方式](#选择部署方式) · [快速开始](#下载与开始使用) · [验证范围](#验证状态) · [著作权与许可](COPYRIGHT.md) · [支持项目](#支持项目)

> **当前版本：0.7.1。** Windows 免 Docker 部署仍在预发布阶段：官方组件已在隔离项目启动并生成二维码，尚未用独立测试号完成真实扫码、聊天收发和多账号并发验收。请先在测试环境试用。

## 能做什么

| 功能 | 说明 |
| --- | --- |
| 首次部署 | 一个 Windows EXE 引导本机免 Docker、本机 Docker 或远程 SSH 项目；首次使用按所选模式下载官方组件。 |
| 多账号连接 | 每个 QQ 号独立保存登录数据；账号卡片随实际账号数量变化，支持头像、状态、扫码、改名和单号掉线恢复。 |
| Bot 设置 | 配置自己的模型 API、角色与可选语音；查看不含密钥的设置摘要。 |
| 思维强度 | 对支持的 DeepSeek 模型，按 QQ 账号分别设置关闭、低、高、最高；管理员也可在 QQ 会话中用自然语言调整。 |
| 项目运维 | 查看服务、日志、备份、非敏感配置和插件清单，并打开 AstrBot 控制台。 |

## 选择部署方式

| 模式 | 适合谁 | 需要准备 | QQ 聊天连接 |
| --- | --- | --- | --- |
| **本机免 Docker** | 想在一台 Windows 电脑上试用 | 空项目目录、自己的模型 API；首次下载需联网 | 向导自动配置 OneBot 平台；真实多号聊天仍待独立验收。 |
| **本机 Docker** | 已使用 Docker Desktop 的用户 | Docker Desktop 的 Linux 容器环境 | 每个 QQ 号需在 AstrBot 控制台完成一次 [OneBot 对接](docs/DOCKER_CONNECT.md)。 |
| **远程 SSH** | 已有 Linux Docker 服务器的维护者 | SSH 私钥、Python 3.9+、Docker Engine 与 Compose | 新项目按 [OneBot 对接](docs/DOCKER_CONNECT.md)配置；现有账号可[接管](docs/ADOPT_EXISTING.md)。 |

本项目不附带任何个人 API Key、QQ 登录资料或云端账号配置。不同模式的连接资料分别保留，切换模式不会删除另一个项目的数据。

## 下载与开始使用

1. 从 [0.7.1 预览版](https://github.com/Lionwen-1/bot-assistant/releases/tag/v0.7.1)下载 `BotAssistant-0.7.1-win64.zip`，解压后双击 `BotAssistant.exe`。也可从 **Actions → Windows build** 获取最新构建，或按下文从源码构建。运行 EXE 无需安装 Python。
2. 在“连接设置”选择部署方式。免 Docker 模式选择新的空目录，并填写自己的模型 API、AstrBot 控制台密码、角色与可选语音设置；其他模式按界面填写本机项目或 SSH 服务器信息。
3. 点击“添加账号”，输入 QQ 号和昵称；在该账号卡片获取二维码，用手机 QQ 扫码确认。
4. 登录后查看账号状态。掉线时只操作对应卡片的“获取登录码”；程序会检查状态与旧码时效，状态不明时不会重启账号。
5. 进入“项目运维”查看服务和备份；进入“Bot 设置”管理模型、语音、角色及思维强度。

需要桌面快捷方式时，可运行 ZIP 中的 `Install-BotAssistant-Desktop.ps1`。它会保留已有连接配置。免 Docker 模式的完整流程和故障排查见 [NATIVE_SETUP.md](NATIVE_SETUP.md)。

## 模型、角色与语音

新部署项目从使用者自己的模型配置开始。可在 **Bot 设置 → 在程序中配置** 填写 API 地址、模型与密钥；现有 AstrBot 项目也可以通过“配置 API”打开官方控制台设置。语音识别（STT）和语音回复（TTS）分别开启，角色提示词可单独修改。

密钥保存在使用者选择的 AstrBot 项目中。bot助手的远程连接配置只记录服务器地址、私钥**路径**与已核对的主机公钥；不会复制私钥，也不会在项目摘要中显示 API Key。同一个 AstrBot 项目的 QQ 账号默认共享模型和角色；不同角色或模型的路由仍在 AstrBot 中配置。

### 按账号调节思维强度

打开 **Bot 设置 → 调节思维强度**，首次点击“启用思维调节”加载组件；这会短暂重启 AstrBot，不重启 NapCat。之后选择 QQ 账号和档位，保存后从该账号的下一条消息起生效。

| 档位 | 传给 DeepSeek 的值 |
| --- | --- |
| 关闭 | `none` |
| 低 | `low` |
| 高（默认） | `high` |
| 最高 | `max` |

目前支持 `deepseek-flash` 和 `deepseek-v4-pro`。更换基础模型后需重新启用组件；其他模型会在窗口中显示不支持原因。参数遵循 [DeepSeek 思考模式文档](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/)。

**AstrBot 管理员**还可以在对应 QQ 会话中发送“把思考强度调低”“把思考强度调到最高”“关闭思考”或“现在思考强度是多少”。指令由本地规则处理，不为切换档位调用模型；普通成员不能修改。每号档位独立保存于项目的 `data/settings/bot_assistant_thinking.json`。实际耗时与费用以模型服务商账单为准。

## 数据与边界

- 每个 QQ 号有独立的 NapCat 登录目录。移除新建账号会停止该号进程或容器并移出列表，默认保留登录数据以供重新添加。
- 备份包含配置、数据库和 QQ 登录目录，可能含凭据；请妥善保管。本机备份在所选项目的 `backups/`，远程备份留在服务器项目的 `backups/`。
- 配置预览只显示非敏感文件；插件启停、更新及高级 AstrBot 设置由 AstrBot 官方控制台处理。
- 项目不开放公网 NapCat 管理端口。远程 AstrBot 控制台通过临时 SSH 隧道访问。
- bot助手与 AstrBot、NapCatQQ、腾讯 QQ 官方均无隶属关系。AstrBot、NapCat、QQ、Docker 程序不随本仓库或 EXE 再分发；EXE 内的 Python、Pillow 等依赖另见 [第三方许可与条件](THIRD_PARTY.md)。

## 验证状态

| 项目 | 当前结果 |
| --- | --- |
| 单元测试与 Windows EXE 自检 | 0.7.1 本地构建通过 42 项测试与内置资源检查。 |
| 现有云端项目 | 两个已接管账号的状态、项目服务和角色隔离已检查；思维档位按号保存与恢复已验证。 |
| 管理员 QQ 自然语言指令 | 规则与权限有自动测试；真实 QQ 消息调用尚未实测。 |
| 全新本机免 Docker 项目 | 官方组件启动及二维码生成已验证；真实扫码、聊天回复和多号并发待独立测试号验收。 |

上表记录已有验证范围，不代表所有 Windows 环境、QQ 版本或模型服务均已通过。

## 开发与构建

```powershell
python -m unittest discover -s tests -v
./scripts/build.ps1
```

构建脚本输出 `release/BotAssistant.exe` 和版本化的 Windows ZIP，并从构建环境收集随 EXE 分发的 Python、Tcl/Tk、Pillow 许可文本。`python run.pyw --demo` 可预览三张标有“演示账号”的卡片，不连接真实账号或服务器。

代码分布：`bot_assistant/` 是 Windows 客户端，`server/manager.py` 是远程管理脚本，`server/thinking_plugin/` 是思维强度插件，`tests/` 是模拟测试。发布包包含本项目代码、图标和随包依赖，首次部署所需的 AstrBot、NapCat、QQ 或 Docker 程序由使用者从官方来源下载。

## 进一步阅读

- [Windows 免 Docker 部署](NATIVE_SETUP.md)
- [本机 Docker / 远程 OneBot 对接](docs/DOCKER_CONNECT.md)
- [接管现有 NapCat 账号](docs/ADOPT_EXISTING.md)
- [软著登记准备说明](docs/COPYRIGHT_REGISTRATION.md)
- [第三方组件与许可](THIRD_PARTY.md)
- [公开发布前隐私核查](docs/PUBLIC_RELEASE_AUDIT.md)

## 文件导航

```text
bot-assistant/
├─ bot_assistant/         Windows 桌面客户端
├─ server/                SSH 管理脚本与思维强度插件
├─ scripts/               构建与桌面快捷方式脚本
├─ tests/                 模拟测试
├─ docs/                  对接、接管、软著准备与赞赏码
├─ NATIVE_SETUP.md        免 Docker 部署说明
├─ THIRD_PARTY.md         第三方组件和许可
├─ COPYRIGHT.md           著作权、授权和侵权反馈
├─ NOTICE                 权利声明
└─ LICENSE                PolyForm Noncommercial 1.0.0
```

## 许可与来源

感谢 [AstrBot](https://github.com/AstrBotDevs/AstrBot)、[AstrBot Desktop](https://github.com/AstrBotDevs/AstrBot-desktop)、[NapCatQQ](https://github.com/NapNeko/NapCatQQ) 与 [NapCat-Docker](https://github.com/NapNeko/NapCat-Docker) 的维护者，以及 [OneBot v11](https://github.com/botuniverse/onebot-11) 协议社区。bot助手基于这些项目提供的运行环境、接口和协议构建；具体用途、源码边界、作者与许可见 [上游项目与第三方许可清单](THIRD_PARTY.md)。

bot助手 0.7.1 起的原创代码与文档采用 [PolyForm Noncommercial 1.0.0](LICENSE)，仅许可非商业用途；项目源码可供查看和按许可使用，**不是 OSI 定义的开源软件**。此前按 MIT 发布的副本保留原有 MIT 授权。详情见 [著作权与侵权反馈](COPYRIGHT.md)；上游项目和随包依赖仍各用自己的许可。

## 支持项目

bot助手的非商业使用无需赞助。若项目对你有帮助，可以给仓库点 Star，也可以自愿赞赏维护者 Lionwen；赞赏不构成商业使用授权。

<a href="docs/media/support-lionwen.png"><img src="docs/media/support-lionwen.png" alt="Lionwen 的赞赏码" width="480"></a>

[打开原尺寸赞赏码](docs/media/support-lionwen.png)
