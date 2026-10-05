# 上游项目、第三方组件与许可

bot助手最初围绕 AstrBot 和 NapCat 的现有能力开发，并使用 OneBot v11 协议连接两者。感谢这些项目及其维护者。本清单说明**本仓库实际怎样使用它们**；致谢不代表上游认可、参与或授权 bot助手使用其商标。

本仓库的桌面客户端、管理脚本和思维调节插件是本项目的集成代码。按当前仓库及最初提交的文件核对，没有将 AstrBot、AstrBot Desktop、NapCatQQ 或 NapCat-Docker 的源码、镜像、安装包复制进本仓库或 bot助手 EXE。它们在使用者选择部署后，从各自发布渠道下载，或由使用者已有的安装提供。若以后引入上游源码、图片或其他素材，须在引入时记录具体文件、原作者、版本和原始许可，不能只靠本页的一般致谢代替许可义务。

## 项目所依托的上游

| 上游及作者 | 本项目中的用途与位置 | 获取方式和上游条款 |
| --- | --- | --- |
| [AstrBot](https://github.com/AstrBotDevs/AstrBot)，AstrBotDevs / Soulter 及贡献者 | Bot 框架；`bot_assistant/local.py` 部署其容器，`server/manager.py` 管理现有服务，`server/thinking_plugin/` 通过公开的 AstrBot 插件 API 接入。 | 使用者拉取镜像或连接自己的已有服务；上游为 [AGPL-3.0](https://github.com/AstrBotDevs/AstrBot/blob/master/LICENSE)，另见其 [EULA](https://github.com/AstrBotDevs/AstrBot/blob/master/EULA.md)。本仓库不打包 AstrBot。 |
| [AstrBot Desktop](https://github.com/AstrBotDevs/AstrBot-desktop)，AstrBotDevs 及贡献者 | `bot_assistant/native_runtime.py` 在免 Docker 模式下载 v4.28.1 Windows 便携版，作为本机 AstrBot 运行环境。 | 使用者首次部署时从上游 Release 下载；该桌面项目自己的 [LICENSE](https://github.com/AstrBotDevs/AstrBot-desktop/blob/main/LICENSE) 为 AGPL-3.0。本仓库不打包其便携版。 |
| [NapCatQQ](https://github.com/NapNeko/NapCatQQ)，Mlikiowa / NapNeko 及贡献者 | QQ 协议端；`bot_assistant/native_runtime.py` 下载 v4.18.28 Shell，并从 v4.18.19 OneKey 包提取安装辅助工具；`bot_assistant/native.py`、`server/manager.py` 管理单号登录、二维码和状态。 | 使用者首次部署时从上游 Release 下载，或使用其已有部署。其 [LICENSE](https://github.com/NapNeko/NapCatQQ/blob/main/LICENSE) 是**自定义限制性许可**，含非商业使用和再分发条件；不能把它当成 MIT 或普通宽松开源许可。本仓库不打包 NapCat。 |
| [NapCat-Docker](https://github.com/NapNeko/NapCat-Docker)，NapNeko 及贡献者 | 本机 Docker / 远程 SSH 模式所用的 NapCat 镜像，镜像地址在 `server/manager.py` 固定。 | 使用者的 Docker 主机拉取镜像；镜像及其中软件的适用条款应以该镜像和 [NapCatQQ 许可](https://github.com/NapNeko/NapCatQQ/blob/main/LICENSE) 为准。本仓库不打包镜像。 |
| [OneBot v11](https://github.com/botuniverse/onebot-11)，BotUniverse 及贡献者 | AstrBot 与 NapCat 之间的接口规范；本项目生成 `onebot11_<QQ>.json` 并按该协议连接。 | 参考协议文档实现对接；本仓库没有复制其规范文本或实现源码。 |

`server/thinking_plugin/` 是对 AstrBot API 的独立插件实现，并非 AstrBot 官方插件。当前仓库没有复制 AstrBot 源码；如果未来将其与 AstrBot 源码合并发布、修改上游或以其他方式分发组合产物，应重新核对 AGPL-3.0 的对应义务及许可兼容性。本项目的 [PolyForm Noncommercial 许可](LICENSE)只覆盖有权由本项目授权的内容，不替代任何上游许可。

## 随 Windows EXE 一起分发的依赖

| 组件 | 用途 | 许可与随包文件 |
| --- | --- | --- |
| [Pillow](https://github.com/python-pillow/Pillow)，Secret Labs AB、Fredrik Lundh、Jeffrey A. Clark 及贡献者 | `bot_assistant/app.py`、`bot_assistant/avatars.py` 的图像处理；构建固定版本见 `requirements-build.txt`。 | [Pillow LICENSE](https://github.com/python-pillow/Pillow/blob/main/LICENSE)，MIT-CMU 风格许可；Windows ZIP 的 `third_party_licenses/Pillow-LICENSE.txt` 从本次构建实际安装的包提取。 |
| [CPython](https://www.python.org/)，Python Software Foundation 及贡献者；含 Tkinter 使用的 [Tcl/Tk](https://www.tcl-lang.org/software/tcltk/license.html) | PyInstaller 将运行时与 Tk 图形界面所需组件封装进 EXE；具体 Python 版本取决于构建环境。 | [Python 许可](https://docs.python.org/3/license.html)及 Tcl/Tk 自有许可；Windows ZIP 的 `third_party_licenses/Python-LICENSE.txt` 和 `Tcl-Tk-LICENSE.txt` 从本次构建环境复制。 |
| [PyInstaller](https://pyinstaller.org/)，PyInstaller 开发者 | `scripts/build.ps1` 构建单文件 EXE，其中含 PyInstaller bootloader。 | [PyInstaller 许可说明](https://pyinstaller.org/en/stable/license.html)：GPL-2.0 附带打包应用的特殊例外，部分文件使用 Apache-2.0；生成的应用仍须遵守各依赖许可。 |

发布 ZIP 应包含上述许可文本及本文件。源代码仓库不提交虚拟环境或 PyInstaller 生成文件，构建脚本从实际构建环境提取文本，避免将旧版本的许可误配给新构建。

## 由使用者获取的其他程序和服务

- [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)：仅本机 Docker 模式需要；向导从 Docker 官方渠道获取安装程序，受其 [订阅与许可条款](https://docs.docker.com/subscription-billing/desktop-license/)约束。它不是本仓库的开源代码或发布附件。
- [7-Zip](https://www.7-zip.org/)，Igor Pavlov 及贡献者：免 Docker 模式会从上游 NapCat OneKey 包提取 `7z.exe`，在使用者电脑上解包 QQ 安装文件；本项目的 EXE 与发布 ZIP 不附带该工具。7-Zip 各组件的 LGPL、BSD 和 unRAR 限制见其[官方许可](https://www.7-zip.org/license.txt)。
- [腾讯 QQ](https://im.qq.com/)：免 Docker 模式由使用者安装官方客户端；`bot_assistant/native_runtime.py` 只处理获取与签名校验。QQ 是腾讯软件，不属于本项目或上述开源项目，用户须遵守其服务条款。
- QQ 头像：`bot_assistant/avatars.py` 按 QQ 号请求腾讯 `thirdqq.qlogo.cn` 头像接口，返回图片只在客户端显示和本机缓存；仓库与 EXE 不附带他人的 QQ 头像。
- 模型 API（例如 [DeepSeek](https://api-docs.deepseek.com/)）：由使用者自行选择和填写；本项目不提供模型服务，也不附带他人的 API Key。每个服务商的接口和使用条件独立适用。

`docs/media/support-lionwen.png` 是维护者从自己维护的 [DSH 挂件计费增强项目](https://github.com/Lionwen-1/dsh-whale-widget-billing-hud)复用的赞赏码，只用于 README 的自愿支持区，不是运行依赖；其中头像按原图保留，本项目的软件许可不另行授权该图片的独立商用。

本清单覆盖 `bot-assistant` 仓库和由它构建的发布包；使用者自己安装的 AstrBot 插件、Docker 镜像的传递依赖及其他独立项目，应分别查阅各自随附的许可。发现遗漏或归属错误，请按 [COPYRIGHT.md](COPYRIGHT.md) 提供具体文件与上游链接，以便补正。
