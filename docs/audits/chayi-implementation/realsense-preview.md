# RealSense 官方深度显示：阶段实现与证据

本次实现位于 `feat/chayi-g1`，只更新 `hc-chayi-g1` 隔离服务。它是 W2/W4/W5
的部分实现，不代表三条上传入口、真实录制或 17 项 P0 已完成验收。

## 显示决策

按用户要求，深度 PNG 和视频预览直接调用 `pyrealsense2.colorizer`，不再保留
自定义调色板或自定义回退实现。固定 SDK `2.58.4.10922`，使用官方默认的
Dynamic、Jet、开启直方图均衡。代码只通过 SDK 的 software-device API 输入
内存帧，不访问相机或机器人。

依据：[官方 colorizer 实现](https://github.com/realsenseai/librealsense/blob/master/src/proc/colorizer.cpp)、
[官方 NumPy 帧输入示例](https://github.com/realsenseai/librealsense/blob/master/wrappers/python/examples/numpy_to_frame.py)、
[SDK 发行包](https://pypi.org/project/pyrealsense2/2.58.4.10922/)。
测试同时校验已安装 SDK 的实际默认选项，防止将文档默认值误当成运行时默认值。

深度显示链路：原灰度 HEVC → 按原 profile 反量化为米 → 应用 HC 有效掩码 →
构造 SDK 所需的临时 Z16 帧 → 官方 colorizer → PNG 或网页 H.264 预览。
临时 Z16 的单位为 `depth_max / 65535` 米，仅存在内存中，不保存新的 16 位
主副本。无效像素输入为 0。动态着色使用每帧直方图，颜色不是固定距离刻度；
界面明确提示测量应读取物理值。逐像素一致性指 SDK 输出和无损 PNG，网页
H.264 显示副本经过有损压缩，不声称它逐像素无损。

灰度 HEVC 主文件、12 位量化 profile、源单位、掩码和完整包下载保持原契约。
显示用的 Z16 转换和 H.264 压缩不计作主深度视频的编码无损证据，也不替代
原量化误差报告。完整包仍不依赖预览文件才能读取。

## 上传与预览行为

- 包内 RGB H.265 保留原字节，另生成可供网页播放的 H.264/yuv420p 副本。
  已是 H.264/yuv420p 的 RGB 直接授权原文件。深度灰度 HEVC 保留原字节，
  着色预览是独立对象，不重做主深度量化。
- 预览 profile 为 `rs-2.58.4-dynamic-jet-h264-v1`，记录 SDK、显示选项、源视频
  哈希、完整数据版本引用和预览哈希。改变显示实现需要新 profile，不覆盖
  已 READY 的预览。主包 profile 与显示 profile 分别管理。
- 处理服务发布并核验预览之后才提交 READY；授权请求只读，缺预览返回 425、
  已失败返回 409，不在打开页面时触发编码。中断后的重试可恢复，READY 重试
  不编码。对象上传成功、数据库提交之前失败时可能重做预览编码，但不会修改
  主包或重复量化主深度。
- 授权固定到租户、项目、地域、数据集、Episode、数据版本、特征和哈希。
  每次初次授权或续签均重新检查权限并写审计。签名 URL 有效期为 5 分钟；
  撤权会拒绝新授权及续签，已签出的 URL 在到期前仍可使用，不宣称即时吊销。
- P06 使用新格式绑定的 RGB/深度预览源，历史媒体路径继续保留。
  预览状态的完整独立卡片、统一详情、像素取值交互仍需后续实现。

## 已运行的验证

使用隔离 PostgreSQL、MinIO、API/gateway/object-edge；输入是明确标记的
4 帧 16×16 合成样本，不是实际采集或上传/Temporal 全链路证据。

1. `tests/episode_store`：28 passed；`episode-preview-tests.xml`。
   包含 SDK 默认 colorizer 的逐像素对照、HC 无效像素/全无效帧、PNG、
   实际预览编码、真实网关登录授权和签名视频 GET/Range、帧数/时间、
   H.264 RGB 复用/H.265 RGB 副本、原包每个文件哈希不变、中断/并发/重试、
   丢失提交响应、跨租户/地域拒绝、热缓存撤权及物理值读取。
2. 深度契约、运行时路由授权、对象存储端点配置：41 passed；
   `episode-preview-regression-tests.xml`。一个已有 Starlette/httpx 弃用警告。
3. 前端媒体源、数值源、viewer adapter、授权媒体 runtime、Workbench：
   58 passed；`episode-preview-frontend-tests.xml`。媒体源测试使用真实作用域
   HTTP 客户端和运行时操作许可表，但响应为 fetch stub，不算浏览器验收。
4. `tsc -b --pretty false`、生成 API 的 `--check`、变更模块 Ruff 及
   `git diff --check` 通过。运行时 OpenAPI SHA-256：
   `cb950d483267426633c2aeba893ca0a43a85105a435bbb160a0366ce7b35893c`。

## 运行环境与复现

`backend/pyproject.toml` 的 data extra 和 `uv.lock` 固定 SDK 版本。当前隔离
基础镜像已有项目依赖，用 `deploy/chayi/backend-preview.Dockerfile` 追加这个
官方 wheel，不升级其它依赖。cp312/Linux x86_64 wheel SHA-256 为：

`1e83454cbaf9de50962d78ce3addb0b6a6d8d02259c1f0c64d4a0d527a02bf73`

从执行工作树根目录可重建（网络下载 wheel 需相应网络权限）：

```bash
python3 -m pip download --only-binary=:all: --no-deps --platform manylinux1_x86_64 --implementation cp --python-version 312 --abi cp312 --dest backend/.g1-artifacts/realsense-wheels pyrealsense2==2.58.4.10922
bash deploy/chayi/compose.sh build test
bash deploy/chayi/compose.sh up -d --no-deps --wait --wait-timeout 30 api worker
bash deploy/chayi/compose.sh run --rm --no-deps test tests/episode_store --fail-on-skipped --fail-on-xfailed -q
```

本轮镜像为 `hc-chayi-backend:realsense-2.58.4`，构建清单摘要：
`sha256:24146e1fb725e74100a4a726c3a643a74e0ebc24fd0960ad28c7a123980d9e9e`。
沿用基础镜像运行用户 `65532:65532`。最初使用 1000 导致 API/Worker 对原
缓存目录无写权限；修正用户并重建后服务健康，随后重跑全部上述后端测试。
测试服务单独以 1000 使用测试目录。不要将此临时基础镜像标签当作最终 G2
可重复构建方案；最终候选应从锁文件构建所有依赖并固定基础镜像摘要。

`episode_store/0005_media_previews.sql` 仅在隔离数据库应用，包含预览状态、
不可变 READY 收据和项目范围 RLS。现网 8088、旧存储及机器人未操作。
未来 G2 需要在候选镜像/API/Worker 都具备 SDK 后按迁移清单应用扩展迁移；
预览版本为新增对象，回退应用时保留这些对象和迁移，不删除已有主包。
这里只记录后续部署所需条件，没有执行 G2/G3。

## 尚未验收

真实录制的尺度/标定/时间映射；三条入口实际切换到新主存储；长视频的
资源与性能；1440/1024 浏览器流程及真人审核；版本标注、三类审核和批量
交付等其余 G1 工作仍待完成。SDK 着色与数据可读不代表任何未指定模型已
支持深度训练。17 项 P0 均保持待验收。
