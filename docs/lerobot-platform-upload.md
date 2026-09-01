# 通过平台上传 Unitree G1 LeRobot 数据

推荐使用平台上传脚本，而不是让机器持有 OSS AccessKey 后直接写 Bucket。脚本执行的流程是：

```text
本地 LeRobot（Parquet + MP4）
  -> 向平台创建原生 LeRobot Raw 上传会话
  -> 使用平台签发的 URL 将文件正文直传 OSS
  -> 向平台提交完成
  -> 平台登记 Raw Source、Episode 和处理任务
  -> 自动质检、对齐、可视化和标注
```

文件正文不会经过平台 API 服务器；平台只负责身份、项目归属、Object Key、临时上传授权和
完成登记。这与网页上传使用同一套控制面协议，因此不需要 Bucket 扫描。

单次原生 LeRobot 导入最多包含 10,000 个源文件和 10,000 个 episode；网页、本地上传
脚本和平台 API 使用同一上限。

机器人端不得配置 OSS AccessKey，也不得自行拼接 Object Key。所有机器人上传必须先向平台
创建上传会话，并只使用平台签发的短期、对象级上传 URL。原始文件由平台放入以下正式路径，
同时保持 LeRobot 目录内的相对路径：

```text
raw/{organization_id}/{dataset_id}/{import_id}/source/{原始相对路径}
raw/{organization_id}/{dataset_id}/{import_id}/manifest.json
derived/lerobot-imports/{organization_id}/{dataset_id}/{import_id}/plan.json
```

项目不再提供 `lerobot-inbox`、Bucket 自动发现、机器人 OSS AccessKey 或 OSS 源目录导入入口。

## 交互运行

在仓库根目录执行：

```bash
backend/.venv/bin/python scripts/upload_lerobot_via_platform.py
```

依次输入：

1. 本地 LeRobot 文件夹，例如
   `E:\HC-Unitree-G1-Conversion-Cache\unitreerobotics--G1_WBT_Dex1_Put_Clothes_into_Washing_Machine`；
2. 平台地址，本机开发环境通常直接回车使用 `http://127.0.0.1:8000`；
3. Organization ID；
4. 平台用户名和密码。

当前数据的参数已写入脚本：

```text
Project: be22-hf-g1-video-20260819-02-p1
Region:  be22-hf-g1-video-20260819-02-cn
Dataset: be22-hf-g1-video-20260819-02-p1
Task:    14d16ba1-d95a-5ee3-aaa7-7b7d78091b52
Robot:   robot-d1a17126-b495-59b8-bf48-0ccce0a6ffe7
Episode: 从 meta/info.json 自动读取
```

也可以非交互执行。Token 通过环境变量传入，避免出现在命令行和进程列表：

```bash
export HC_DATA_ACCESS_TOKEN='平台 Bearer Token'
export HC_ORGANIZATION_ID='所属 Organization ID'

backend/.venv/bin/python scripts/upload_lerobot_via_platform.py \
  --source-dir 'E:\HC-Unitree-G1-Conversion-Cache\unitreerobotics--G1_WBT_Dex1_Put_Clothes_into_Washing_Machine' \
  --api-base-url http://127.0.0.1:8000
```

Windows 路径会在 WSL 中自动转换成对应的 `/mnt/e/...`。上传会保留原始 Parquet、MP4 和
元数据文件，不在机器人端生成 MCAP。

如果平台启用了登录人机验证，命令行用户名/密码登录可能被拒绝；此时先在受信任的服务账号
流程中获取 Token，再使用 `HC_DATA_ACCESS_TOKEN`。
