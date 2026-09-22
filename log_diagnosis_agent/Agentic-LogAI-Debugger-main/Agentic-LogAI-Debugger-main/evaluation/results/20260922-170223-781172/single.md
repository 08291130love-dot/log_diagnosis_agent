### 诊断结论
用户 1001 创建订单失败，直接原因是 `OrderService.java:24` 对 `userClient.findById(userId)` 返回的 `null` 对象调用了 `getLevel()`，触发 `NullPointerException`。根因是业务代码未对远程用户服务返回空值做防御性校验，而日志 #2 已明确记录“user service returned empty profile userId=1001”。置信度：高。影响：该请求返回 HTTP 500，订单未落库。

### 证据链
1. **日志 #1（原始第 1 行）**：`create order request userId=1001 skuId=SKU-9`，确认入参正常，请求已进入 `order-service`。
2. **日志 #2（原始第 2 行）**：`user service returned empty profile userId=1001`，证明上游用户服务对该用户返回了空结果，这是触发异常的前置条件。
3. **日志 #3（原始第 3-6 行）**：`java.lang.NullPointerException: Cannot invoke "UserProfile.getLevel()" because "profile" is null at com.demo.OrderService.createOrder(OrderService.java:24)`，定位到具体故障代码行。
4. **源码 OrderService.java:21-24**：`UserProfile profile = userClient.findById(userId);` 之后直接使用 `profile.getLevel();`，无任何 null 判断，与异常堆栈完全吻合。
5. **日志 #4（原始第 7 行）**：`response status=500 userId=1001`，确认故障最终表现为接口 500。

### 根因分析
- **触发条件**：用户 1001 在用户服务中不存在或用户服务返回了 null/empty profile（日志 #2 已确认）。
- **直接原因**：`OrderService.java:24` 对 null 引用调用方法，抛出 NPE。
- **根本原因**：`createOrder` 方法缺少对外部依赖返回值的空值校验和对应的业务异常转换；将“用户不存在”这一业务语义错误地暴露为系统级 NPE。
- **候选解释排查**：不存在参数校验失败（`validateRequest` 已通过）、数据库写入异常或并发问题证据；所有证据一致指向空值未校验。

### 修复建议
**最小可行修复**：在 `OrderService.java:21` 获取 `profile` 后立即判空，若为 null 则抛出明确的业务异常（如 `UserNotFoundException` 或 `IllegalArgumentException`），避免 NPE 并让上层返回合适的 HTTP 状态码（如 400/404）。

**防御性改进**：
1. 在 `userClient.findById` 的契约文档或接口定义中明确是否允许返回 null；若不允许，应在 client 层封装异常而非透传 null。
2. 全局异常处理器中将 `UserNotFoundException` 映射为 404 或 400，避免业务异常继续以 500 暴露。
3. 补充单元测试覆盖“用户服务返回 null”场景。

**风险**：仅增加判空不会改变“用户不存在时无法下单”的业务结果，但会将错误类型从系统异常转为业务异常，需确认前端/调用方对新状态码的兼容。

### 修复代码
```java
// OrderService.java createOrder 方法，替换原第 21-24 行
UserProfile profile = userClient.findById(userId);
if (profile == null) {
    throw new IllegalArgumentException("user profile not found, userId=" + userId);
}
String level = profile.getLevel();
```
> 注：以上代码基于已查看的 `OrderService.java:18-27` 结构给出，异常类型可根据项目规范替换为自定义 `UserNotFoundException`。未修改任何文件，仅作示意。

### 验证方案
1. **复现测试**：使用 userId=1001（或 mock userClient 返回 null）调用创建订单接口，预期返回 400/404 及明确错误信息，不再出现 500 和 NPE 日志。
2. **正向回归**：使用存在的用户 ID 调用接口，预期订单正常创建，日志中无新增 ERROR。
3. **日志观察**：部署后搜索 `create order failed` + `NullPointerException`，预期该组合不再出现；搜索 `user profile not found` 应能命中新的业务异常日志。
4. **单元测试**：新增用例 `createOrder_whenProfileNull_shouldThrowBusinessException`，断言抛出预期异常且不调用 `orderRepository.save`。

### 尚未确认
- 用户 1001 在用户服务中是否确实不存在，还是用户服务本身异常导致误返 null（需查 user-service 日志或确认其接口契约）。
- 项目中是否已有统一的“用户不存在”业务异常类及对应的全局异常处理映射。
- 调用方对非 500 错误码的兼容情况。