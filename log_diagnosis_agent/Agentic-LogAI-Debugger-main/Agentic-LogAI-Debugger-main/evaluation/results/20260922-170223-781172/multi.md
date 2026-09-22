### 诊断结论

用户 1001 创建订单失败的首要根因是 `OrderService.createOrder` 方法在调用下游 user service 获取用户资料后，未对返回值进行空值校验，直接解引用导致 `NullPointerException`。该故障置信度为**高**（日志与源码完全吻合）。影响结果是本次请求返回 HTTP 500，订单创建中断；同时暴露出 order-service 缺乏对下游服务异常返回的防御性编程缺陷。

---

### 证据链

按时间顺序和因果链排列：

1. **请求入口**：`2026-09-21 09:15:20.101`，用户 1001 发起创建订单请求（skuId=SKU-9），traceId 为 `trace-order-1001`。
   - 证据：日志 #1（原始第 1 行）`create order request userId=1001 skuId=SKU-9`。
2. **触发条件（下游返回空）**：`2026-09-21 09:15:20.126`，user service 对用户 1001 返回了空的 profile。
   - 证据：日志 #2（原始第 2 行）`user service returned empty profile userId=1001`。
3. **直接原因（NPE 抛出）**：`2026-09-21 09:15:20.132`，order-service 抛出空指针异常，堆栈明确指向 `OrderService.java:24`。
   - 证据：日志 #3（原始第 3-6 行）`java.lang.NullPointerException: Cannot invoke "UserProfile.getLevel()" because "profile" is null at com.demo.OrderService.createOrder(OrderService.java:24)`。
4. **源码印证**：`OrderService.java:21` 通过 `userClient.findById(userId)` 获取 profile，第 22 行注释明确指出“远程服务可能返回 null，但这里没有做空值校验”，第 24 行直接执行 `profile.getLevel()`。
   - 证据：源码 `OrderService.java:21-24`。
5. **影响结果**：`2026-09-21 09:15:20.139`，请求最终返回 HTTP 500。
   - 证据：日志 #4（原始第 7 行）`response status=500 userId=1001`。

*注：故障知识库调查阶段状态为 skipped（未提供相应数据），本诊断仅基于当前日志与源码得出。*

---

### 根因分析

- **触发条件**：user service 针对 userId=1001 返回了空的 profile（具体原因如数据缺失、查询条件错误或服务异常，需下游排查，当前证据无法确认）。
- **直接原因**：`OrderService.java:24` 处 `profile.getLevel()` 发生 `NullPointerException`，因为此时 `profile` 变量为 `null`。
- **根本原因**：`OrderService.createOrder` 方法（`OrderService.java:21-24`）缺乏防御性编程。虽然第 29-32 行的 `validateRequest` 方法对入参进行了非空校验，但对依赖的外部 RPC/HTTP 调用返回值（`userClient.findById`）未做任何空值保护，导致外部服务异常直接转化为内部 NPE 并引发 HTTP 500。

---

### 修复建议

**最小可行修复（order-service 侧容错）**：
在 `OrderService.java:21` 与 `24` 之间增加空值校验。若 `profile` 为 `null`，应提前终止流程并抛出明确的业务异常，避免抛出 NPE 导致 500 错误。

**防御性改进**：
1. **全局异常处理**：由于 `OrderController.java` 源码未提供（工具返回未找到），需确认项目中是否配置了全局异常处理器（如 `@RestControllerAdvice`），确保新抛出的业务异常能被正确转换为合理的 HTTP 状态码（如 400 或 404）及标准错误响应体。
2. **下游排查**：联系 user service 团队排查为何对有效用户 1001 返回空 profile，解决数据源问题。

**涉及文件与风险**：
- 修改位置：`OrderService.java` 的 `createOrder` 方法。
- 风险：引入新的业务异常类需确保上层 Controller 或全局拦截器能正确处理，否则可能只是将 500 错误换成了另一种未处理的异常。

---

### 修复代码

基于已查看的 `OrderService.java` 源码结构，提供以下最小修复片段。请将 `BusinessException` 替换为项目中实际使用的自定义业务异常类（如 `IllegalArgumentException` 或项目统一定义的错误码异常）。

```java
// OrderService.java 修改示意（第 21-24 行附近）
public Order createOrder(Long userId, String skuId) {
    validateRequest(userId, skuId);

    UserProfile profile = userClient.findById(userId);
    
    // 【新增】防御性空值校验
    if (profile == null) {
        throw new IllegalArgumentException("User profile not found for userId: " + userId);
    }

    String level = profile.getLevel();
    Order order = new Order(userId, skuId, level);
    return orderRepository.save(order);
}
```

---

### 验证方案

1. **单元测试**：
   - 场景：Mock `userClient.findById(1001L)` 返回 `null`，调用 `createOrder(1001L, "SKU-9")`。
   - 预期结果：抛出 `IllegalArgumentException`（或指定的业务异常），且异常消息包含 "User profile not found"，不再抛出 `NullPointerException`。
2. **集成测试**：
   - 场景：模拟 user service 对特定用户返回空响应，通过 HTTP 接口发起创建订单请求。
   - 预期结果：接口返回明确的业务错误码（如 HTTP 400 或 404，取决于全局异常处理器的配置），而非 HTTP 500。
3. **日志观察**：
   - 预期结果：修复后，相同场景下日志中不再出现 `java.lang.NullPointerException` 堆栈，且不应再记录 `response status=500`。
4. **回归检查**：
   - 场景：使用正常存在的用户（user service 能返回有效 profile）发起创建订单请求。
   - 预期结果：订单创建成功，流程不受新增空值校验的影响。

---

### 尚未确认

1. **`OrderController.java` 源码缺失**：日志堆栈显示入口为 `OrderController.java:31`，但源码库中未找到该文件。无法确认控制器层是否已有全局异常处理机制来优雅地转换新抛出的业务异常。
2. **user service 返回空 profile 的原因**：日志 #2 证明 user service 返回了空数据，但这是由用户 1001 数据不存在、查询逻辑 Bug 还是 user service 自身故障引起的，当前 order-service 侧证据无法判定，需下游团队配合排查。
3. **故障知识库未启用**：知识库调查阶段被跳过（skipped），未能参考历史类似故障的处理经验或运维手册。