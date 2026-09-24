package com.demo;

import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

@Service
public class StockService {
    private final StringRedisTemplate redisTemplate;

    public StockService(StringRedisTemplate redisTemplate) {
        this.redisTemplate = redisTemplate;
    }

    public boolean checkStock(Long voucherId) {
        String stockKey = "shop:stock:" + voucherId;
        String stockValue = redisTemplate.opsForValue().get(stockKey);
        if (stockValue == null) {
            return false;
        }
        return Integer.parseInt(stockValue) > 0;
    }
}
