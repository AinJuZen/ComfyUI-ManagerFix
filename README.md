# ComfyUI-ManagerFix · Manager 镜像修复

> 由 @痒局长 制作 · [B 站](https://space.bilibili.com/322892)

> **Manager 打开一片空白、报 `Failed to get external model list`？
> 它想连的 `raw.githubusercontent.com` 你连不上。这个工具把它指到能连的镜像。**

---

## 症状

```
· ComfyUI-Manager 装好了，点开 Model Manager，空的
· Custom Nodes Manager 列表刷不出来
· 控制台里 "Failed to get external model list" / InvalidChannel
· 换了个网络、换了台机器，又不行了
```

**不是 Manager 坏了，是它默认去 `raw.githubusercontent.com` 取节点清单，那个域名在很多网络里连不上。**

---

## One-liner (EN)

ComfyUI-Manager fetches its node/model lists from `raw.githubusercontent.com`, which is
unreachable on some networks. This tool **diagnoses** which URLs are still unmirrored,
**fixes** them to point at a reachable mirror, and **verifies** by actually downloading
the list. Idempotent, with backups.

---

## 用法

```bash
# ① 先诊断：只看现状，一个字节都不改
python managerfix.py --check

# ② 修（幂等，可以反复跑）
python managerfix.py --fix

# ③ 只验网络：镜像到底能不能拉到清单
python managerfix.py --verify
```

**参数：**

```
--instance <路径>   指定 ComfyUI 目录；不给就自动找
--mirror <前缀>     换镜像，默认 https://ghfast.top/
```

**Windows 不想敲命令：双击 `检查.bat` 看现状，双击 `修复.bat` 修。**

---

## 它到底改什么

**`--check` 会逐项报，`--fix` 只改这四处：**

| # | 改哪 | 改成什么 |
|---|---|---|
| 1 | `user\__manager\channels.list` | 6 个频道地址前面加上镜像前缀 |
| 2 | `user\__manager\config.ini` | `channel_url` 指向镜像的 default |
| 3 | `custom_nodes\ComfyUI-Manager` 里 `.py` / `.template` 中写死的原始地址 | 加镜像前缀 |
| 4 | `glob\manager_downloader.py` | 让模型下载默认走 `hf-mirror.com`，不依赖环境变量 |

**每个被改的文件旁边会留一份 `<文件名>.bak_managerfix`，原样可回滚。**

---

## 三个设计上的取舍

**① 它改官方源码——这是有意的**

**Manager 把清单地址写死在代码里，没有配置项能覆盖它。不碰源码就没有解。**

**代价说清楚：Manager 升级后，这些改动会被覆盖，你得重跑一次。**

**② 所以 `--check` 是主角，`--fix` 是配角**

**它随时能告诉你「现在是什么状态、哪里不通」——
即使哪天 Manager 改了结构、`--fix` 修不了，`--check` 也会准确地告诉你「我修不了」，而不是静默失败。**

**③ `--verify` 是真去下清单**

**不是「我改完了」，是「改完真的拉到了 3015 KB」。**
**拉不到就给退出码 1，不假装成功。**

---

## 实测

**本机（Manager 3.42 / ComfyUI 0.44.2 / Windows 11）：**

```
--check     自动找到 E:\...\ComfyUI，6 项全部正常
--verify    custom-node-list.json  3015 KB  13.9s
            model-list.json         282 KB   2.8s
```

**模拟一台"没修过"的机器（造了假实例）：**

```
--check    4 项 FAIL：channels.list 2 处没走镜像 / channel_url 原始 /
           源码 1 个文件写死 / 下载器依赖环境变量
--fix      4 项全部修好 + 校验通过 + 4 个 .bak 备份
--fix 再跑  全部「已是镜像，没动」——幂等，没有叠成两层 ghfast.top
--check    6 项全部正常
```

---

## 已知限制（请先读）

```
① 只针对 custom_nodes 里装的 Manager 3.x
   用 pip 装的 Manager 4.x 多数情况不需要它（它读 api.comfy.org）

② 会改 Manager 源码
   Manager 升级 / 重装 / Update All 之后要重跑一次

③ 依赖镜像可用性
   镜像挂了就修不了。换一个：--mirror https://另一家/

④ 只改清单地址，不动模型文件
   模型下载走镜像这件事，改的是 Manager 的下载器默认值
```

---

## 免责声明

```
⚠️ 本工具会修改 ComfyUI-Manager 的配置文件与源码文件。
   每个被改的文件都会留 .bak_managerfix 备份，可以手动还原。

⚠️ 请先用 --check 看清它会改什么，再跑 --fix。

⚠️ 镜像服务由第三方提供，其可用性、速度、内容与本项目无关。

⚠️ 本工具与 ComfyUI / Comfy Org / ComfyUI-Manager 官方均无隶属关系。

⚠️ 使用本工具产生的任何后果由使用者自行承担。
```

---

## 署名

**由 @痒局长 制作。**

- GitHub：[AinJuZen](https://github.com/AinJuZen)
- B 站：[痒局长](https://space.bilibili.com/322892)

发现 Manager 新版本改了结构、`--fix` 修不了，请开 issue 并附上 `--check` 的输出。

---

## License

MIT — 见 [LICENSE](LICENSE)。
