# 平台统一发布

`deploy/` 是本仓库唯一的部署来源。`deploy/helm/hc-data-platform` 直接包含前端、API、
Worker、运行时配置和仅向前迁移钩子；其中没有组件子 Chart。各组件镜像仍保持独立，
但每个环境都使用同一份发布清单中记录的准确摘要。

验证自包含的 Chart：

```bash
helm lint deploy/helm/hc-data-platform -f deploy/helm/hc-data-platform/values-ci.yaml
helm template hc-data-platform deploy/helm/hc-data-platform \
  -f deploy/helm/hc-data-platform/values-ci.yaml
```

绝不能部署仓库默认配置中的全零摘要或 `example.invalid` 仓库地址。CI 会在推送全部三个镜像后
生成真实的发布清单。预发布和生产环境提升的是同一份清单，不会重新构建镜像。

回滚会恢复上一个完整的 Helm 修订版本。数据库迁移不会反向执行，因此每项模式变更都必须同时
兼容候选应用和此前已验证的平台版本。

本地开发也只有一个入口：

```bash
docker compose -f compose.dev.yaml up --build
```

根目录的 Compose 文件负责所有服务，并在 `http://127.0.0.1:8088` 暴露统一网关；
各组件目录不包含自己的 Compose 文件。
