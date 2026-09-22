### 诊断结论

用户 1001 创建订单失败，直接原因是 `OrderService.createOrder` 在 `OrderService.java:24` 对 `userClient.findById(1001)` 返回的 `null` 调用了 `getLevel()`，触发 `NullPointerException`。根因是业务代码未对远程/客户端返回的空值做防御性校验，而当前 `UserClient` 测试桩对用户 1001 显式返回 `null`（`UserClient.java:10-11`）。置信度高，影响为该用户本次下单请求返回 HTTP 500，订单未落库。

### 证据链

1. **日志 #1**（原始第 1 行）：`trace-order-1001` 收到创建订单请求 `userId=1001 skuId=SKU-9`，确认故障请求入口。
2. **日志 #2**（原始第 2 行）：同一 traceId 记录 `user service returned empty profile userId=1001`，证明上游用户服务/客户端已返回空结果，且系统已感知到该状态但未中断流程。
3. **日志 #3**（原始第 3-6 行）：抛出 `java.lang.NullPointerException: Cannot invoke "UserProfile.getLevel()" because "profile" is null`，堆栈指向 `OrderService.createOrder(OrderService.java:24)`，确认 NPE 发生位置。
4. **源码 `OrderService.java:21-24`**：`UserProfile profile = userClient.findById(userId);` 后直接调用 `profile.getLevel();`，无任何空值判断，与异常消息完全吻合。
5. **源码 `UserClient.java:8-14`**：`findById` 为测试桩实现，当 `userId == 1001` 时显式 `return null;`，解释了为何该用户必然触发 NPE；其他用户返回非空 `UserProfile`。
6. **日志 #4**（原始第 7 行）：同一 traceId 返回 `response status=500 userId=1001`，确认故障最终表现为接口 500。

### 根因分析

- **触发条件**：用户 1001 发起创建订单请求；`UserClient.findById(1001)` 在当前测试桩实现下返回 `null`（`UserClient.java:10-11`）。
- **直接原因**：`OrderService.java:24` 对可能为 `null` 的 `profile` 直接调用方法，导致 NPE。
- **根本原因**：`createOrder` 方法缺少对 `userClient.findById` 返回值的空值校验与业务语义处理；同时日志 #2 表明系统已检测到“empty profile”但未将其转化为明确的业务异常或降级逻辑，属于防御性编程缺失。
- **候选解释排除**：异常链仅一层 NPE，无超时、连接异常或数据库约束错误；`validateRequest`（`OrderService.java:29-33`）只校验入参非空，不涉及用户存在性，因此不是参数校验问题。若生产环境 `UserClient` 实现不同，仍需保留空值防御，因为远程服务返回空是合理场景。

### 修复建议

**最小可行修复**：在 `OrderService.createOrder` 中，于 `OrderService.java:21` 之后增加 `profile == null` 判断，抛出明确业务异常（如 `IllegalStateException` 或自定义 `UserNotFoundException`），避免 NPE 并让上层返回更合适的状态码（如 400/404 而非 500）。

**防御性改进**：
1. 将日志 #2 对应的检测逻辑与 `createOrder` 的空值校验统一，避免“已记录空结果却继续执行”的不一致路径。
2. 若业务允许默认等级，可在 `profile == null` 时使用默认值并记录 WARN 日志；否则应快速失败。
3. 审查 `UserClient` 契约：若规范要求“用户不存在时抛异常而非返回 null”，则应修改客户端实现并在 `OrderService` 捕获对应异常；当前测试桩返回 null 仅代表一种可能行为，代码应按最宽松契约防御。
4. 在全局异常处理器中将此类业务异常映射为适当 HTTP 状态码，避免内部异常直接暴露为 500。

**风险**：改为快速失败后，原本“静默 NPE → 500”变为“明确业务异常 → 4xx”，前端/调用方需适配新错误码；若选择默认等级降级，需确认下游计费、风控等逻辑对默认值的兼容性。

### 修复代码

以下为基于已查看源码的最小修复示意（`OrderService.java`）：

```java
public Order createOrder(Long userId, String skuId) {
    validateRequest(userId, skuId);

    UserProfile profile = userClient.findById(userId);
    if (profile == null) {
        // 与日志 "user service returned empty profile" 保持一致，转为明确业务异常
        throw new IllegalStateException("User profile not found for userId=" + userId);
    }

    String level = profile.getLevel();
    Order order = new Order(userId, skuId, level);
    return orderRepository.save(order);
}
```

> 注：若项目已有自定义异常类型（如 `UserNotFoundException`），应替换 `IllegalStateException`；全局异常处理器需相应配置以返回 404/400 而非 500。未查看异常处理器源码，此处不给出具体映射代码。

### 验证方案

1. **单元测试**：Mock `userClient.findById(1001L)` 返回 `null`，断言 `createOrder(1001L, "SKU-9")` 抛出预期业务异常且消息包含 `userId=1001`；Mock 返回正常 `UserProfile` 时断言订单正常创建并调用 `orderRepository.save`。
2. **集成测试**：使用当前 `UserClient` 测试桩，向 `/orders` 发送 `userId=1001` 请求，断言响应状态码为 4xx（取决于异常映射）且响应体包含用户不存在提示；发送其他有效 userId 请求，断言返回 200 且订单持久化成功。
3. **日志观察**：复现请求后，确认不再出现 `NullPointerException` 及 `response status=500`；应出现业务异常日志（级别 INFO/WARN 而非 ERROR）及对应的非 500 响应日志。
4. **回归检查**：验证历史正常用户（非 1001）下单流程不受影响；检查订单表中无因本次故障产生的脏数据。

### 尚未确认

- `UserClient` 在生产环境的真实实现是否也允许返回 `null`，还是应抛异常或返回 `Optional`；当前结论基于测试桩代码（`UserClient.java:8-14`）。
- 全局异常处理器对业务异常的 HTTP 状态码映射规则未查看，修复后的实际响应码需结合该配置确认。
- 日志 #2 “user service returned empty profile” 的记录位置未在已查看源码中找到对应语句，可能存在未上传的拦截器/过滤器代码；不影响根因判断，但建议补充该部分源码以完善链路可观测性。