import { Alert, Button, Form, Input, Modal, Select, Spin, Steps } from "antd";
import {
  CheckCircle2,
  File as FileIcon,
  FolderOpen,
  UploadCloud,
  X,
} from "lucide-react";
import { RobotSceneCore } from "../../viewer";
import { StatusTag } from "../../../shared/ui";
import workspace from "../../../shared/ui/workspace.module.css";
import type { ModelMetadataFormValues } from "./contracts";
import type { RobotModelImportController } from "./use-robot-model-import";
import {
  EMPTY_MAPPING,
  importStep,
  modelAssetRoleLabels,
  modelFileAccept,
} from "./presentation";
import uploadStyles from "./model-upload.module.css";
import styles from "./import-dialog.module.css";

export function RobotModelImportDialog({
  controller,
}: {
  readonly controller: RobotModelImportController;
}) {
  const {
    importOpen,
    importStage,
    importSourceVersion,
    modelForm,
    modelMetadata,
    modelAssets,
    modelAssetCounts,
    analysis,
    mappings,
    mappingsValid,
    localModelRef,
    localRuntimeLoader,
    configurationAsset,
    configurationPreview,
    modelDropActive,
    readingDroppedFiles,
    loadingExistingModel,
    parsingModel,
    modelNotice,
    modelError,
    savedVersionId,
    modelSavePending,
    modelSelectionError,
    modelFileInputRef,
    modelFolderInputRef,
    setImportOpen,
    setModelDropActive,
    setModelAssets,
    setMappings,
    setModelError,
    clearAnalysis,
    resetImport,
    cancelImport,
    addModelFilesFromPicker,
    handleModelFilesDrop,
    reviewSelectedFiles,
    saveRobotModel,
  } = controller;
  return (
    <Modal
      open={importOpen}
      width={1040}
      title={importSourceVersion ? "检查并修改机器人模型" : "导入机器人模型"}
      footer={null}
      destroyOnHidden
      mask={{ closable: !modelSavePending }}
      onCancel={() => void cancelImport()}
    >
      <Steps
        className={styles.wizardSteps}
        current={importStep(importStage)}
        items={[
          { title: "导入文件", content: "URDF + 描述配置" },
          { title: "解析与预览", content: "检查结构和映射" },
          { title: "保存", content: "校验并发布" },
        ]}
      />

      {loadingExistingModel ? (
        <div className={styles.savingState} role="status">
          <div className={styles.savingCopy}>
            <Spin size="large" />
            <h3>正在读取当前模型文件…</h3>
            <p>文件读取完成后会自动解析，并进入可视化检查。</p>
          </div>
        </div>
      ) : importStage === "files" ? (
        <div className={styles.wizardSection}>
          <Alert
            type="info"
            showIcon
            title="先导入，解析成功后才能预览和保存"
            description="必须包含一个入口 URDF。描述配置使用 JSON；如果没有提供，系统会根据 URDF 自动生成 robot.config.json。"
          />
          <Form<ModelMetadataFormValues>
            form={modelForm}
            layout="vertical"
            requiredMark="optional"
          >
            {!importSourceVersion ? (
              <div className={styles.wizardMeta}>
                <Form.Item
                  name="manufacturer"
                  label="制造商"
                  rules={[{ required: true, whitespace: true, max: 256 }]}
                >
                  <Input autoComplete="organization" maxLength={256} />
                </Form.Item>
                <Form.Item
                  name="modelCode"
                  label="型号代码"
                  rules={[{ required: true, whitespace: true, max: 128 }]}
                >
                  <Input autoComplete="off" maxLength={128} />
                </Form.Item>
                <Form.Item
                  name="displayName"
                  label="模型名称"
                  rules={[{ required: true, whitespace: true, max: 256 }]}
                >
                  <Input autoComplete="off" maxLength={256} />
                </Form.Item>
              </div>
            ) : null}
            <Form.Item
              name="versionLabel"
              label="保存版本"
              rules={[{ required: true, whitespace: true, max: 128 }]}
            >
              <Input autoComplete="off" maxLength={128} />
            </Form.Item>
          </Form>
          <div
            className={`${uploadStyles.dropZone} ${modelDropActive ? uploadStyles.dropZoneActive : ""}`}
            role="button"
            tabIndex={0}
            aria-label="拖拽或选择 URDF 和机器人配置文件"
            aria-busy={readingDroppedFiles}
            onClick={(event) => {
              const target = event.target;
              if (target instanceof Element && target.closest("button, input"))
                return;
              modelFileInputRef.current?.click();
            }}
            onKeyDown={(event) => {
              if (event.target !== event.currentTarget) return;
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                modelFileInputRef.current?.click();
              }
            }}
            onDragEnter={(event) => {
              event.preventDefault();
              setModelDropActive(true);
            }}
            onDragOver={(event) => {
              event.preventDefault();
              event.dataTransfer.dropEffect = "copy";
              setModelDropActive(true);
            }}
            onDragLeave={(event) => {
              if (
                !event.currentTarget.contains(
                  event.relatedTarget as Node | null,
                )
              ) {
                setModelDropActive(false);
              }
            }}
            onDrop={(event) => {
              event.preventDefault();
              event.stopPropagation();
              void handleModelFilesDrop(event.dataTransfer);
            }}
          >
            <div className={uploadStyles.dropZoneContent}>
              <span className={uploadStyles.dropZoneIcon}>
                <UploadCloud aria-hidden="true" size={24} />
              </span>
              <strong>
                {readingDroppedFiles
                  ? "正在读取文件夹…"
                  : "拖拽 URDF、配置文件或整个模型文件夹"}
              </strong>
              <span className={uploadStyles.dropZoneCopy}>
                选择新的 URDF 或 JSON
                配置时会替换同类型旧文件；网格和纹理会保留目录结构。
              </span>
              <div className={uploadStyles.uploadActions}>
                <Button
                  icon={<FileIcon aria-hidden="true" size={16} />}
                  disabled={readingDroppedFiles}
                  onClick={(event) => {
                    event.stopPropagation();
                    modelFileInputRef.current?.click();
                  }}
                >
                  选择文件
                </Button>
                <Button
                  icon={<FolderOpen aria-hidden="true" size={16} />}
                  disabled={readingDroppedFiles}
                  onClick={(event) => {
                    event.stopPropagation();
                    modelFolderInputRef.current?.click();
                  }}
                >
                  选择文件夹
                </Button>
              </div>
            </div>
            <input
              ref={modelFileInputRef}
              className={uploadStyles.hiddenInput}
              aria-label="选择机器人模型文件"
              type="file"
              multiple
              accept={modelFileAccept}
              onChange={(event) => {
                addModelFilesFromPicker(event.target.files);
                event.target.value = "";
              }}
            />
            <input
              ref={(element) => {
                modelFolderInputRef.current = element;
                element?.setAttribute("webkitdirectory", "");
                element?.setAttribute("directory", "");
              }}
              className={uploadStyles.hiddenInput}
              aria-label="选择机器人模型文件夹"
              type="file"
              multiple
              accept={modelFileAccept}
              onChange={(event) => {
                addModelFilesFromPicker(event.target.files);
                event.target.value = "";
              }}
            />
          </div>

          {modelAssets.length ? (
            <>
              <div className={uploadStyles.selectionSummary} aria-live="polite">
                <strong>已选择 {modelAssets.length} 个文件</strong>
                <span>URDF {modelAssetCounts.URDF}</span>
                <span>网格 {modelAssetCounts.MESH}</span>
                <span>纹理 {modelAssetCounts.TEXTURE}</span>
                <span>配置 {modelAssetCounts.CONFIG}</span>
              </div>
              <ul className={uploadStyles.fileList} aria-label="待解析模型文件">
                {modelAssets.map((asset) => (
                  <li className={uploadStyles.fileRow} key={asset.relativePath}>
                    <span
                      className={uploadStyles.filePath}
                      title={asset.relativePath}
                    >
                      {asset.relativePath}
                    </span>
                    <span className={uploadStyles.fileRole}>
                      {modelAssetRoleLabels[asset.role]}
                    </span>
                    <button
                      className={uploadStyles.removeButton}
                      type="button"
                      aria-label={`移除 ${asset.relativePath}`}
                      onClick={() => {
                        setModelAssets((current) =>
                          current.filter(
                            (item) => item.relativePath !== asset.relativePath,
                          ),
                        );
                        clearAnalysis();
                        setModelError(null);
                      }}
                    >
                      <X aria-hidden="true" size={15} />
                    </button>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {modelNotice ? (
            <Alert type="warning" showIcon title={modelNotice} />
          ) : null}
          {modelError ? (
            <Alert type="error" showIcon title={modelError} />
          ) : null}
          <div className={styles.wizardFooter}>
            <Button onClick={() => void cancelImport()}>取消</Button>
            <Button
              type="primary"
              loading={parsingModel}
              disabled={!modelAssets.length || Boolean(modelSelectionError)}
              onClick={() => void reviewSelectedFiles()}
            >
              解析文件并预览
            </Button>
          </div>
        </div>
      ) : importStage === "review" && analysis && modelMetadata ? (
        <div className={styles.wizardSection}>
          <div className={styles.wizardMeta}>
            <div>
              <span>入口 URDF</span>
              <strong title={analysis.urdfPath}>{analysis.urdfPath}</strong>
            </div>
            <div>
              <span>URDF 模型名称</span>
              <strong>{analysis.robotName}</strong>
            </div>
            <div>
              <span>描述配置</span>
              <strong>{configurationAsset?.relativePath}</strong>
            </div>
            <div>
              <span>Link</span>
              <strong>{analysis.linkNames.length}</strong>
            </div>
            <div>
              <span>Joint</span>
              <strong>{analysis.joints.length}</strong>
            </div>
            <div>
              <span>活动关节</span>
              <strong>{analysis.actuatedJointNames.length}</strong>
            </div>
          </div>

          {analysis.missingMeshReferences.length ? (
            <Alert
              type="warning"
              showIcon
              title="URDF 引用的部分资源未找到"
              description={analysis.missingMeshReferences.join("、")}
            />
          ) : (
            <Alert
              type="success"
              showIcon
              title="URDF 结构解析通过"
              description="请旋转、缩放 3D 模型检查方向和结构，再确认关节映射。"
            />
          )}

          <div className={styles.reviewGrid}>
            <div className={styles.wizardPreview}>
              {localRuntimeLoader ? (
                <RobotSceneCore
                  modelRef={localModelRef}
                  jointMapping={EMPTY_MAPPING}
                  runtimeLoader={localRuntimeLoader}
                />
              ) : (
                <div className={styles.emptyPreview}>3D 预览正在准备…</div>
              )}
            </div>
            <section className={styles.mappingPanel} aria-label="关节映射配置">
              <header className={styles.mappingHeader}>
                <div>
                  <h3>关节映射</h3>
                  <p>修改后会直接写入 {configurationAsset?.relativePath}</p>
                </div>
                <StatusTag
                  status={mappingsValid ? "VALID" : "INVALID"}
                  label={mappingsValid ? "完整" : "需修正"}
                  tone={mappingsValid ? "success" : "warning"}
                />
              </header>
              {mappings.length ? (
                <ul className={styles.mappingList}>
                  {mappings.map((mapping, index) => (
                    <li
                      className={styles.mappingRow}
                      key={`${mapping.target_joint_name}-${index}`}
                    >
                      <Input
                        aria-label={`${mapping.target_joint_name} 的数据关节名`}
                        value={mapping.source_joint_name}
                        placeholder="数据关节名"
                        onChange={(event) =>
                          setMappings((current) =>
                            current.map((item, itemIndex) =>
                              itemIndex === index
                                ? {
                                    ...item,
                                    source_joint_name: event.target.value,
                                  }
                                : item,
                            ),
                          )
                        }
                      />
                      <span className={styles.mappingArrow} aria-hidden="true">
                        →
                      </span>
                      <Select
                        aria-label={`${mapping.source_joint_name} 对应的 URDF 关节`}
                        value={mapping.target_joint_name}
                        options={analysis.actuatedJointNames.map(
                          (jointName) => ({
                            value: jointName,
                            label: jointName,
                          }),
                        )}
                        onChange={(targetJointName) =>
                          setMappings((current) =>
                            current.map((item, itemIndex) =>
                              itemIndex === index
                                ? {
                                    ...item,
                                    target_joint_name: targetJointName,
                                  }
                                : item,
                            ),
                          )
                        }
                      />
                      <Select
                        aria-label={`${mapping.source_joint_name} 的方向`}
                        value={mapping.direction}
                        options={[
                          { value: "SAME", label: "同向" },
                          { value: "INVERTED", label: "反向" },
                        ]}
                        onChange={(direction: "SAME" | "INVERTED") =>
                          setMappings((current) =>
                            current.map((item, itemIndex) =>
                              itemIndex === index
                                ? { ...item, direction }
                                : item,
                            ),
                          )
                        }
                      />
                    </li>
                  ))}
                </ul>
              ) : (
                <p className={workspace.safeNote}>该 URDF 没有活动关节。</p>
              )}
              <details>
                <summary>查看将要保存的描述配置</summary>
                <pre className={styles.configurationPreview}>
                  {configurationPreview}
                </pre>
              </details>
            </section>
          </div>
          {modelError ? (
            <Alert type="error" showIcon title={modelError} />
          ) : null}
          <div className={styles.wizardFooter}>
            <Button onClick={() => clearAnalysis()}>返回重新选择</Button>
            <Button
              type="primary"
              disabled={
                !mappingsValid || Boolean(analysis.missingMeshReferences.length)
              }
              onClick={() => void saveRobotModel()}
            >
              保存机器人模型
            </Button>
          </div>
        </div>
      ) : importStage === "saving" ? (
        <div className={styles.savingState} role="status">
          <div className={styles.savingCopy}>
            <Spin size="large" />
            <h3>正在保存机器人模型…</h3>
            <p>
              正在写入 URDF、描述配置和关节映射，并执行服务端校验与版本发布。
            </p>
          </div>
        </div>
      ) : (
        <div className={styles.savingState} role="status">
          <div className={styles.savingCopy}>
            <CheckCircle2 aria-hidden="true" color="#389e0d" size={52} />
            <h3>机器人模型已保存</h3>
            <p>
              URDF、描述配置和关节映射已通过校验，模型版本已经发布。
              {savedVersionId ? `版本：${savedVersionId}` : ""}
            </p>
            <Button
              type="primary"
              onClick={() => {
                setImportOpen(false);
                resetImport();
              }}
            >
              完成
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
