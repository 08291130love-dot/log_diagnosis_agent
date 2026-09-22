# Spring Boot 常见故障排查手册

## Redis 连接失败

当日志出现 `RedisConnectionFailureException` 或 `RedisConnectionException` 时，先核对 Redis 地址、端口、认证信息和网络连通性。若配置指向 `127.0.0.1:6379`，需要确认 Redis 是否与应用部署在同一主机或同一容器。业务读取缓存时应根据一致性要求设计降级策略，并为连接异常设置监控告警。

## HikariCP 获取连接超时

出现 `SQLTransientConnectionException` 和 `Connection is not available` 时，应检查数据库可用性、连接池占用、慢 SQL、连接泄漏以及连接池大小。不要只通过无限增大连接池掩盖慢查询或未释放连接的问题。修改参数后应结合数据库最大连接数进行压测。

## 下游调用超时

出现 `TimeoutException` 时，应检查下游服务延迟、客户端连接和读取超时、线程池饱和度以及重试策略。消息消费场景需要保证操作幂等，并限制重试次数，避免故障期间形成重试风暴。
