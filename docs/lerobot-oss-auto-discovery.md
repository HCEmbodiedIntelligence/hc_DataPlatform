# LeRobot 上传 OSS 后自动解析

> 该方案现在作为“机器必须自行写 OSS”时的兼容/补偿方案。机器能够访问平台 API 时，优先
> 使用 [平台上传脚本](lerobot-platform-upload.md)，它与网页上传共享正式上传会话，平台无需
> 扫描 Bucket。

目标链路是：

```text
原始 LeRobot 文件夹
  -> lerobot-inbox/（OSS）
  -> OSS 自动发现器
  -> 每个 Episode 的平台标准 MCAP
  -> 校验 / 质检 / 对齐 / Lance
  -> 数据可视化与标注任务
```

平台不会把 Bucket 中任意对象都当成业务数据。自动发现器只扫描明确配置的
`lerobot-inbox/`，并且只接收完整的 Unitree G1 LeRobot v3 根目录。这样不会把临时文件、
其他项目数据或上传一半的目录误导入。

## 1. 上传原始文件夹

在仓库根目录运行：

```bash
backend/.venv/bin/python scripts/test_aliyun_oss_upload.py
```

脚本要求输入 AccessKey ID、AccessKey Secret 和本地路径。本地路径可以直接粘贴 Windows
路径，例如：

```text
E:\HC-Unitree-G1-Conversion-Cache\unitreerobotics--G1_WBT_Dex1_Put_Clothes_into_Washing_Machine
```

OSS 保存路径留空时使用：

```text
lerobot-inbox/unitreerobotics--G1_WBT_Dex1_Put_Clothes_into_Washing_Machine
```

脚本递归上传正式文件，忽略 `.cache`、`.part`、`.lock`、`.incomplete`，并保持原来的
Parquet/MP4 目录结构。每个 revision 的 `meta/info.json` 会自动最后上传；不用手动选择或
再上传一次。它在 OSS 中的完整位置类似：

```text
oss://humanoid-robot-embodied-operation/
  lerobot-inbox/
  unitreerobotics--G1_WBT_Dex1_Put_Clothes_into_Washing_Machine/
  6d698e2641cc4bb765cd738835fe3a4ecc0fe2c7/
  meta/info.json
```

这里的换行只是展示，真实 Object Key 是一整行、使用 `/` 分隔。

## 2. 启动自动发现器

自动发现器使用服务器侧 OSS 凭据读取 inbox，并用平台 Bearer Token 调用正式上传接口。
`HC_LEROBOT_DISCOVERY_PLATFORM_REGION_CODE` 是平台项目的 Region Code，不是 OSS 的
`cn-beijing`。

当前项目的开发环境可配置：

```bash
export HC_OBJECT_STORE_ENDPOINT='https://oss-cn-beijing.aliyuncs.com'
export HC_OBJECT_STORE_BUCKET='humanoid-robot-embodied-operation'
export HC_OBJECT_STORE_ACCESS_KEY='填写 AccessKey ID'
export HC_OBJECT_STORE_SECRET_KEY='填写 AccessKey Secret'
export HC_DATA_ACCESS_TOKEN='填写有当前项目写权限的 Bearer Token'
export HC_LEROBOT_DISCOVERY_PREFIX='lerobot-inbox'
export HC_LEROBOT_DISCOVERY_EPISODE_COUNT='10'
export HC_LEROBOT_DISCOVERY_PLATFORM_REGION_CODE='be22-hf-g1-video-20260819-02-cn'
```

随后启动带自动发现 profile 的开发栈：

```bash
docker compose --profile lerobot-discovery -f compose.dev.yaml up -d
```

也可以在已经运行 API/worker 的主机上单独执行：

```bash
PYTHONPATH=backend/src backend/.venv/bin/python \
  -m hc_data_platform.tools.lerobot_oss_discovery \
  --source-prefix lerobot-inbox \
  --project-id be22-hf-g1-video-20260819-02-p1 \
  --collection-task-id 14d16ba1-d95a-5ee3-aaa7-7b7d78091b52 \
  --robot-id robot-d1a17126-b495-59b8-bf48-0ccce0a6ffe7 \
  --episode-count 10 \
  --api-base-url http://127.0.0.1:8000 \
  --region-code be22-hf-g1-video-20260819-02-cn
```

发现器每 30 秒扫描一次。一个根目录的正式对象列表连续稳定 60 秒后才开始处理。上传或
转换中断可直接重试；源签名、输出包 ID 和平台上传幂等键都是稳定的，不会因为重复扫描
产生第二套 Episode。

## 3. 解析与可视化语义

这批模拟数据保持 LeRobot 原格式：Parquet 存关节、动作和时间戳，MP4 存四路相机。
发现器不会覆盖或改写这些 OSS Raw 对象。平台转换阶段读取 Parquet，并从 MP4 解码所需
帧，生成平台当前处理链能够验证的 MCAP；随后沿既有工作流生成对齐数据和可视化媒体，
再投影为可标注的数据集。

未来真实机器人原生采集不要求先生成 Parquet。推荐直接写传感器 MCAP 和相机 MP4，
平台侧再按需生成 Lance/Parquet 等训练或分析派生数据。
