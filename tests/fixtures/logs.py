"""Deterministic Spring Boot log input."""
SAMPLE = """2026-09-19 10:00:00.000 INFO [shop,trace-1,span-1] [http-1] demo.Order : start
2026-09-19 10:00:00.050 ERROR [shop,trace-1,span-2] [http-1] demo.Stock : failed
org.springframework.data.redis.RedisConnectionFailureException: Unable to connect
\tat demo.Stock.check(Stock.java:42)
Caused by: io.lettuce.core.RedisConnectionException: Connection refused
\tat io.lettuce.Client.connect(Client.java:10)
2026-09-19 10:00:00.060 WARN [shop,trace-2,span-1] [http-2] demo.Cache : slow query
"""
