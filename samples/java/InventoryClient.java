package com.demo;

import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

@Component
public class InventoryClient {
    private final RestClient restClient;

    public InventoryClient(RestClient.Builder builder) {
        this.restClient = builder.baseUrl("http://inventory-service").build();
    }

    public void update(Long orderId) {
        restClient.post()
            .uri("/api/inventory/{orderId}", orderId)
            .retrieve()
            .toBodilessEntity();
    }
}
