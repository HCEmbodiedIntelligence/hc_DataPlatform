# 通过平台上传 Unitree G1 LeRobot 数据

推荐使用平台上传脚本，而不是让机器持有 OSS AccessKey 后直接写 Bucket。脚本执行的流程是：

```text
本地 LeRobot（Parquet + MP4）
  -> 本地转换为平台标准 Episode（MCAP + Manifest）
  -> 向平台创建上传会话
  -> 使用平台签发的 URL 将文件正文直传 OSS
  -> 向平台提交完成
  -> 自动质检、对齐、可视化和标注
```

文件正文不会经过平台 API 服务器；平台只负责身份、项目归属、Object Key、临时上传授权和
完成登记。这与网页上传使用同一套控制面协议，因此不需要 Bucket 扫描。

## 交互运行

在仓库根目录执行：

```bash
backend/.venv/bin/python scripts/upload_lerobot_via_platform.py
```

依次输入：

1. 本地 LeRobot 文件夹，例如
   `E:\HC-Unitree-G1-Conversion-Cache\unitreerobotics--G1_WBT_Dex1_Put_Clothes_into_Washing_Machine`；
2. 平台地址，本机开发环境通常直接回车使用 `http://127.0.0.1:8000`；
3. 平台用户名和密码。

当前数据的参数已写入脚本：

```text
Project: be22-hf-g1-video-20260819-02-p1
Region:  be22-hf-g1-video-20260819-02-cn
Task:    14d16ba1-d95a-5ee3-aaa7-7b7d78091b52
Robot:   robot-d1a17126-b495-59b8-bf48-0ccce0a6ffe7
Episode: 0-9
```

也可以非交互执行。Token 通过环境变量传入，避免出现在命令行和进程列表：

```bash
export HC_DATA_ACCESS_TOKEN='平台 Bearer Token'

backend/.venv/bin/python scripts/upload_lerobot_via_platform.py \
  --source-dir 'E:\HC-Unitree-G1-Conversion-Cache\unitreerobotics--G1_WBT_Dex1_Put_Clothes_into_Washing_Machine' \
  --api-base-url http://127.0.0.1:8000 \
  --episode-count 10
```

Windows 路径会在 WSL 中自动转换成对应的 `/mnt/e/...`。转换结果默认缓存到
`artifacts/hf-unitree-g1-mcap`；重复运行会校验并复用已经完成的 Episode，平台上传会话
也使用稳定幂等键，不会重复收录同一条数据。

如果平台启用了登录人机验证，命令行用户名/密码登录可能被拒绝；此时先在受信任的服务账号
流程中获取 Token，再使用 `HC_DATA_ACCESS_TOKEN`。
