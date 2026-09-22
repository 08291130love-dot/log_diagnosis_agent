### 诊断结论

用户 1001 创建订单失败的根本原因是 `OrderService.createOrder` 方法在获取用户资料后未做空值校验，直接调用 `profile.getLevel()` 触发了 `NullPointerException`。当前环境中的 `UserClient` 是一个本地测试桩，针对 userId=1001 硬编码返回了 `null`，导致请求最终返回 HTTP 500。置信度：**高**（日志堆栈与源码实现完全吻合）。主要影响：userId=1001 的订单创建请求必然失败，且以非预期的服务端异常形式暴露给调用方。

---

### 证据链

按时间顺序排列，所有记录均属于 traceId `trace-order-1001`：

1. **请求入口**：日志 #1（原始第 1 行）`2026-09-21 09:15:20.101 INFO create order request userId=1001 skuId=SKU-9`。证明用户 1001 发起了创建订单请求。
2. **空值来源**：日志 #2（原始第 2 行）`2026-09-21 09:15:20.126 INFO user service returned empty profile userId=1001`。证明系统已识别到用户服务返回了空资料。
   - **源码印证**：`UserClient.java:10-11` 显示这是一个带 `@Component` 注解的本地类，其 `findById` 方法包含测试桩逻辑：当 `userId == 1001L` 时显式 `return null;`。这解释了为何会返回空结果，且排除了真实网络 RPC 调用的可能性。
3. **异常触发**：日志 #3（原始第 3-6 行）`2026-09-21 09:15:20.132 ERROR create order failed ... java.lang.NullPointerException: Cannot invoke "UserProfile.getLevel()" because "profile" is null at com.demo.OrderService.createOrder(OrderService.java:24)`。
   - **源码印证**：`OrderService.java:21` 接收了 `userClient.findById(userId)` 的返回值；第 22 行注释明确写道“故障点：远程服务可能返回 null，但这里没有做空值校验”；第 24 行 `String level = profile.getLevel();` 直接解引用了该对象。日志堆栈与源码行号完全一致。
4. **故障结果**：日志 #4（原始第 7 行）`2026-09-21 09:15:20.139 ERROR response status=500 userId=1001`。证明 NPE 未被捕获，最终转化为 HTTP 500 响应。

*注：故障知识库调查阶段状态为 skipped（未提供相应数据），本诊断仅基于日志和源码事实。*

---

### 根因分析

| 维度 | 说明 |
| :--- | :--- |
| **触发条件** | `UserClient` 测试桩（`UserClient.java:10-11`）针对 userId=1001 硬编码返回 `null`。 |
| **直接原因** | `OrderService.java:24` 对值为 `null` 的 `profile` 变量调用了 `getLevel()` 方法，抛出 `NullPointerException`。 |
| **根本原因** | `OrderService.createOrder` 方法（`OrderService.java:21-24`）缺乏防御性编程，未对下游依赖（即使是测试桩）的返回值进行空值校验。 |
| **候选解释排除** | 无外部网络超时或数据库故障。源码证实 `UserClient` 是本地内存测试桩，不存在 RPC/HTTP 客户端依赖，因此无需排查网络连通性或远端用户服务宕机问题。 |

---

### 修复建议

#### 最小可行修复（立即止血）
在 `OrderService.java:21` 与 `OrderService.java:24` 之间增加空值拦截。若业务上不允许资料为空的用户下单，应抛出明确的业务异常（如 `IllegalArgumentException`），使框架将其转换为 4xx 错误而非 500。

- **修改位置**：`OrderService.java`，`createOrder` 方法内部。
- **潜在副作用**：原本静默失败或产生脏数据的场景将变为快速失败，需确认前端或调用方是否能正确处理新的异常类型。

#### 防御性改进（长期优化）
1. **替换测试桩**：`UserClient.java` 当前是硬编码测试桩，生产环境需替换为真实的 HTTP/RPC 客户端实现，并统一下游返回契约（例如使用 `Optional<UserProfile>` 或统一抛出异常，避免返回 `null`）。
2. **全局异常处理**：在 `OrderController` 或 Spring Boot 全局 `@ExceptionHandler` 中捕获 `IllegalArgumentException`，映射为标准的业务错误码（如 HTTP 400 + 自定义 JSON 错误体）。

---

### 修复代码

以下代码基于已读取的 `OrderService.java` 源码结构提供，可直接替换原第 21-24 行逻辑：

```java
// OrderService.java 修改示意
public Order createOrder(Long userId, String skuId) {
    validateRequest(userId, skuId);

    UserProfile profile = userClient.findById(userId);
    
    // 新增：防御性空值校验
    if (profile == null) {
        throw new IllegalArgumentException("User profile not found for userId=" + userId);
    }

    String level = profile.getLevel();
    Order order = new Order(userId, skuId, level);
    return orderRepository.save(order);
}
```

---

### 验证方案

| 步骤 | 操作 | 预期结果 |
| :--- | :--- | :--- |
| **1. 单元测试** | Mock `userClient.findById(1001L)` 返回 `null`，调用 `orderService.createOrder(1001L, "SKU-9")`。 | 抛出 `IllegalArgumentException`，异常消息包含 `"userId=1001"`，不抛出 NPE。 |
| **2. 集成测试** | 使用当前 `UserClient` 桩，通过 HTTP 发起 `POST /orders`（userId=1001）。 | 响应状态码由 500 变为 400（或业务约定的 4xx），响应体包含明确的错误提示。 |
| **3. 日志观察** | 重复上述请求，检查 order-service 日志。 | 不再出现 `NullPointerException` 堆栈（原日志 #3 消失）；不再出现 `response status=500`（原日志 #4 消失）。 |
| **4. 回归测试** | 发起请求 userId=1002（或其他非 1001 的值）。 | `UserClient` 桩返回 `NORMAL` level，订单创建成功，原有正常流程不受影响。 |

---

### 尚未确认

1. **业务规则**：用户资料为空时，业务上是应该直接拒绝下单（当前修复方案的做法），还是允许使用默认等级（如 "DEFAULT"）继续下单？需产品经理确认。
2. **生产环境实现**：`UserClient` 在生产环境中是否会被替换为真实的远程调用？如果是，真实服务在用户不存在时的返回契约是什么（返回 null、空对象还是抛异常）？
3. **全局异常映射**：应用是否已配置 `@RestControllerAdvice` 将 `IllegalArgumentException` 自动转换为 4xx HTTP 响应？若无，抛出该异常仍可能导致 500，需补充异常处理器。