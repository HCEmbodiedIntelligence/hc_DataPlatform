import { isDomainError } from "../../shared/api/domain-error";

export type AuthOperation = "login" | "register" | "bootstrap" | "logout";

const genericByOperation: Readonly<Record<AuthOperation, string>> = {
  login: "暂时无法登录。请稍后重试。",
  register: "暂时无法创建账户。请稍后重试。",
  bootstrap: "暂时无法确认账户状态。请稍后重新登录。",
  logout: "暂时无法安全退出。请稍后重试。",
};

export function authErrorMessage(
  error: unknown,
  operation: AuthOperation,
): string {
  if (!isDomainError(error)) return genericByOperation[operation];
  if (error.httpStatus === 429) {
    return "尝试次数过多。请稍后再试，平台不会显示账户是否存在。";
  }
  if (error.httpStatus === 401) {
    return operation === "bootstrap"
      ? "登录状态已失效。请重新登录。"
      : "用户名或密码不正确。请检查后重试。";
  }
  if (error.httpStatus === 422) {
    return operation === "register"
      ? "注册信息未通过校验。请检查用户名、密码和确认密码。"
      : "登录信息未通过校验。请检查用户名和密码。";
  }
  if (error.httpStatus === 409 && operation === "register") {
    return "暂时无法完成注册。请调整注册信息或稍后重试。";
  }
  if (error.code === "CONTRACT_MISMATCH") {
    return "服务响应与当前认证合同不一致。页面没有继续处理，请联系平台管理员。";
  }
  if (error.code === "NETWORK_ERROR") {
    return "网络连接失败。请检查连接后重试。";
  }
  return genericByOperation[operation];
}
