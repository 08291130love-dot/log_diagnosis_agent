package com.demo;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

@Repository
public class ShopRepository {
    private final JdbcTemplate jdbcTemplate;

    public ShopRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    public Shop findById(Long shopId) {
        String sql = "select id, name, address from shop where id = ?";
        return jdbcTemplate.queryForObject(
            sql,
            (rs, rowNum) -> new Shop(
                rs.getLong("id"),
                rs.getString("name"),
                rs.getString("address")
            ),
            shopId
        );
    }
}
